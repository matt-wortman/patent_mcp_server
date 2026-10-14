"""
Smoke tests for looser-name checks on name searches (ODP and PTAB).

A name search that finds nothing, or an exact name with an apostrophe,
can miss records stored under another form of the name. The search tools
then also search looser forms and report them in name_variants. Cases are
from live searches on 2026-10-14; counts are lower bounds so they hold as
USPTO adds records.

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.
"""

import os

import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import odp_search_applications, ptab_search_proceedings

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


def _variant(reply, form):
    """The name_variants entry with this form label."""
    for entry in reply.get("name_variants", []):
        if entry["form"] == form:
            return entry
    raise AssertionError(f"no {form!r} in {reply.get('name_variants')}")


async def test_abbreviated_applicant_finds_full_name_as_prefix():
    reply = await odp_search_applications(assignee_name="Raytheon Co", limit=1)
    assert reply["total"] == 0
    prefix = _variant(reply, "every word as a prefix")
    assert prefix["total"] >= 9000
    assert "Raytheon Company" in prefix["names_found"]


async def test_inventor_with_words_reversed_is_found_in_any_order():
    reply = await odp_search_applications(inventor_name="Jodele Sonata", limit=1)
    assert reply["total"] == 0
    any_order = _variant(reply, "all words, in any order")
    assert any_order["total"] >= 23
    assert "Sonata Jodele" in any_order["names_found"]


async def test_misspelled_word_is_found_by_leaving_it_out():
    reply = await odp_search_applications(
        assignee_name="Cincinnati Childrens Hospital", limit=1
    )
    assert reply["total"] == 0
    assert _variant(reply, "every word as a prefix")["total"] == 0
    without = _variant(reply, "without Childrens")
    assert without["total"] >= 44
    assert any("Cincinnati Children" in name for name in without["names_found"])


async def test_looser_search_keeps_the_other_inputs():
    """A filing-date range must narrow the looser searches too."""
    everything = await odp_search_applications(assignee_name="Raytheon Co", limit=1)
    recent = await odp_search_applications(
        assignee_name="Raytheon Co", filing_date_from="2025-01-01", limit=1
    )
    all_years = _variant(everything, "every word as a prefix")["total"]
    since_2025 = _variant(recent, "every word as a prefix")["total"]
    assert 0 < since_2025 < all_years


async def test_apostrophe_name_reports_records_stored_under_other_spellings():
    reply = await odp_search_applications(
        assignee_name="Children's Hospital Medical Center", limit=1
    )
    assert reply["total"] >= 998
    prefix = _variant(reply, "every word as a prefix")
    assert prefix["missed_by_exact_name"] >= 20
    assert "CHILDRENS HOSPITAL MEDICAL CENTER" in prefix["names_found"]


async def test_name_found_without_apostrophe_makes_no_extra_searches():
    reply = await odp_search_applications(assignee_name="Raytheon Company", limit=1)
    assert reply["total"] >= 7000
    assert "name_variants" not in reply


async def test_board_party_name_finds_full_name_as_prefix():
    reply = await ptab_search_proceedings(party_name="Samsung Elec", limit=1)
    assert reply["total"] == 0
    prefix = _variant(reply, "every word as a prefix")
    assert prefix["total"] >= 1000
    assert any(name.startswith("Samsung Electronics") for name in prefix["names_found"])


async def test_page_past_the_end_is_not_checked():
    """USPTO reports a page past the last record as zero matches too."""
    reply = await odp_search_applications(
        inventor_name="Sonata Jodele", offset=1000, limit=1
    )
    assert reply["total"] == 0
    assert "name_variants" not in reply
