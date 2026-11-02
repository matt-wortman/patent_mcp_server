"""Unit tests for response normalization and truncation utilities."""
import json
import os
import tempfile
from types import SimpleNamespace

import pydantic_core
import pytest
from mcp.server.lowlevel.server import request_ctx

import patent_mcp_server.util.response as response_mod
from patent_mcp_server.config import claude_code_max_response_chars, config
from patent_mcp_server.util.response import (
    ResponseEnvelope,
    check_and_truncate,
    claims_html_to_text,
    claims_only_document,
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
    # The preview rows stay inline; results are not replaced by a summary
    assert out.get("_oversized") is not True


@pytest.mark.unit
def test_list_truncation_saves_every_row_to_disk(tmp_download_dir):
    """Rows cut from the inline preview must be saved, not dropped.

    A 100-row page cut to its first rows kept has_more, offset and limit
    describing the whole page, so a caller paging on to offset 100 never
    saw the rows in between. The same bug was found live in the EPO server
    (2026-10-20), which copied this code.
    """
    rows = [{"i": i, "title": f"record {i}"} for i in range(100)]
    response = ResponseEnvelope.success(
        results=rows, source="ppubs", total=536, offset=0, limit=100,
    )

    out = truncate_response(response, max_tokens=200, max_results=5)

    assert out["results"] == rows[:5]
    file_path = out["_oversized_file"]
    with open(file_path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["results"] == rows
    assert file_path in out["_truncation_message"]
    assert "offset 100" in out["_truncation_message"]


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


@pytest.mark.unit
def test_disk_save_keeps_real_record_count(tmp_download_dir):
    """The envelope count describes the records on disk, not the summary.

    Regression: count was overwritten to 1, so a 10-record search saved to
    disk reported "count: 1".
    """
    huge_items = [{"id": i, "blob": "y" * 5000} for i in range(10)]
    response = ResponseEnvelope.success(
        results=huge_items, source="ppubs", total=495, limit=5,
    )

    out = truncate_response(response, max_tokens=200, max_results=5)

    assert out.get("_oversized") is True
    assert out["count"] == 10
    assert out["results"]["record_count"] == 10


@pytest.mark.unit
def test_disk_save_single_record_count(tmp_download_dir):
    """A single oversized record reports one record, in envelope and summary."""
    response = ResponseEnvelope.success(
        results={f"field_{i}": "x" * 500 for i in range(50)}, source="odp",
    )

    out = truncate_response(response, max_tokens=100)

    assert out["count"] == 1
    assert out["results"]["record_count"] == 1


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


# ----------------------------------------------------------------------
# Claims-only documents
# ----------------------------------------------------------------------

# Shape copied from the claimsHtml of US 12,714,740 B2 as PPUBS returns it.
CLAIMS_HTML = (
    "<b>1</b>. A method of treating a glioblastoma, comprising a TNF receptor "
    "(“TWEAKR”) &amp; a plasmid.  <br /> <b>2</b>. The method of "
    "claim 1, wherein\n the nanoparticle comprises two ligands. <br /> "
    "<b>12</b>. The method of claim 1, further comprising irradiation."
)


@pytest.mark.unit
def test_claims_html_to_text_puts_one_claim_per_line():
    assert claims_html_to_text(CLAIMS_HTML).split("\n") == [
        "1. A method of treating a glioblastoma, comprising a TNF receptor "
        "(“TWEAKR”) & a plasmid.",
        "2. The method of claim 1, wherein the nanoparticle comprises two ligands.",
        "12. The method of claim 1, further comprising irradiation.",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("empty", [None, "", "  <br />  <br/> "])
def test_claims_html_to_text_handles_missing_claims(empty):
    assert claims_html_to_text(empty) == ""


@pytest.mark.unit
def test_claims_only_document_keeps_identity_and_claims_only():
    document = {
        "guid": "US-12714740-B2",
        "publicationReferenceDocumentNumber": "12714740",
        "inventionTitle": "Compositions comprising a tweak ligand",
        "datePublished": "2026-08-25T00:00:00Z",
        "applicationNumber": "17/604557",
        "assigneeName": ["Children's Hospital Medical Center"],
        "inventorsName": ["Yurek; David"],
        "numberOfClaims": "12",
        "exemplaryClaimNumber": ["1"],
        "claimsHtml": CLAIMS_HTML,
        "descriptionHtml": "x" * 50_000,
        "otherRefPub": ["y" * 60_000],
    }
    reduced = claims_only_document(document)
    assert set(reduced) == {
        "guid", "publicationReferenceDocumentNumber", "inventionTitle",
        "datePublished", "applicationNumber", "assigneeName", "inventorsName",
        "numberOfClaims", "exemplaryClaimNumber", "claims",
    }
    assert reduced["claims"].startswith("1. A method of treating a glioblastoma")
    assert reduced["assigneeName"] == ["Children's Hospital Medical Center"]


# ----------------------------------------------------------------------
# Complete flat records are never flagged as saved to disk
# ----------------------------------------------------------------------

def _flat_patent_record():
    """A PPUBS document reply: one flat record, no `results` envelope."""
    return {
        "guid": "US-6237565-B1",
        "inventionTitle": "Example",
        "claimsHtml": "<b>1</b>. A thing. <br />",
        "descriptionHtml": "d" * 60_000,  # ~15,000 estimated tokens
    }


@pytest.mark.unit
@pytest.mark.parametrize("client_name", [None, "mcp", "claude-code"])
def test_flat_record_over_limit_is_returned_unchanged(
    monkeypatch, tmp_download_dir, client_name
):
    """A flat record already holds every field inline, so it is returned as is.

    Regression: the no-envelope branch copied the record, kept every field
    inline, and added a `results` summary claiming the payload had been saved
    to disk because it was too big. The notice was false (US 6,237,565 probe).
    """
    monkeypatch.setattr(response_mod, "_current_client_name", lambda: client_name)
    record = _flat_patent_record()
    original = json.loads(json.dumps(record))

    for out in (check_and_truncate(record), truncate_response(record, max_tokens=100)):
        assert out == original
        assert "_oversized" not in out
        assert "_oversized_file" not in out
        assert "results" not in out
    assert os.listdir(tmp_download_dir) == []


# ----------------------------------------------------------------------
# Client-aware inline limit
# ----------------------------------------------------------------------

def _hundred_row_page():
    """100 rows, ~10,000 estimated tokens: over 8,000, far under 450,000 chars."""
    rows = [{"i": i, "title": f"record {i} " + "t" * 380} for i in range(100)]
    return ResponseEnvelope.success(
        results=rows, source="ppubs", total=536, offset=0, limit=100,
    )


@pytest.mark.unit
@pytest.mark.parametrize("client_name", [None, "mcp", "codex-mcp-client"])
def test_other_clients_keep_the_token_limit(monkeypatch, tmp_download_dir, client_name):
    """Any client other than Claude Code (or none) is cut to 20 rows, as before."""
    monkeypatch.setattr(response_mod, "_current_client_name", lambda: client_name)
    response = _hundred_row_page()
    assert estimate_tokens(response) > config.MAX_RESPONSE_TOKENS

    out = check_and_truncate(response)

    assert out["count"] == 20
    assert len(out["results"]) == 20
    assert out["_truncated"] is True
    assert "_truncation_message" in out
    with open(out["_oversized_file"], encoding="utf-8") as f:
        assert len(json.load(f)["results"]) == 100


@pytest.mark.unit
def test_no_request_context_keeps_the_token_limit(tmp_download_dir):
    """Outside an MCP request there is no client name: today's rule applies."""
    out = check_and_truncate(_hundred_row_page())
    assert out["count"] == 20
    assert out["_truncated"] is True


@pytest.mark.unit
@pytest.mark.parametrize("client_name", ["claude-code", "Claude-Code"])
def test_claude_code_gets_the_whole_page(monkeypatch, tmp_download_dir, client_name):
    """Claude Code receives all 100 rows when the sent text fits its limit."""
    monkeypatch.setattr(response_mod, "_current_client_name", lambda: client_name)
    response = _hundred_row_page()

    out = check_and_truncate(response)

    assert out is response
    assert out["count"] == 100
    assert len(out["results"]) == 100
    assert "_truncated" not in out
    assert os.listdir(tmp_download_dir) == []


@pytest.mark.unit
def test_claude_code_limit_measures_the_indented_text(monkeypatch, tmp_download_dir):
    """The size checked is the indented JSON FastMCP sends, not compact JSON."""
    monkeypatch.setattr(response_mod, "_current_client_name", lambda: "claude-code")
    monkeypatch.setattr(config, "CLAUDE_CODE_MAX_RESPONSE_CHARS", 20_000)
    response = ResponseEnvelope.success(
        results={"rows": [[i, i] for i in range(1_000)]}, source="odp",
    )
    compact = len(json.dumps(response, default=str))
    sent = len(pydantic_core.to_json(response, fallback=str, indent=2).decode())
    assert compact < 20_000 < sent  # the case this test exists for

    out = check_and_truncate(response)

    assert out["_oversized"] is True
    assert os.path.isfile(out["_oversized_file"])
    with open(out["_oversized_file"], encoding="utf-8") as f:
        assert json.load(f)["results"] == response["results"]


@pytest.mark.unit
def test_explicit_max_tokens_uses_the_token_rule_for_claude_code(
    monkeypatch, tmp_download_dir
):
    """A caller's explicit token limit wins over the client-aware limit."""
    monkeypatch.setattr(response_mod, "_current_client_name", lambda: "claude-code")
    out = truncate_response(_hundred_row_page(), max_tokens=2_000, max_results=5)
    assert out["_truncated"] is True
    assert out["count"] == 5


@pytest.mark.unit
def test_client_name_read_from_request_context():
    """The name comes from the initialize clientInfo of the current session."""
    assert response_mod._current_client_name() is None  # no request

    info = SimpleNamespace(clientInfo=SimpleNamespace(name="claude-code"))
    token = request_ctx.set(SimpleNamespace(session=SimpleNamespace(client_params=info)))
    try:
        assert response_mod._current_client_name() == "claude-code"
    finally:
        request_ctx.reset(token)

    token = request_ctx.set(SimpleNamespace(session=SimpleNamespace(client_params=None)))
    try:
        assert response_mod._current_client_name() is None
    finally:
        request_ctx.reset(token)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw, expected",
    [(None, 450_000), ("300000", 300_000), ("490000", 490_000), ("2000000", 490_000)],
)
def test_claude_code_char_limit_is_clamped(raw, expected):
    """The setting defaults to 450,000 and never exceeds 490,000."""
    assert claude_code_max_response_chars(raw) == expected


@pytest.mark.unit
def test_scalar_results_over_limit_still_moves_to_disk(tmp_download_dir):
    """A `results` string over the limit is replaced by the summary, truthfully."""
    response = {"success": True, "source": "odp", "results": "s" * 50_000}
    out = check_and_truncate(response)
    assert out["_oversized"] is True
    assert out["results"]["_shape"] == "str"
    with open(out["_oversized_file"], encoding="utf-8") as f:
        assert json.load(f)["results"] == "s" * 50_000
