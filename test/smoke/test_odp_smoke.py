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
)

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

APP_NUM = "14643719"


def _load_oversized_response(result):
    """Load the full saved response when the token-budget summary is returned."""
    results_payload = result.get("results")
    if isinstance(results_payload, dict) and results_payload.get("_oversized"):
        return json.loads(Path(results_payload["file_path"]).read_text())
    return result


def _first_document_id(result):
    """Extract a stable document identifier from odp_get_documents output."""
    full_result = _load_oversized_response(result)
    results_payload = full_result.get("results", {})
    document_bag = results_payload.get("documentBag", []) if isinstance(results_payload, dict) else []
    if not document_bag:
        return None
    return document_bag[0].get("documentIdentifier")


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


async def test_odp_download_document_returns_pdf_on_disk():
    """odp_download_document downloads a real file wrapper document to disk."""
    result = await odp_get_documents(app_num=APP_NUM)
    assert not result.get("error"), f"Expected no error from odp_get_documents, got: {result}"

    document_id = _first_document_id(result)
    assert document_id, f"Expected a document identifier in odp_get_documents payload, got: {result}"

    download = await odp_download_document(app_num=APP_NUM, document_id=document_id)

    assert download.get("success") is True, f"Expected success, got: {download}"
    assert download.get("content_type") in {"application/pdf", "application/octet-stream"}, (
        f"Expected PDF-like content type, got: {download.get('content_type')}"
    )
    file_path = download.get("file_path")
    assert file_path and os.path.isfile(file_path), (
        f"Expected file_path to exist on disk, got: {file_path}"
    )
    assert os.path.getsize(file_path) > 1000, "Expected non-trivial file on disk"


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
