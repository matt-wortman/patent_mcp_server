"""Unit tests for looser-name checks on name searches.

Pure internal logic: which looser forms of a name are built, which stored
names are sampled from records, and when the checks run at all. The
search function is replaced by one that records the clauses it is asked
for, so nothing crosses the network. Live behavior is covered by
test/smoke/test_looser_names_smoke.py.
"""

import pytest

import patent_mcp_server.patents as patents
from patent_mcp_server.uspto.ptab_client import PTABClient
from patent_mcp_server.util.query import (
    has_apostrophe,
    looser_name_exprs,
    prefix_expr,
    prefix_terms,
)


pytestmark = pytest.mark.unit


# --- Looser forms of a name ----------------------------------------------

def test_two_word_name_gets_every_looser_form():
    assert looser_name_exprs("Raytheon Co") == [
        ("all words, in any order", "(Raytheon AND Co)"),
        ("every word as a prefix", "(Raytheon* AND Co*)"),
        ("without Raytheon", "Co*"),
        ("without Co", "Raytheon*"),
    ]


def test_single_word_gets_only_the_prefix_form():
    assert looser_name_exprs("Raytheon") == [
        ("every word as a prefix", "Raytheon*"),
    ]


def test_forms_matching_the_original_search_are_left_out():
    # "Samsung Elec*" is already sent as (Samsung AND Elec*).
    forms = [label for label, _ in looser_name_exprs("Samsung Elec*")]
    assert "all words, in any order" not in forms
    assert looser_name_exprs("Raytheon*") == []


def test_words_are_left_out_only_for_short_names():
    labels = [label for label, _ in looser_name_exprs("A B C D E")]
    assert labels == ["all words, in any order", "every word as a prefix"]


def test_prefix_cuts_each_word_at_its_apostrophe():
    assert prefix_terms("Children's Hospital") == ["Children*", "Hospital*"]
    assert prefix_terms("Children\u2019s Hospital") == ["Children*", "Hospital*"]


def test_prefix_splits_words_at_punctuation():
    assert prefix_terms("Hewlett-Packard Dev.") == ["Hewlett*", "Packard*", "Dev*"]
    assert prefix_terms("Smith & Nephew") == ["Smith*", "Nephew*"]
    assert prefix_expr("&") is None


def test_apostrophes_are_recognized_in_every_form():
    assert has_apostrophe("Children's")
    assert has_apostrophe("Children\u2019s")
    assert has_apostrophe("Children\u2018s")
    assert not has_apostrophe("Childrens")


# --- Stored names sampled from records -------------------------------------

def test_sample_keeps_names_sharing_a_word_with_the_typed_name():
    records = [
        {"applicationMetaData": {"inventorBag": [
            {"inventorNameText": "Sonata Jodele"},
            {"inventorNameText": "Bruce Aronow"},
        ]}},
        {"applicationMetaData": {"inventorBag": [
            {"inventorNameText": "SONATA JODELE"},
            {"inventorNameText": "Sonata Jodele"},
        ]}},
    ]
    names = patents._sample_names(
        records,
        lambda record: patents._application_stored_names("inventor_name", record),
        "Jodele Sonata",
    )
    assert names == ["Sonata Jodele", "SONATA JODELE"]


def test_sample_matches_words_by_prefix_ignoring_case():
    records = [{"applicationMetaData": {
        "firstApplicantName": "CHILDRENS HOSPITAL MEDICAL CENTER"
    }}]
    names = patents._sample_names(
        records,
        lambda record: patents._application_stored_names("assignee_name", record),
        "Children's Hospital Medical Center",
    )
    assert names == ["CHILDRENS HOSPITAL MEDICAL CENTER"]


def test_party_names_come_from_both_sides_of_a_trial():
    record = {
        "regularPetitionerData": {"realPartyInInterestName": "Samsung Electronics"},
        "patentOwnerData": {"realPartyInInterestName": "Netlist"},
    }
    assert patents._party_stored_names("party_name", record) == [
        "Samsung Electronics", "Netlist",
    ]


# --- When the checks run ---------------------------------------------------

NOT_FOUND = {"error": True, "status_code": 404, "message": "Not Found"}


def _recorder(answer):
    asked = []

    async def run(input_name, clause):
        asked.append((input_name, clause))
        return answer

    return asked, run


async def _check(reply, offset, names, answer):
    asked, run = _recorder(answer)
    result = await patents._check_looser_names(
        reply, offset, names,
        clause_for=lambda input_name, expr: f"f:{expr}",
        run=run,
        records_in=lambda raw: raw.get("records") or [],
        names_in=lambda input_name, record: [record["name"]],
    )
    return result, asked


