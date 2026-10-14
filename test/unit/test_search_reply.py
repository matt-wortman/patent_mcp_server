"""Unit tests for the shared reply of the ODP and PTAB search tools.

Pure internal logic: every search reply, including empty and error
replies, must carry `query_sent` (the search as sent to USPTO, without
paging) so a hidden rewrite of the caller's input is visible.
"""

import pytest

from patent_mcp_server.config import config
from patent_mcp_server.patents import _search_reply


pytestmark = pytest.mark.unit

ODP_BODY = {
    "pagination": {"offset": 0, "limit": 2},
    "q": "applicationMetaData.firstApplicantName:(Cincinnati AND Children*)",
    "filters": [{"name": "applicationNumberText", "value": ["16123456"]}],
    "rangeFilters": [{
        "field": "applicationMetaData.filingDate",
        "valueFrom": "2023-01-01",
        "valueTo": "2024-01-01",
    }],
    "sort": [{"field": "filingDate", "order": "desc"}],
}
ODP_SENT = {k: v for k, v in ODP_BODY.items() if k != "pagination"}
PTAB_PARAMS = {"offset": 0, "limit": 2, "q": "trialMetaData.trialTypeCode:IPR"}


def test_odp_success_reply_carries_query_sent_without_paging():
    raw = {"count": 1, "patentFileWrapperDataBag": [{"applicationNumberText": "1"}]}
    reply = _search_reply(raw, "odp", 0, 2, ODP_BODY)
    assert reply["success"] is True
    assert reply["total"] == 1
    assert reply["query_sent"] == ODP_SENT


def test_ptab_success_reply_carries_query_sent_without_paging():
    raw = {"count": 1, "patentTrialProceedingDataBag": [{"trialNumber": "IPR1"}]}
    reply = _search_reply(raw, "ptab", 0, 2, PTAB_PARAMS)
    assert reply["source"] == "ptab"
    assert reply["results"] == [{"trialNumber": "IPR1"}]
    assert reply["query_sent"] == {"q": "trialMetaData.trialTypeCode:IPR"}


def test_zero_match_reply_carries_query_sent():
    not_found = {"error": True, "status_code": 404, "message": "Not Found"}
    reply = _search_reply(not_found, "odp", 0, 2, ODP_BODY)
    assert reply["success"] is True
    assert reply["total"] == 0
    assert reply["query_sent"] == ODP_SENT


def test_error_reply_carries_query_sent():
    bad_request = {"error": True, "status_code": 400, "message": "Bad Request"}
    reply = _search_reply(bad_request, "ptab", 0, 2, PTAB_PARAMS)
    assert reply["error"] is True
    assert reply["status_code"] == 400
    assert reply["query_sent"] == {"q": "trialMetaData.trialTypeCode:IPR"}


def test_search_without_criteria_reports_empty_query_sent():
    raw = {"count": 0, "patentTrialDecisionDataBag": []}
    reply = _search_reply(raw, "ptab", 0, 2, {"offset": 0, "limit": 2})
    assert reply["query_sent"] == {}


def test_oversized_reply_keeps_query_sent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MAX_RESPONSE_TOKENS", 50)
    monkeypatch.setattr(config, "TRUNCATE_LARGE_RESPONSES", True)
    records = [{"text": "x" * 2000} for _ in range(3)]
    raw = {"count": 3, "patentFileWrapperDataBag": records}

    reply = _search_reply(raw, "odp", 0, 3, ODP_BODY)

    assert reply["_oversized"] is True
    assert reply["count"] == 3
    assert reply["query_sent"] == ODP_SENT


@pytest.mark.parametrize("source", ["odp", "ptab"])
def test_unreadable_search_becomes_error_with_query_sent(source):
    unreadable = {"count": 0, "requestIdentifier": "b0997"}
    reply = _search_reply(unreadable, source, 0, 2, ODP_BODY)
    assert reply["error"] is True
    assert reply["error_code"] == "UNREADABLE_SEARCH"
    assert "could not read" in reply["message"]
    assert reply["query_sent"] == ODP_SENT
