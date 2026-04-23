"""
Smoke tests for DSAPI tools (api.uspto.gov/api/v1/patent/oa).

DSAPI returns raw Solr-style responses:
  {"response": {"start": N, "numFound": N, "docs": [...]}}

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.
"""

import os
import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import (
    dsapi_list_status_codes,
    dsapi_lookup_status_code,
    get_status_code,
    dsapi_search_oa_citations,
    dsapi_search_office_actions,
    dsapi_search_rejections,
    dsapi_search_enriched_citations,
    dsapi_search_litigation,
    dsapi_get_patent_litigation,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

APP_NUM = "14412875"


async def test_dsapi_list_status_codes_no_error():
    """dsapi_list_status_codes returns a dict without error and has a response key."""
    result = await dsapi_list_status_codes(rows=5)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    # DSAPI Solr format: {"response": {"numFound": N, "docs": [...]}}
    assert "response" in result, (
        f"Expected Solr 'response' envelope key, got keys: {list(result.keys())}"
    )


async def test_dsapi_lookup_status_code_finds_record():
    """dsapi_lookup_status_code returns at least one doc for a known status code."""
    result = await dsapi_lookup_status_code("150")

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert "response" in result, (
        f"Expected Solr 'response' envelope key, got keys: {list(result.keys())}"
    )
    docs = result["response"].get("docs", [])
    assert len(docs) >= 1, "Expected at least one status code doc for code 150"


async def test_get_status_code_returns_normalized_record():
    """get_status_code returns the normalized helper payload without error."""
    result = await get_status_code("150")

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert result.get("code") == "150", f"Expected code='150', got: {result}"
    assert result.get("description"), f"Expected non-empty description, got: {result}"


async def test_dsapi_search_oa_citations_no_error():
    """dsapi_search_oa_citations returns at least one citation row for fixture app 14412875."""
    result = await dsapi_search_oa_citations(APP_NUM, rows=1)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    docs = result["response"].get("docs", [])
    assert len(docs) >= 1, "Expected at least one office action citation row"


async def test_dsapi_search_office_actions_no_error():
    """dsapi_search_office_actions returns without error for known fixture app 14412875."""
    # 14412875 is referenced throughout the repo as a known test fixture application.
    result = await dsapi_search_office_actions(APP_NUM, rows=1)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"


async def test_dsapi_search_rejections_no_error():
    """dsapi_search_rejections returns at least one row for fixture app 14412875."""
    result = await dsapi_search_rejections(APP_NUM, rows=1)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    docs = result["response"].get("docs", [])
    assert len(docs) >= 1, "Expected at least one rejection row"


async def test_dsapi_search_enriched_citations_no_error():
    """dsapi_search_enriched_citations returns at least one row for fixture app 14412875."""
    result = await dsapi_search_enriched_citations(APP_NUM, rows=1)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    docs = result["response"].get("docs", [])
    assert len(docs) >= 1, "Expected at least one enriched citation row"


async def test_dsapi_search_litigation_no_error():
    """dsapi_search_litigation returns at least one row for a broad Apple query."""
    result = await dsapi_search_litigation('case_name:"Apple"', rows=1)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    docs = result["response"].get("docs", [])
    assert len(docs) >= 1, "Expected at least one litigation row"


async def test_dsapi_get_patent_litigation_no_error():
    """dsapi_get_patent_litigation returns a Solr envelope even when the heuristic finds no hits."""
    result = await dsapi_get_patent_litigation("7654321", rows=1)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert "response" in result, (
        f"Expected Solr 'response' envelope key, got keys: {list(result.keys())}"
    )