@pytest.mark.asyncio
async def test_empty_search_tries_every_looser_form():
    reply, asked = await _check(
        {"success": True, "total": 0, "message": "No records matched."},
        0, {"assignee_name": "Raytheon Co"},
        {"count": 2, "records": [{"name": "Raytheon Company"}]},
    )
    assert [clause for _, clause in asked] == [
        "f:(Raytheon AND Co)", "f:(Raytheon* AND Co*)", "f:Co*", "f:Raytheon*",
    ]
    first = reply["name_variants"][0]
    assert first == {
        "input": "assignee_name",
        "form": "all words, in any order",
        "clause": "f:(Raytheon AND Co)",
        "total": 2,
        "names_found": ["Raytheon Company"],
    }
    assert reply["message"].startswith("No records matched. Looser forms")


@pytest.mark.asyncio
async def test_looser_forms_with_no_matches_are_still_reported():
    reply, _ = await _check(
        {"success": True, "total": 0}, 0, {"assignee_name": "Raytheon"}, NOT_FOUND
    )
    assert reply["name_variants"] == [{
        "input": "assignee_name",
        "form": "every word as a prefix",
        "clause": "f:Raytheon*",
        "total": 0,
        "names_found": [],
    }]


@pytest.mark.asyncio
async def test_found_records_without_apostrophe_make_no_extra_search():
    reply, asked = await _check(
        {"success": True, "total": 7229}, 0, {"assignee_name": "Raytheon Company"},
        {"count": 1, "records": []},
    )
    assert asked == []
    assert "name_variants" not in reply


@pytest.mark.asyncio
async def test_found_records_with_apostrophe_count_what_the_exact_name_missed():
    reply, asked = await _check(
        {"success": True, "total": 998}, 0,
        {"assignee_name": "Children's Hospital"},
        {"count": 27, "records": [{"name": "CHILDRENS HOSPITAL"}]},
    )
    assert asked == [(
        "assignee_name",
        "(f:(Children* AND Hospital*) AND NOT f:\"Children's Hospital\")",
    )]
    assert reply["name_variants"] == [{
        "input": "assignee_name",
        "form": "every word as a prefix",
        "clause": "f:(Children* AND Hospital*)",
        "missed_by_exact_name": 27,
        "names_found": ["CHILDRENS HOSPITAL"],
    }]


@pytest.mark.asyncio
async def test_apostrophe_check_reports_nothing_when_nothing_was_missed():
    reply, asked = await _check(
        {"success": True, "total": 998}, 0,
        {"assignee_name": "Children's Hospital"}, NOT_FOUND,
    )
    assert len(asked) == 1
    assert "name_variants" not in reply


@pytest.mark.asyncio
async def test_later_pages_are_not_checked():
    # USPTO also reports a page past the last record as zero matches.
    reply, asked = await _check(
        {"success": True, "total": 0}, 25, {"assignee_name": "Raytheon Co"}, NOT_FOUND
    )
    assert asked == []
    assert "name_variants" not in reply


@pytest.mark.asyncio
async def test_error_replies_are_not_checked():
    reply, asked = await _check(
        {"error": True, "message": "boom"}, 0, {"assignee_name": "Raytheon Co"},
        NOT_FOUND,
    )
    assert asked == []
    assert "name_variants" not in reply


@pytest.mark.asyncio
async def test_a_failed_looser_search_is_reported_as_an_error():
    reply, _ = await _check(
        {"success": True, "total": 0}, 0, {"assignee_name": "Raytheon"},
        {"error": True, "status_code": 500, "message": "Server Error"},
    )
    assert reply["name_variants"][0]["error"] == "Server Error"


# --- Board client accepts a ready-built party clause -----------------------

@pytest.mark.asyncio
async def test_board_client_uses_given_party_clause_and_fields(monkeypatch):
    client = PTABClient()
    captured = {}

    async def capture(endpoint, method="GET", params=None, data=None, base_url=None):
        captured["params"] = params
        return {"count": 0}

    monkeypatch.setattr(client, "_make_request", capture)
    await client.search_proceedings(
        trial_type="IPR", party_clause="(p:Samsung* OR o:Samsung*)", fields="a,b"
    )
    await client.close()
    assert captured["params"]["q"] == (
        "trialMetaData.trialTypeCode:IPR AND (p:Samsung* OR o:Samsung*)"
    )
    assert captured["params"]["fields"] == "a,b"
