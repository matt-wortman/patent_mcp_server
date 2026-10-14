"""
Smoke tests for a caller's OR search combined with a tool's own filters.

USPTO reads "A OR B AND C" as "B AND C": the AND binds tighter and the
first half of the OR is dropped. So the caller's search text must be put
in parentheses before a tool joins its filters to it. Live-verified
2026-10-14 before the fix: PGR or CBM trials filed in 2024 -> 0 without
parentheses, 41 with them; Raytheon design or plant applications -> 0
without, 3 with.

Each test runs the same search twice, once with the OR already in
parentheses, and requires the same nonzero total. That holds however the
live counts change over time.

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.
"""

import os

import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import (
    odp_search_applications,
    odp_search_petition_decisions,
    ptab_search_proceedings,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

DESIGN_OR_PLANT = (
    "applicationMetaData.applicationTypeLabelName:Design"
    " OR applicationMetaData.applicationTypeLabelName:Plant"
)


async def _same_total(tool, query, **filters):
    """Run `query` bare and in parentheses; return the shared total."""
    bare = await tool(query=query, limit=1, **filters)
    grouped = await tool(query=f"({query})", limit=1, **filters)
    assert bare.get("error") is not True, bare
    assert grouped.get("error") is not True, grouped
    assert grouped["total"] > 0, grouped
    assert bare["total"] == grouped["total"], (
        f"bare {bare['total']} vs grouped {grouped['total']}; "
        f"sent {bare.get('query_sent')}"
    )
    return grouped["total"]


async def test_application_or_search_with_applicant_name():
    await _same_total(
        odp_search_applications, DESIGN_OR_PLANT, assignee_name="Raytheon Company"
    )


async def test_application_or_search_with_one_sided_filing_date():
    await _same_total(
        odp_search_applications, DESIGN_OR_PLANT, filing_date_from="2025-01-01"
    )


async def test_petition_or_search_with_application_number():
    # 09756788 has denied petition decisions on record (2010).
    await _same_total(
        odp_search_petition_decisions,
        "decidedPetitionTypeCodeDescriptionText:DENIED"
        " OR decidedPetitionTypeCodeDescriptionText:GRANTED",
        app_num="09756788",
    )


async def test_board_or_search_with_filing_dates():
    await _same_total(
        ptab_search_proceedings,
        "trialMetaData.trialTypeCode:PGR OR trialMetaData.trialTypeCode:CBM",
        filing_date_from="2024-01-01",
        filing_date_to="2024-12-31",
    )
