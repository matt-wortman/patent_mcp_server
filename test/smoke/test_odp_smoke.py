"""
Smoke tests for api.uspto.gov Open Data Portal tools.

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.

Stable fixture: application number 14643719.
"""

import json
import os
from pathlib import Path

import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import (
    odp_search_applications,
    odp_get_application,
    odp_get_application_metadata,
    odp_get_continuity,
    odp_get_assignment,
    odp_get_adjustment,
    odp_get_attorney,
    odp_get_foreign_priority,
    odp_get_transactions,
    odp_get_documents,
    odp_download_document,
    odp_search_datasets,
    odp_get_dataset,
    odp_get_associated_documents,
    odp_search_petition_decisions,
    odp_get_petition_decision,
    odp_download_dataset_file,
    odp_search_office_actions,
    odp_get_office_action,
    odp_get_office_action_text,
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

APP_NUM = "14643719"


def _load_oversized_response(result):
    """Load the full saved response when the token-budget summary is returned."""
    results_payload = result.get("results")
    if isinstance(results_payload, dict) and results_payload.get("_oversized"):
        return json.loads(Path(results_payload["file_path"]).read_text())
    return result


def _first_document_id_with_format(result, format_name):
    """Find a stable document that advertises the requested native format."""
    full_result = _load_oversized_response(result)
    results_payload = full_result.get("results", {})
    document_bag = (
        results_payload.get("documentBag", [])
        if isinstance(results_payload, dict)
        else []
    )
    for document in document_bag:
        formats = {
            option.get("mimeTypeIdentifier")
            for option in document.get("downloadOptionBag", [])
        }
        if format_name in formats:
            return document.get("documentIdentifier")
    return None


def _first_dataset_id(result):
    """Extract a dataset product identifier from odp_search_datasets output."""
    dataset_bag = result.get("bulkDataProductBag", [])
    if not dataset_bag:
        return None
    return dataset_bag[0].get("productIdentifier")


async def test_odp_search_applications_returns_results():
    """odp_search_applications returns a normalized success envelope with at least 1 hit."""
    result = await odp_search_applications(application_number=APP_NUM, limit=1)

    assert result.get("success") is True, f"Expected success, got: {result}"
    # ResponseEnvelope.from_odp always normalizes to "results" key
    assert len(result.get("results", [])) >= 1, "Expected at least one result"


async def test_odp_get_application_no_error():
    """odp_get_application returns without error and contains a results payload."""
    result = await odp_get_application(app_num=APP_NUM)

    assert not result.get("error"), f"Expected no error, got: {result}"
    # from_odp always normalizes to "results" — either a list or a passthrough dict
    assert "results" in result, f"Expected 'results' key in response, got keys: {list(result.keys())}"


async def test_odp_get_application_metadata_no_error():
    """odp_get_application_metadata returns without error and contains a results payload."""
    result = await odp_get_application_metadata(app_num=APP_NUM)

    assert not result.get("error"), f"Expected no error, got: {result}"
    assert "results" in result, f"Expected 'results' key in response, got keys: {list(result.keys())}"


async def test_odp_get_continuity_no_error():
    """odp_get_continuity returns without error."""
    result = await odp_get_continuity(app_num=APP_NUM)

    assert not result.get("error"), f"Expected no error, got: {result}"
    assert "results" in result, f"Expected 'results' key in response, got keys: {list(result.keys())}"


async def test_odp_get_assignment_no_error():
    """odp_get_assignment returns raw ODP data without an error flag."""
    result = await odp_get_assignment(app_num=APP_NUM)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert result.get("count", 0) >= 1, f"Expected assignment records, got: {result}"


async def test_odp_get_adjustment_no_error():
    """odp_get_adjustment returns PTA data without an error flag."""
    result = await odp_get_adjustment(app_num=APP_NUM)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert result.get("count", 0) >= 1, f"Expected adjustment records, got: {result}"


async def test_odp_get_attorney_no_error():
    """odp_get_attorney returns attorney data without an error flag."""
    result = await odp_get_attorney(app_num=APP_NUM)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert result.get("count", 0) >= 1, f"Expected attorney records, got: {result}"


async def test_odp_get_foreign_priority_no_error():
    """odp_get_foreign_priority returns priority data without an error flag."""
    result = await odp_get_foreign_priority(app_num=APP_NUM)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert result.get("count", 0) >= 1, f"Expected foreign priority records, got: {result}"


async def test_odp_get_transactions_no_error():
    """odp_get_transactions returns without error and contains a results payload."""
    result = await odp_get_transactions(app_num=APP_NUM)

    assert not result.get("error"), f"Expected no error, got: {result}"
    assert "results" in result, f"Expected 'results' key in response, got keys: {list(result.keys())}"


async def test_odp_get_documents_no_error():
    """odp_get_documents returns without error."""
    result = await odp_get_documents(app_num=APP_NUM)

    assert not result.get("error"), f"Expected no error, got: {result}"
    assert "results" in result, f"Expected 'results' key in response, got keys: {list(result.keys())}"


async def test_odp_download_document_prefers_native_xml_on_disk():
    """odp_download_document extracts native XML instead of downloading PDF."""
    result = await odp_get_documents(app_num=APP_NUM)
    assert not result.get("error"), f"Expected no error from odp_get_documents, got: {result}"

    document_id = _first_document_id_with_format(result, "XML")
    assert document_id, f"Expected a document with native XML, got: {result}"

    download = await odp_download_document(app_num=APP_NUM, document_id=document_id)

    assert download.get("success") is True, f"Expected success, got: {download}"
    assert download.get("selected_format") == "XML", (
        f"Expected native XML selection, got: {download}"
    )
    assert download.get("ocr_may_be_required") is False
    file_path = download.get("file_path")
    assert file_path and os.path.isfile(file_path), (
        f"Expected file_path to exist on disk, got: {file_path}"
    )
    assert file_path.lower().endswith(".xml"), f"Expected extracted XML path, got: {file_path}"
    assert os.path.getsize(file_path) > 1000, "Expected non-trivial XML on disk"


async def test_odp_search_datasets_returns_results():
    """odp_search_datasets returns live dataset records from the bulk-data catalog."""
    result = await odp_search_datasets(query="Patent File Wrapper", limit=1)

    assert isinstance(result, dict), "Expected a dict response"
    assert not result.get("error"), f"Expected no error, got: {result}"
    assert result.get("count", 0) >= 1, f"Expected at least one dataset, got: {result}"
    assert len(result.get("bulkDataProductBag", [])) >= 1, (
        f"Expected bulkDataProductBag entries, got: {result}"
    )


async def test_odp_get_dataset_no_error():
    """odp_get_dataset returns a normalized success envelope for a live product id."""
    search_result = await odp_search_datasets(query="Patent File Wrapper", limit=1)
    assert not search_result.get("error"), f"Expected no error from odp_search_datasets, got: {search_result}"

    product_id = _first_dataset_id(search_result)
    assert product_id, f"Expected a productIdentifier in odp_search_datasets payload, got: {search_result}"

    result = await odp_get_dataset(product_id=product_id)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert "results" in result, f"Expected 'results' key in response, got keys: {list(result.keys())}"


async def test_odp_search_by_cpc_classification_returns_results():
    """odp_search_applications with cpc_classification kwarg maps to the ODP
    filters[] array and returns at least one hit.

    Uses C12N 15/63 (recombinant DNA vectors) — a heavily-populated CPC
    symbol that has thousands of live applications. If this ever returns
    zero results, either the CPC normalizer is producing the wrong format
    or USPTO has changed the filter field name.
    """
    result = await odp_search_applications(
        cpc_classification="C12N 15/63",
        limit=1,
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, (
        "Expected at least one result for C12N 15/63 — if this fails, probe "
        "USPTO directly to check whether the filter field name or value "
        "format has changed."
    )


async def test_odp_search_by_publication_category_returns_results():
    """publication_category kwarg maps to applicationMetaData.publicationCategoryBag.

    Uses "Pre-Grant Publications - PGPub" — exact phrase stored by USPTO
    (probed 2026-04-22: 8.4M hits). Exact phrase is required; partial
    phrases 404.
    """
    result = await odp_search_applications(
        publication_category="Pre-Grant Publications - PGPub",
        limit=1,
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, (
        "Expected at least one result for 'Pre-Grant Publications - PGPub'"
    )


async def test_odp_search_by_application_status_returns_results():
    """application_status kwarg maps to applicationMetaData.applicationStatusDescriptionText.

    Uses "Non Final Action Mailed" — case-sensitive exact phrase (probed
    2026-04-22: 168K hits). Lowercase variants 404, which is why the
    docstring routes callers through get_status_code for discovery.
    """
    result = await odp_search_applications(
        application_status="Non Final Action Mailed",
        limit=1,
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, (
        "Expected at least one result for 'Non Final Action Mailed'"
    )


async def test_odp_search_by_application_type_returns_results():
    """application_type kwarg maps to applicationMetaData.applicationTypeLabelName.

    Uses "Utility" — the dominant type (probed 2026-04-22: 9.1M hits).
    Field is case-insensitive at USPTO.
    """
    result = await odp_search_applications(
        application_type="Utility",
        limit=1,
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, (
        "Expected at least one result for application_type='Utility'"
    )


async def test_odp_search_by_group_art_unit_returns_results():
    """group_art_unit kwarg maps to applicationMetaData.groupArtUnitNumber.

    Uses 1631 as an int to exercise the str-cast path (molecular biology /
    microbiology — Aldevron's art unit). Probed 2026-04-22: 13.9K hits with
    str "1631" and same count with int 1631.
    """
    result = await odp_search_applications(
        group_art_unit=1631,
        limit=1,
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert len(result.get("results", [])) >= 1, (
        "Expected at least one result for group_art_unit=1631"
    )


async def test_odp_get_application_never_raw_oversized():
    """odp_get_application must never return a raw >8K-token payload.

    For single-record dict results, the token budget enforcer must either:
    - fit the response within the 8K token budget (normal case), or
    - save to disk and return an _oversized summary envelope.

    A raw dict with >8K tokens means check_and_truncate wasn't wired in.
    """
    result = await odp_get_application(app_num=APP_NUM)

    assert not result.get("error"), f"Expected no error, got: {result}"

    results_payload = result.get("results")
    if isinstance(results_payload, dict) and results_payload.get("_oversized"):
        # Large response was correctly diverted to disk.
        assert results_payload.get("file_path"), "_oversized summary must include file_path"
        import os
        assert os.path.isfile(results_payload["file_path"]), (
            f"Oversized file {results_payload['file_path']} must exist on disk"
        )
    else:
        # Response fits in budget — verify the token count is reasonable.
        token_estimate = len(json.dumps(result, default=str)) // 4
        assert token_estimate <= 10_000, (
            f"odp_get_application returned ~{token_estimate} tokens inline — "
            "expected either a truncated response or an _oversized disk-save. "
            "check_and_truncate may not be wired in."
        )


# --- Petition decisions, associated documents, dataset files (v0.12.0) --

PETITION_APP_NUM = "09756788"   # one petition decision on record
ASSOC_DOC_APP_NUM = "14412875"  # granted application with pgpub + grant XML


async def test_odp_get_associated_documents_returns_file_uris():
    """odp_get_associated_documents returns pgpub/grant XML locations."""
    result = await odp_get_associated_documents(ASSOC_DOC_APP_NUM)

    assert result.get("success") is True, f"Expected success, got: {result}"
    rows = result.get("results", [])
    assert rows, "Expected at least one associated-documents row"
    grant_meta = rows[0].get("grantDocumentMetaData", {})
    assert grant_meta.get("fileLocationURI", "").startswith(
        "https://api.uspto.gov/api/v1/datasets/products/files/"
    ), f"Expected a dataset file URI, got: {grant_meta}"


async def test_odp_search_petition_decisions_by_app_num():
    """odp_search_petition_decisions filtered by application number works."""
    result = await odp_search_petition_decisions(app_num=PETITION_APP_NUM, limit=2)

    assert result.get("success") is True, f"Expected success, got: {result}"
    rows = result.get("results", [])
    assert rows, "Expected at least one petition decision"
    assert rows[0].get("applicationNumberText") == PETITION_APP_NUM


async def test_odp_get_petition_decision_roundtrip():
    """odp_get_petition_decision fetches a record found via search."""
    search_result = await odp_search_petition_decisions(
        app_num=PETITION_APP_NUM, limit=1
    )
    assert search_result.get("success") is True, f"Got: {search_result}"
    record_id = search_result["results"][0].get("petitionDecisionRecordIdentifier")
    assert record_id, f"Expected a record identifier, got: {search_result}"

    result = await odp_get_petition_decision(record_id)

    assert result.get("success") is True, f"Expected success, got: {result}"
    assert result.get("count", 0) >= 1


async def test_odp_download_dataset_file_saves_grant_xml(tmp_path, monkeypatch):
    """odp_download_dataset_file follows the signed redirect and saves the file."""
    from patent_mcp_server.config import config as _config

    monkeypatch.setattr(_config, "DOWNLOAD_DIR", str(tmp_path))

    assoc = await odp_get_associated_documents(ASSOC_DOC_APP_NUM)
    assert assoc.get("success") is True, f"Got: {assoc}"
    file_uri = assoc["results"][0]["grantDocumentMetaData"]["fileLocationURI"]

    result = await odp_download_dataset_file(file_uri)

    assert result.get("success") is True, f"Expected success, got: {result}"
    saved = Path(result["file_path"])
    assert saved.exists() and saved.stat().st_size > 1000
    assert saved.read_bytes()[:5] == b"<?xml"


async def test_odp_search_petition_decisions_outcome_and_date_filters():
    """Outcome filter, two-sided date range, and ascending sort all apply."""
    result = await odp_search_petition_decisions(
        decision="DENIED,DISMISSED",
        decision_date_from="2026-01-01",
        decision_date_to="2026-06-30",
        sort_order="asc",
        limit=5,
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    rows = _load_oversized_response(result)["results"]
    assert rows, "Expected denied/dismissed decisions in the first half of 2026"
    dates = [row["decisionDate"] for row in rows]
    assert dates == sorted(dates), f"Expected ascending dates, got: {dates}"
    for row in rows:
        assert row["decidedPetitionTypeCodeDescriptionText"] in {"DENIED", "DISMISSED"}
        assert "2026-01-01" <= row["decisionDate"] <= "2026-06-30"


# --- Office actions (v0.15.0) --------------------------------------------

OA_APP_NUM = APP_NUM  # 14643719: one non-final rejection (2017), one allowance (2018)


async def test_odp_search_office_actions_by_app_num():
    """Searching by application number returns that application's letters."""
    result = await odp_search_office_actions(app_num=OA_APP_NUM)

    assert result.get("success") is True, f"Expected success, got: {result}"
    rows = result.get("results", [])
    assert {row["documentCode"] for row in rows} >= {"CTNF", "NOA"}, f"Got: {rows}"
    assert all(row["applicationNumberText"] == OA_APP_NUM for row in rows)


async def test_odp_search_office_actions_filters_and_one_sided_date():
    """Document type, rejection type, and a one-sided date range all apply."""
    result = await odp_search_office_actions(
        document_code="CTNF",
        rejection_type="101",
        submission_date_from="2026-01-01",
        limit=5,
    )

    assert result.get("success") is True, f"Expected success, got: {result}"
    rows = result.get("results", [])
    assert rows, "Expected 101 non-final rejections mailed in 2026"
    dates = [row["submissionDate"] for row in rows]
    assert dates == sorted(dates, reverse=True), f"Expected newest first: {dates}"
    for row in rows:
        assert row["documentCode"] == "CTNF"
        assert "101" in row.get("rejectionTypeBag", [])
        assert row["submissionDate"] >= "2026-01-01"


async def _allowance_record_id():
    search = await odp_search_office_actions(app_num=OA_APP_NUM, document_code="NOA")
    assert search.get("success") is True, f"Got: {search}"
    return search["results"][0]["officeActionRecordIdentifier"]


async def test_odp_get_office_action_roundtrip():
    """odp_get_office_action fetches a record found via search."""
    record_id = await _allowance_record_id()

    result = await odp_get_office_action(record_id)

    assert result.get("success") is True, f"Expected success, got: {result}"
    row = result["results"][0]
    assert row["officeActionRecordIdentifier"] == record_id
    assert row["applicationNumberText"] == OA_APP_NUM


async def test_odp_get_office_action_text_returns_full_letter():
    """The text tool returns the whole letter, past the 500-character cut."""
    record_id = await _allowance_record_id()

    result = await odp_get_office_action_text(record_id)

    assert result.get("success") is True, f"Expected success, got: {result}"
    text = _load_oversized_response(result)["results"]["officeActionText"]
    assert len(text) > 1000, f"Expected the full letter, got {len(text)} chars"
    assert "EXAMINER'S AMENDMENT" in text


# --- Multi-word name wildcards (live-verified 2026-10-14) -----------------
# ODP ignores "*" inside a quoted phrase, so these returned 0 before the fix.

async def test_multi_word_assignee_wildcard_matches_name_variants():
    exact = await odp_search_applications(
        assignee_name="Cincinnati Children's", limit=1
    )
    wildcard = await odp_search_applications(
        assignee_name="Cincinnati Children*", limit=1
    )
    assert exact["total"] >= 43
    # The wildcard also catches the curly-apostrophe spelling "Children’s".
    assert wildcard["total"] > exact["total"]


async def test_multi_word_inventor_wildcard_matches():
    exact = await odp_search_applications(inventor_name="Sonata Jodele", limit=1)
    wildcard = await odp_search_applications(inventor_name="Sonata Jodel*", limit=1)
    assert exact["total"] >= 1
    assert wildcard["total"] >= exact["total"]
