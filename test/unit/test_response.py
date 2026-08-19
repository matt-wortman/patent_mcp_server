"""Unit tests for response normalization and truncation utilities."""
import json
import os
import tempfile

import pytest

from patent_mcp_server.config import config
from patent_mcp_server.util.response import (
    ResponseEnvelope,
    check_and_truncate,
    estimate_tokens,
    truncate_response,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def tmp_download_dir(monkeypatch, tmp_path):
    """Point config.DOWNLOAD_DIR at a pytest-managed tmp_path for the test.

    Ensures the disk-save branches don't leak files into the real default
    directory and lets us introspect saved files.
    """
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path))
    return tmp_path


# ----------------------------------------------------------------------
# Happy path — under budget
# ----------------------------------------------------------------------

@pytest.mark.unit
def test_under_budget_passthrough():
    """Responses under the token budget are returned unchanged."""
    response = {
        "success": True,
        "source": "ppubs",
        "count": 1,
        "results": [{"patent": "US1234567"}],
    }
    out = check_and_truncate(response, max_tokens=10_000)
    assert out is response  # untouched


@pytest.mark.unit
def test_estimate_tokens_nonempty():
    assert estimate_tokens({"k": "v"}) > 0
    assert estimate_tokens(None) == 0


# ----------------------------------------------------------------------
# List truncation — regression guard
# ----------------------------------------------------------------------

@pytest.mark.unit
def test_list_truncation_happy_path(tmp_download_dir):
    """A large list of small records should get sliced to max_results."""
    # Build 100 small records; total comfortably over 100 tokens but each
    # record is tiny, so slicing alone should drop us under budget.
    results = [{"i": i, "title": f"record {i}"} for i in range(100)]
    response = {
        "success": True,
        "source": "ppubs",
        "count": 100,
        "results": results,
    }
    # Use a generous max_results and tight token budget so slicing triggers
    out = truncate_response(response, max_tokens=200, max_results=5)

    assert out.get("_truncated") is True
    assert out["_original_count"] == 100
    assert out["_truncated_to"] == 5
    assert len(out["results"]) <= 5
    # should NOT have been forced to disk save
    assert out.get("_oversized") is not True


# ----------------------------------------------------------------------
# Dict-case truncation — saves to disk
# ----------------------------------------------------------------------

@pytest.mark.unit
def test_dict_case_saves_to_disk(tmp_download_dir):
    """Single-record dict responses over budget are written to disk with a summary."""
    # Create a big dict payload — fill it with enough text to exceed budget.
    big_blob = {f"field_{i}": "x" * 500 for i in range(50)}  # ~25KB
    response = {
        "success": True,
        "source": "ppubs",
        "count": 1,
        "results": big_blob,
    }

    out = truncate_response(response, max_tokens=100)

    assert out.get("_oversized") is True
    assert isinstance(out["results"], dict)
    summary = out["results"]
    assert summary.get("_oversized") is True
    assert summary.get("file_path")
    assert os.path.isfile(summary["file_path"])
    # The on-disk file should parse as JSON containing the original data
    with open(summary["file_path"], "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert "results" in saved and isinstance(saved["results"], dict)
    assert "preview_keys" in summary and len(summary["preview_keys"]) > 0


# ----------------------------------------------------------------------
# List-still-oversized case — saves to disk
# ----------------------------------------------------------------------

@pytest.mark.unit
def test_list_still_oversized_saves_to_disk(tmp_download_dir):
    """When each list item is huge, slicing isn't enough — fall back to disk."""
    huge_items = [
        {"id": i, "blob": "y" * 5000}  # each record ~5KB
        for i in range(10)
    ]
    response = {
        "success": True,
        "source": "dsapi",
        "count": 10,
        "results": huge_items,
    }

    # max_results=5 still leaves ~25KB of content, over a tight budget
    out = truncate_response(response, max_tokens=200, max_results=5)

    assert out.get("_oversized") is True
    summary = out["results"]
    assert isinstance(summary, dict)
    assert summary.get("_oversized") is True
    assert summary.get("file_path")
    assert os.path.isfile(summary["file_path"])

    # The file must hold the COMPLETE original payload — not the sliced copy.
    # (Regression: the summary says "full payload saved to disk", so losing
    # records here is silent data loss.)
    with open(summary["file_path"], encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["results"] == huge_items
    assert len(saved["results"]) == 10


# ----------------------------------------------------------------------
# check_and_truncate respects config.TRUNCATE_LARGE_RESPONSES
# ----------------------------------------------------------------------

@pytest.mark.unit
def test_check_and_truncate_can_be_disabled(monkeypatch, tmp_download_dir):
    """When TRUNCATE_LARGE_RESPONSES=False, check_and_truncate is a no-op."""
    monkeypatch.setattr(config, "TRUNCATE_LARGE_RESPONSES", False)
    response = {"results": {"big": "x" * 10_000}, "source": "odp"}
    out = check_and_truncate(response, max_tokens=10)
    assert out is response
