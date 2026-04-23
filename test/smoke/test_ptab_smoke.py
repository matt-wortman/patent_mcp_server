"""
Smoke tests for PTAB tools (api.uspto.gov/api/v1/patent/trials).

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.
"""

import os
import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import (
    ptab_search_proceedings,
    ptab_get_proceeding,
    ptab_search_decisions,
    ptab_get_decision,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

PATENT_NUM = "10000000"
PROCEEDING_NUM = "IPR2026-00338"


def _first_decision_id(result):
    """Extract a decision/document identifier from ptab_search_decisions output."""
    rows = result.get("results", [])
    if not rows:
        return None
    return rows[0].get("documentData", {}).get("documentIdentifier")


async def test_ptab_search_proceedings_returns_results():
    """ptab_search_proceedings by known patent number returns success with results."""
    result = await ptab_search_proceedings(patent_number=PATENT_NUM, limit=1)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, "Expected at least one proceeding"


async def test_ptab_get_proceeding_no_error():
    """ptab_get_proceeding returns a dict without an error flag."""
    result = await ptab_get_proceeding(PROCEEDING_NUM)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"


async def test_ptab_search_decisions_returns_results():
    """ptab_search_decisions by patent number returns at least one live decision."""
    result = await ptab_search_decisions(patent_number=PATENT_NUM, limit=1)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, "Expected at least one decision"


async def test_ptab_get_decision_no_error():
    """ptab_get_decision returns a dict without an error flag."""
    search_result = await ptab_search_decisions(patent_number=PATENT_NUM, limit=1)

    assert search_result.get("success") is True, f"Expected success, got: {search_result}"
    decision_id = _first_decision_id(search_result)
    assert decision_id, f"Expected a decision identifier in ptab_search_decisions payload, got: {search_result}"

    result = await ptab_get_decision(decision_id)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
