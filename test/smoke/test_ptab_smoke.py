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
    ptab_search_trial_documents,
    ptab_get_proceeding_documents,
    ptab_search_appeal_decisions,
    ptab_get_appeal_decisions,
    ptab_search_interference_decisions,
    ptab_get_interference_decisions,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

# Patent 11570034 has a real PTAB record (IPR2025-00913: 1 proceeding,
# 3 decisions as of 2026-08-27); 10000000 has none — it only "worked"
# while the old client sent filters the API silently ignored.
PATENT_NUM = "11570034"
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


# --- Trial documents, appeals, interferences (added v0.12.0) ------------

DOCUMENTED_PROCEEDING = "IPR2023-00037"  # 88 documents as of 2026-08-19
APPEAL_NUM = "2026002664"                # decided ex parte appeal (reexam 90019821)
APPEAL_APP_NUM = "90019821"
INTERFERENCE_NUM = "106130"              # decided interference, 2 decisions


async def test_ptab_search_trial_documents_by_proceeding():
    """ptab_search_trial_documents filtered by trial number returns results."""
    result = await ptab_search_trial_documents(
        proceeding_number=DOCUMENTED_PROCEEDING, limit=2
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, "Expected at least one document"
    assert result["results"][0].get("trialNumber") == DOCUMENTED_PROCEEDING


async def test_ptab_get_proceeding_documents_returns_results():
    """ptab_get_proceeding_documents lists the documents of one trial."""
    result = await ptab_get_proceeding_documents(DOCUMENTED_PROCEEDING)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result.get("total", 0) >= 1, "Expected at least one document"


async def test_ptab_search_appeal_decisions_by_app_num():
    """ptab_search_appeal_decisions filtered by application number works."""
    result = await ptab_search_appeal_decisions(app_num=APPEAL_APP_NUM, limit=2)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, "Expected at least one appeal decision"


async def test_ptab_get_appeal_decisions_no_error():
    """ptab_get_appeal_decisions returns decisions for a known appeal."""
    result = await ptab_get_appeal_decisions(APPEAL_NUM)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result.get("total", 0) >= 1, "Expected at least one decision"


async def test_ptab_search_interference_decisions_by_number():
    """ptab_search_interference_decisions filtered by interference number works."""
    result = await ptab_search_interference_decisions(
        interference_number=INTERFERENCE_NUM, limit=2
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, "Expected at least one decision"


async def test_ptab_get_interference_decisions_no_error():
    """ptab_get_interference_decisions returns decisions for a known interference."""
    result = await ptab_get_interference_decisions(INTERFERENCE_NUM)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result.get("total", 0) >= 1, "Expected at least one decision"
