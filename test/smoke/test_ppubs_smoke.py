"""
Smoke tests for ppubs.uspto.gov tools.

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.
"""

import os
import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import (
    ppubs_search_patents,
    ppubs_search_applications,
    ppubs_get_patent_by_number,
    ppubs_download_patent_pdf,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


async def test_ppubs_search_patents_returns_results():
    """ppubs_search_patents returns a normalized success envelope with results."""
    result = await ppubs_search_patents("machine learning", limit=1)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, "Expected at least one result"


async def test_ppubs_search_applications_returns_results():
    """ppubs_search_applications returns a normalized success envelope with results."""
    result = await ppubs_search_applications("neural network", limit=1)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, "Expected at least one result"


async def test_ppubs_get_patent_by_number_returns_document():
    """ppubs_get_patent_by_number returns a document dict with a non-empty inventionTitle."""
    result = await ppubs_get_patent_by_number("10000000")

    # The tool returns the raw document dict, not a ResponseEnvelope — no "success" key.
    assert not result.get("error"), f"Expected no error, got: {result}"
    title = result.get("inventionTitle")
    assert isinstance(title, str) and len(title) > 0, (
        f"Expected non-empty inventionTitle string, got: {title!r}"
    )


async def test_ppubs_download_patent_pdf_returns_pdf():
    """ppubs_download_patent_pdf saves a PDF to disk and returns the file path."""
    import os

    result = await ppubs_download_patent_pdf("10000000")
    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result.get("content_type") == "application/pdf"
    file_path = result.get("file_path")
    assert file_path and os.path.isfile(file_path), (
        f"Expected file_path to exist on disk, got: {file_path}"
    )
    assert os.path.getsize(file_path) > 1000, "Expected non-trivial PDF on disk"
