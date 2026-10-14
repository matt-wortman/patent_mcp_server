"""Unit tests for ODP office-action and petition search body construction.

Pure internal logic: the search tools must compile their arguments into
the ODP POST body (filters, rangeFilters, q, sort) using field names
live-verified 2026-10-14. ODP rejects a rangeFilters entry with only one
bound, so a one-sided date range must become a q clause. Every request is
captured before it leaves the client, so these tests never touch the
network.
"""

import pytest

import patent_mcp_server.patents as patents


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    async def capture(url, method="GET", data=None):
        seen["url"] = url
        seen["method"] = method
        seen["body"] = data
        return {"count": 0}

    monkeypatch.setattr(patents.api_client, "make_request", capture)
    return seen


# --- odp_search_office_actions -------------------------------------------

async def test_office_actions_default_body_sorts_newest_first(captured):
    await patents.odp_search_office_actions()
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/patent/office-action/search")
    assert captured["body"] == {
        "pagination": {"offset": 0, "limit": 25},
        "sort": [{"field": "submissionDate", "order": "desc"}],
    }


async def test_office_actions_filters_use_live_field_names(captured):
    await patents.odp_search_office_actions(
        app_num="14/643,719",
        patent_number="US 10,123,456",
        document_code="CTNF, CTFR",
        rejection_type="102,103",
        group_art_unit=1631,
    )
    assert captured["body"]["filters"] == [
        {"name": "applicationNumberText", "value": ["14643719"]},
        {"name": "patentNumber", "value": ["10123456"]},
        {"name": "documentCode", "value": ["CTNF", "CTFR"]},
        {"name": "rejectionTypeBag", "value": ["102", "103"]},
        {"name": "groupArtUnitNumber", "value": ["1631"]},
    ]


async def test_office_actions_two_sided_range_uses_range_filters(captured):
    await patents.odp_search_office_actions(
        submission_date_from="2026-01-01", submission_date_to="2026-06-30"
    )
    assert captured["body"]["rangeFilters"] == [{
        "field": "submissionDate",
        "valueFrom": "2026-01-01",
        "valueTo": "2026-06-30",
    }]
    assert "q" not in captured["body"]


async def test_office_actions_one_sided_range_becomes_q_clause(captured):
    await patents.odp_search_office_actions(submission_date_from="2026-10-01")
    assert captured["body"]["q"] == "submissionDate:>=2026-10-01"
    assert "rangeFilters" not in captured["body"]


async def test_office_actions_one_sided_range_parenthesizes_existing_query(captured):
    await patents.odp_search_office_actions(
        query="101 OR 112", submission_date_to="2010-12-31"
    )
    assert captured["body"]["q"] == "(101 OR 112) AND submissionDate:<=2010-12-31"


async def test_office_actions_sort_order_is_case_insensitive(captured):
    await patents.odp_search_office_actions(sort_order="ASC")
    assert captured["body"]["sort"] == [{"field": "submissionDate", "order": "asc"}]


# --- odp_search_petition_decisions ---------------------------------------

async def test_petitions_post_body_with_decision_filter_and_sort(captured):
    await patents.odp_search_petition_decisions(
        app_num="09756788", decision="denied, dismissed", sort_order="asc"
    )
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/petition/decisions/search")
    assert captured["body"] == {
        "pagination": {"offset": 0, "limit": 25},
        "sort": [{"field": "decisionDate", "order": "asc"}],
        "q": "applicationNumberText:09756788",
        "filters": [{
            "name": "decidedPetitionTypeCodeDescriptionText",
            "value": ["DENIED", "DISMISSED"],
        }],
    }


async def test_petitions_two_sided_range_uses_range_filters(captured):
    await patents.odp_search_petition_decisions(
        decision_date_from="2026-01-01", decision_date_to="2026-06-30"
    )
    assert captured["body"]["rangeFilters"] == [{
        "field": "decisionDate",
        "valueFrom": "2026-01-01",
        "valueTo": "2026-06-30",
    }]


async def test_petitions_one_sided_range_becomes_q_clause(captured):
    await patents.odp_search_petition_decisions(decision_date_to="2005-12-31")
    assert captured["body"]["q"] == "decisionDate:<=2005-12-31"


# --- odp_search_applications: name fields --------------------------------
# Live-verified 2026-10-14: ODP ignores "*" inside a quoted phrase
# ("Cincinnati Children*" -> 0), but matches each word separately
# ("(Cincinnati AND Children*)" -> 44). Names without a wildcard stay an
# exact phrase.

APPLICANT = "applicationMetaData.firstApplicantName"
INVENTOR = "applicationMetaData.inventorBag.inventorNameText"


async def test_single_word_name_is_sent_bare(captured):
    await patents.odp_search_applications(assignee_name="Raytheon*")
    assert captured["body"]["q"] == f"{APPLICANT}:Raytheon*"


async def test_multi_word_name_without_wildcard_stays_exact_phrase(captured):
    await patents.odp_search_applications(assignee_name="Cincinnati Children's")
    assert captured["body"]["q"] == f'{APPLICANT}:"Cincinnati Children\'s"'


async def test_multi_word_name_with_wildcard_matches_each_word(captured):
    await patents.odp_search_applications(assignee_name="Cincinnati Children*")
    assert captured["body"]["q"] == f"{APPLICANT}:(Cincinnati AND Children*)"


async def test_wildcard_name_lowercases_words_odp_would_read_as_operators(captured):
    await patents.odp_search_applications(assignee_name="Johnson AND Johnson*")
    assert captured["body"]["q"] == f"{APPLICANT}:(Johnson AND and AND Johnson*)"


async def test_wildcard_name_escapes_query_syntax_characters(captured):
    await patents.odp_search_applications(assignee_name="Hewlett-Packard (US) Dev*")
    assert captured["body"]["q"] == (
        f"{APPLICANT}:(Hewlett\\-Packard AND \\(US\\) AND Dev*)"
    )


async def test_wildcard_name_drops_words_with_no_letters_or_digits(captured):
    await patents.odp_search_applications(assignee_name="Smith & Nephew*")
    assert captured["body"]["q"] == f"{APPLICANT}:(Smith AND Nephew*)"


async def test_inventor_wildcard_applies_after_comma_normalization(captured):
    await patents.odp_search_applications(inventor_name="Jodel*, Sonata")
    assert captured["body"]["q"] == f"{INVENTOR}:(Sonata AND Jodel*)"


async def test_inventor_multi_word_without_wildcard_stays_exact_phrase(captured):
    await patents.odp_search_applications(inventor_name="Jodele, Sonata")
    assert captured["body"]["q"] == f'{INVENTOR}:"Sonata Jodele"'
