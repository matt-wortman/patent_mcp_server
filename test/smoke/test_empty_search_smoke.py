"""
Smoke tests for searches that match nothing (ODP and PTAB).

USPTO answers a zero-match search with HTTP 404 "Not Found" — the same
reply a lookup gets for a missing record. Search tools must turn that into
an empty result; lookup tools must keep it as an error.

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.
"""

import os

import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import (
    odp_get_application,
    odp_search_applications,
    odp_search_office_actions,
    odp_search_petition_decisions,
    ptab_search_appeal_decisions,
    ptab_search_decisions,
    ptab_search_interference_decisions,
    ptab_search_proceedings,
    ptab_search_trial_documents,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

# A word that appears in no USPTO record (live-verified 2026-10-14: every
# search below returned 404 for it before the fix).
NO_MATCH_QUERY = "zzqxqzzqx"

SEARCH_TOOLS = [
    (odp_search_applications, "odp"),
    (odp_search_petition_decisions, "odp"),
    (odp_search_office_actions, "odp"),
    (ptab_search_proceedings, "ptab"),
    (ptab_search_decisions, "ptab"),
    (ptab_search_trial_documents, "ptab"),
    (ptab_search_appeal_decisions, "ptab"),
    (ptab_search_interference_decisions, "ptab"),
]


@pytest.mark.parametrize(
    "tool,source", SEARCH_TOOLS, ids=[t.__name__ for t, _ in SEARCH_TOOLS]
)
async def test_zero_match_search_returns_empty_result(tool, source):
    result = await tool(query=NO_MATCH_QUERY, limit=2)

    assert result.get("error") is not True, result
    assert result["success"] is True
    assert result["source"] == source
    assert result["results"] == []
    assert result["total"] == 0
    # The reply shows the search exactly as USPTO received it.
    assert result["query_sent"]["q"] == NO_MATCH_QUERY
    assert "No records matched" in result["message"]



# An unbalanced bracket USPTO cannot parse. It answers such a search with
# HTTP 200, a count of 0 and no records list (live-verified 2026-10-14),
# which the tools used to report as 1 fake result (ODP) or a quiet 0 (PTAB).
UNREADABLE_QUERY = f"({NO_MATCH_QUERY}"


@pytest.mark.parametrize(
    "tool,source", SEARCH_TOOLS, ids=[t.__name__ for t, _ in SEARCH_TOOLS]
)
async def test_unreadable_search_is_reported_as_error(tool, source):
    result = await tool(query=UNREADABLE_QUERY, limit=2)

    assert result.get("error") is True, result
    assert result["error_code"] == "UNREADABLE_SEARCH"
    assert result["query_sent"]["q"] == UNREADABLE_QUERY

async def test_zero_match_name_filter_returns_empty_result():
    """An applicant-name search with no matches returns an empty result.

    "Cincinnati Children*" was the original case, but it only matched
    nothing because ODP ignored the wildcard inside quotes. A nonsense
    name keeps this test about the empty result, not the wildcard.
    """
    result = await odp_search_applications(
        assignee_name="Qxzvw Nonexistent Hospital*",
        filing_date_from="2023-01-01",
        limit=2,
    )

    assert result["success"] is True
    assert result["total"] == 0


async def test_lookup_of_missing_application_is_still_an_error():
    """For a lookup, 404 means the record does not exist — keep the error."""
    result = await odp_get_application("99999999")

    assert result.get("error") is True
    assert result.get("status_code") == 404
