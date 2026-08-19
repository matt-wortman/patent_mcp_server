"""Unit tests for tool-boundary input validation (IR-05).

Pure internal logic: invalid pagination values and Lucene-interpolated
identifiers must be rejected BEFORE any upstream call. Each test installs a
tripwire on the relevant client method — if the tool tries to hit the
network, the tripwire raises and the test fails.
"""

import pytest

import patent_mcp_server.patents as patents


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a tool reaches its client instead of validating first."""

    async def tripwire(*args, **kwargs):
        raise AssertionError("Tool attempted an upstream call with invalid input")

    monkeypatch.setattr(patents.dsapi_client, "search", tripwire)
    monkeypatch.setattr(patents.api_client, "make_request", tripwire)
    monkeypatch.setattr(patents.api_client, "download_file", tripwire)
    monkeypatch.setattr(patents.ptab_client, "search_proceedings", tripwire)
    monkeypatch.setattr(patents.ptab_client, "search_decisions", tripwire)
    monkeypatch.setattr(patents.ptab_client, "search_documents", tripwire)
    monkeypatch.setattr(patents.ptab_client, "get_proceeding_documents", tripwire)
    monkeypatch.setattr(patents.ptab_client, "search_appeal_decisions", tripwire)
    monkeypatch.setattr(patents.ptab_client, "get_appeal_decisions", tripwire)
    monkeypatch.setattr(patents.ptab_client, "search_interference_decisions", tripwire)
    monkeypatch.setattr(patents.ptab_client, "get_interference_decisions", tripwire)


def assert_validation_error(result):
    assert result.get("error") is True, f"Expected error envelope, got: {result}"
    assert result.get("error_code") == "VALIDATION_ERROR", f"Got: {result}"


# --- Lucene-interpolated identifiers -----------------------------------

async def test_dsapi_search_rejections_rejects_digitless_app_number():
    result = await patents.dsapi_search_rejections("no-digits-here")
    assert_validation_error(result)


async def test_dsapi_search_rejections_sanitizes_injection_to_digits(monkeypatch):
    """An app number carrying a Lucene quote-breakout is stripped to its
    digits before interpolation — the criteria never contains the payload."""
    captured = {}

    async def capture(*args, **kwargs):
        captured.update(kwargs)
        return {"response": {"start": 0, "numFound": 0, "docs": []}}

    monkeypatch.setattr(patents.dsapi_client, "search", capture)
    await patents.dsapi_search_rejections('16123456" OR bodyText:"*')
    assert captured["criteria"] == 'patentApplicationNumber:"16123456"'


async def test_dsapi_search_oa_citations_rejects_non_digit_app_number():
    result = await patents.dsapi_search_oa_citations("not-a-number")
    assert_validation_error(result)


async def test_dsapi_search_enriched_citations_rejects_non_digit_app_number():
    result = await patents.dsapi_search_enriched_citations("")
    assert_validation_error(result)


async def test_dsapi_search_office_actions_rejects_non_digit_app_number():
    result = await patents.dsapi_search_office_actions('x" OR bodyText:"*')
    assert_validation_error(result)


async def test_dsapi_get_patent_litigation_rejects_bad_patent_number():
    result = await patents.dsapi_get_patent_litigation('" OR case_name:"*')
    assert_validation_error(result)


async def test_get_status_code_rejects_non_digit_code():
    result = await patents.get_status_code('30" OR appl_status_code:"*')
    assert_validation_error(result)


async def test_dsapi_lookup_status_code_rejects_non_digit_code():
    result = await patents.dsapi_lookup_status_code("abandoned")
    assert_validation_error(result)


# --- Pagination bounds --------------------------------------------------

async def test_odp_search_applications_rejects_negative_offset():
    result = await patents.odp_search_applications(query="test", offset=-1)
    assert_validation_error(result)


async def test_odp_search_applications_rejects_oversized_limit():
    result = await patents.odp_search_applications(query="test", limit=10_000)
    assert_validation_error(result)


async def test_odp_search_datasets_rejects_nonpositive_limit():
    result = await patents.odp_search_datasets(limit=0)
    assert_validation_error(result)


async def test_ptab_search_proceedings_rejects_negative_offset():
    result = await patents.ptab_search_proceedings(query="test", offset=-5)
    assert_validation_error(result)


async def test_ptab_search_decisions_rejects_oversized_limit():
    result = await patents.ptab_search_decisions(query="test", limit=999)
    assert_validation_error(result)


# --- New v0.12.0 tool boundaries ---------------------------------------

async def test_ptab_search_trial_documents_rejects_oversized_limit():
    result = await patents.ptab_search_trial_documents(query="test", limit=999)
    assert_validation_error(result)


async def test_ptab_get_proceeding_documents_rejects_blank_number():
    result = await patents.ptab_get_proceeding_documents("   ")
    assert_validation_error(result)


async def test_ptab_search_appeal_decisions_rejects_digitless_app_number():
    result = await patents.ptab_search_appeal_decisions(app_num="no-digits")
    assert_validation_error(result)


async def test_ptab_get_appeal_decisions_rejects_blank_number():
    result = await patents.ptab_get_appeal_decisions("")
    assert_validation_error(result)


async def test_ptab_search_interference_decisions_rejects_negative_offset():
    result = await patents.ptab_search_interference_decisions(offset=-1)
    assert_validation_error(result)


async def test_ptab_get_interference_decisions_rejects_blank_number():
    result = await patents.ptab_get_interference_decisions("  ")
    assert_validation_error(result)


async def test_odp_search_petition_decisions_rejects_digitless_app_number():
    result = await patents.odp_search_petition_decisions(app_num="abc")
    assert_validation_error(result)


async def test_odp_get_petition_decision_rejects_blank_record_id():
    result = await patents.odp_get_petition_decision("   ")
    assert_validation_error(result)


async def test_odp_download_dataset_file_rejects_foreign_url():
    result = await patents.odp_download_dataset_file(
        "https://evil.example.com/api/v1/datasets/products/files/x/y.xml"
    )
    assert_validation_error(result)


async def test_odp_download_dataset_file_rejects_non_dataset_path():
    result = await patents.odp_download_dataset_file(
        "https://api.uspto.gov/api/v1/patent/applications/14412875"
    )
    assert_validation_error(result)
