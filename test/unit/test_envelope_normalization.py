"""Unit tests for response envelope normalization of ODP/PTAB bag keys.

Pure internal logic: given the response shapes the live endpoints return
(bag key names live-verified 2026-08-19), the envelope helpers must find
the result list and total count. No network.
"""

import pytest

from patent_mcp_server.util.response import ResponseEnvelope

pytestmark = pytest.mark.unit


def _bag_response(key, n=2, total=10):
    return {"count": total, key: [{"row": i} for i in range(n)]}


# --- from_ptab: one test per live-verified bag key ----------------------

@pytest.mark.parametrize("key", [
    "patentTrialProceedingDataBag",   # /trials/proceedings
    "patentTrialDecisionDataBag",     # /trials/decisions
    "patentTrialDocumentDataBag",     # /trials/documents
    "patentAppealDataBag",            # /appeals decisions
    "patentInterferenceDataBag",      # /interferences decisions
])
def test_from_ptab_finds_live_bag_keys(key):
    result = ResponseEnvelope.from_ptab(_bag_response(key))

    assert result["success"] is True
    assert result["count"] == 2
    assert result["total"] == 10
    assert result["results"] == [{"row": 0}, {"row": 1}]


def test_from_ptab_empty_bag_yields_empty_results():
    result = ResponseEnvelope.from_ptab({"count": 0, "patentAppealDataBag": []})

    assert result["success"] is True
    assert result["results"] == []
    assert result["total"] == 0


# --- from_odp: file wrapper and petition decision bags ------------------

def test_from_odp_finds_file_wrapper_bag():
    result = ResponseEnvelope.from_odp(_bag_response("patentFileWrapperDataBag"))

    assert result["success"] is True
    assert result["count"] == 2
    assert result["total"] == 10


def test_from_odp_finds_petition_decision_bag():
    result = ResponseEnvelope.from_odp(_bag_response("petitionDecisionDataBag"))

    assert result["success"] is True
    assert result["count"] == 2
    assert result["total"] == 10
    assert result["results"] == [{"row": 0}, {"row": 1}]
