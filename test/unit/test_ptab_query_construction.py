"""Unit tests for PTAB client ODP DSL query construction.

Pure internal logic: search_proceedings and search_decisions must compile
their filters into the ``q`` parameter as field:value clauses (the live
API silently ignores bespoke query parameters — verified 2026-08-27).
Every request is captured before it leaves the client, so these tests
never touch the network.
"""

import pytest

from patent_mcp_server.uspto.ptab_client import PTABClient


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def client(monkeypatch):
    c = PTABClient()
    c.captured = {}

    async def capture(endpoint, method="GET", params=None, data=None, base_url=None):
        c.captured["endpoint"] = endpoint
        c.captured["params"] = params
        return {"count": 0}

    monkeypatch.setattr(c, "_make_request", capture)
    yield c
    await c.close()


# --- search_proceedings -------------------------------------------------

async def test_proceedings_patent_number_becomes_q_clause(client):
    await client.search_proceedings(patent_number="11656742")
    assert client.captured["params"]["q"] == "patentOwnerData.patentNumber:11656742"
    assert "patentNumber" not in client.captured["params"]


async def test_proceedings_trial_type_and_status_clauses(client):
    await client.search_proceedings(trial_type="IPR", status="Pending")
    assert client.captured["params"]["q"] == (
        'trialMetaData.trialTypeCode:IPR'
        ' AND trialMetaData.trialStatusCategory:"Pending"'
    )


async def test_proceedings_party_name_matches_either_side(client):
    await client.search_proceedings(party_name="Raytheon")
    assert client.captured["params"]["q"] == (
        '(regularPetitionerData.realPartyInInterestName:"Raytheon"'
        ' OR patentOwnerData.realPartyInInterestName:"Raytheon")'
    )


async def test_proceedings_party_name_strips_embedded_quotes(client):
    await client.search_proceedings(party_name='Ray" OR x:"y')
    assert '"Ray  OR x: y"' in client.captured["params"]["q"]


async def test_proceedings_filing_date_range_clause(client):
    await client.search_proceedings(
        filing_date_from="2024-01-01", filing_date_to="2024-01-31"
    )
    assert client.captured["params"]["q"] == (
        "trialMetaData.petitionFilingDate:[2024-01-01 TO 2024-01-31]"
    )


async def test_proceedings_open_ended_date_range_uses_wildcard(client):
    await client.search_proceedings(filing_date_from="2024-01-01")
    assert client.captured["params"]["q"] == (
        "trialMetaData.petitionFilingDate:[2024-01-01 TO *]"
    )


async def test_proceedings_combines_query_and_filters_with_and(client):
    await client.search_proceedings(query="foo", trial_type="IPR")
    assert client.captured["params"]["q"] == (
        "foo AND trialMetaData.trialTypeCode:IPR"
    )


async def test_proceedings_no_filters_sends_no_q(client):
    await client.search_proceedings(offset=5, limit=10)
    assert client.captured["params"] == {"offset": 5, "limit": 10}


# --- search_decisions ---------------------------------------------------

async def test_decisions_proceeding_and_patent_number_clauses(client):
    await client.search_decisions(
        proceeding_number="IPR2023-00037", patent_number="11570034"
    )
    assert client.captured["params"]["q"] == (
        "trialNumber:IPR2023-00037"
        " AND patentOwnerData.patentNumber:11570034"
    )
    assert "proceedingNumber" not in client.captured["params"]


async def test_decisions_decision_type_becomes_prefix_wildcard(client):
    await client.search_decisions(decision_type="institution")
    assert client.captured["params"]["q"] == (
        "documentData.documentTypeDescriptionText:institution*"
    )


async def test_decisions_decision_type_uses_first_token_only(client):
    await client.search_decisions(decision_type="final written decision")
    assert client.captured["params"]["q"] == (
        "documentData.documentTypeDescriptionText:final*"
    )


async def test_decisions_date_range_clause(client):
    await client.search_decisions(
        decision_date_from="2026-08-01", decision_date_to="2026-08-27"
    )
    assert client.captured["params"]["q"] == (
        "documentData.documentFilingDate:[2026-08-01 TO 2026-08-27]"
    )


async def test_decisions_open_ended_date_range_uses_wildcard(client):
    await client.search_decisions(decision_date_to="2013-01-01")
    assert client.captured["params"]["q"] == (
        "documentData.documentFilingDate:[* TO 2013-01-01]"
    )


async def test_decisions_no_filters_sends_no_q(client):
    await client.search_decisions(offset=0, limit=25)
    assert client.captured["params"] == {"offset": 0, "limit": 25}
