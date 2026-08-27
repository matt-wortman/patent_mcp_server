"""
USPTO Public Search Module (ppubs.uspto.gov)

This module provides tools for accessing the USPTO Public Search API at ppubs.uspto.gov,
which provides full text patent documents, patent PDFs, and advanced search capabilities.
"""

import copy
import json
import re
import asyncio
from typing import Any, Optional, Dict, List, Union
from datetime import datetime, timedelta
import httpx
import logging
from pathlib import Path

from patent_mcp_server.util.errors import ApiError
from patent_mcp_server.util.http import (
    make_logged_client, rate_limit_delay, rate_limit_error, uspto_retry
)
from patent_mcp_server.config import config
from patent_mcp_server.constants import (
    Sources, Fields, PrintStatus, HTTPMethods, Defaults
)

# Set up logging
logger = logging.getLogger('ppubs_uspto_gov')


# BRS field-code aliases rewritten before a query is sent upstream.
#
# The Patent Public Search backend has no "AN" index: its assignee-name
# index is "AS" (live-verified 2026-08-27 against /api/searches/counts:
# Raytheon.an. -> 0 results, Raytheon.as. -> 11,725 results in USPAT).
# Users coming from legacy PatFT syntax write ".an." for assignee, so
# translate it instead of silently returning zero results.
FIELD_CODE_ALIASES = {
    "an": "as",  # assignee name: legacy PatFT code -> PPUBS index
}

_ALIAS_PATTERN = re.compile(
    r"\.(" + "|".join(FIELD_CODE_ALIASES) + r")\.",
    re.IGNORECASE,
)


def rewrite_field_aliases(query: str) -> str:
    """Translate legacy BRS field codes to the indexes PPUBS actually has.

    Only text outside double-quoted phrases is rewritten, so a quoted
    phrase that happens to contain ".an." is left untouched. Field codes
    are matched case-insensitively (BRS treats them that way).
    """
    parts = query.split('"')
    # Even indexes are outside quotes; odd indexes are inside them.
    for i in range(0, len(parts), 2):
        parts[i] = _ALIAS_PATTERN.sub(
            lambda m: "." + FIELD_CODE_ALIASES[m.group(1).lower()] + ".",
            parts[i],
        )
    return '"'.join(parts)


def resolve_search_total(
    search_result: Dict[str, Any],
    counts_total: Optional[int],
) -> int:
    """Pick the query-wide document total for a search response.

    The searchWithBeFamily response's own "numFound" is NOT the query
    total — it is the number of family groups on the returned page
    (live-verified 2026-08-27: Marron.in. with pageCount=20 returned
    numFound=20 and 21 patents, while numberOfFamilies=176 and the
    /api/searches/counts endpoint reported numResults=207). The true
    document total therefore comes from the counts endpoint; the
    query-wide family count is the fallback when counts is unavailable.
    """
    if isinstance(counts_total, int):
        return counts_total
    families = search_result.get("numberOfFamilies")
    if isinstance(families, int):
        return families
    return search_result.get("numFound") or 0


class PpubsClient:
    """Client for the USPTO Public Search API at ppubs.uspto.gov.

    This client provides access to full text patent documents, search capabilities,
    and PDF downloads from the USPTO Public Search system.

    Supports context manager protocol for proper resource cleanup.
    """

    def __init__(self):
        self.headers = {
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": config.USER_AGENT,
            "Origin": config.PPUBS_BASE_URL,
            "Referer": f"{config.PPUBS_BASE_URL}/pubwebapp/",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "Priority": "u=1, i",
        }

        self.client = make_logged_client(self.headers)
        self.session = dict()
        self.case_id: Optional[str] = None
        self.access_token: Optional[str] = None

        # Session caching
        self.session_expires_at: Optional[datetime] = None

        # Serializes session establishment. Several tool calls can run
        # concurrently against this one client, and each of them resetting
        # the cookie jar and swapping the access token at the same time
        # would leave the others signing requests with a half-replaced
        # session.
        self._session_lock = asyncio.Lock()

        # Load search query template
        script_dir = Path(__file__).parent.parent
        search_query_path = script_dir / "json" / "search_query.json"
        with open(search_query_path, 'r') as f:
            self.search_query: Dict[str, Any] = json.load(f)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.close()

    def _session_is_cached(self) -> bool:
        """Whether the cached session is still usable."""
        if not (config.ENABLE_CACHING and self.session_expires_at):
            return False
        return datetime.now() < self.session_expires_at

    async def get_session(self) -> Optional[Dict[str, Any]]:
        """Establish a session with USPTO Public Search.

        Uses caching to avoid unnecessary session creation. Concurrent
        callers share a single establishment rather than each starting their
        own.
        """
        if self._session_is_cached():
            logger.info("Using cached session")
            return self.session

        async with self._session_lock:
            # Re-check inside the lock: another caller may have established a
            # session while we were waiting for it.
            if self._session_is_cached():
                logger.info("Using cached session")
                return self.session
            return await self._establish_session()

    async def _refresh_session(self, stale_token: Optional[str]) -> Optional[Dict[str, Any]]:
        """Replace a session that a request found expired.

        ``stale_token`` is the token the failed request actually used. If it
        no longer matches the current one, another caller has already
        refreshed and we reuse their session instead of discarding it.
        """
        async with self._session_lock:
            if self.access_token != stale_token:
                logger.info("Session already refreshed by another request")
                return self.session
            return await self._establish_session()

    async def _establish_session(self) -> Optional[Dict[str, Any]]:
        """Create a new upstream session. Callers must hold ``_session_lock``."""
        logger.info("Establishing new session with USPTO Public Search")
        # Drop cookies belonging to the dead session before asking for a new
        # one. Serialized by the lock, so this happens once per refresh.
        self.client.cookies = httpx.Cookies()

        try:
            # First request to get cookies
            response = await self.client.get(f"{config.PPUBS_BASE_URL}/pubwebapp/")

            # Create session
            url = f"{config.PPUBS_BASE_URL}/api/users/me/session"
            response = await self.client.post(
                url,
                json=-1,
                headers={
                    "X-Access-Token": "null",
                    "referer": f"{config.PPUBS_BASE_URL}/pubwebapp/",
                },
            )

            if response.status_code != 200:
                logger.error(f"Failed to establish session: {response.status_code} - {response.text}")
                return None

            # Log response body for debugging
            logger.debug(f"Session response body: {response.text}")

            self.session = response.json()
            self.case_id = self.session["userCase"]["caseId"]
            self.access_token = response.headers["X-Access-Token"]

            # Set session expiration
            if config.ENABLE_CACHING:
                self.session_expires_at = datetime.now() + timedelta(
                    minutes=config.SESSION_EXPIRY_MINUTES
                )

            logger.info(f"Session established with case ID: {self.case_id}")
            return self.session

        except Exception as e:
            logger.error(f"Error establishing session: {str(e)}")
            return None

    @staticmethod
    def _with_auth(kwargs: Dict[str, Any], token: Optional[str]) -> Dict[str, Any]:
        """Copy request kwargs with the session token added to the headers.

        An explicit ``X-Access-Token`` supplied by the caller wins, which is
        what lets session creation send its own placeholder token.
        """
        if not token:
            return kwargs
        merged = dict(kwargs)
        headers = dict(merged.get("headers") or {})
        headers.setdefault("X-Access-Token", token)
        merged["headers"] = headers
        return merged

    @uspto_retry
    async def make_request(self, method: str, url: str, **kwargs) -> Union[httpx.Response, Dict[str, Any]]:
        """Make a request with automatic retry for session expiration and network errors.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Target URL
            **kwargs: Additional arguments to pass to httpx

        Returns:
            httpx.Response object or error dictionary
        """
        try:
            # Capture the token this attempt is signed with, so that if it is
            # rejected we only refresh when nobody else already has.
            token = self.access_token
            response = await self.client.request(
                method, url, **self._with_auth(kwargs, token)
            )

            # Handle 403 (Session expired)
            if response.status_code == 403:
                logger.info("Session expired, refreshing")
                await self._refresh_session(stale_token=token)
                response = await self.client.request(
                    method, url, **self._with_auth(kwargs, self.access_token)
                )

            # Handle rate limiting (same policy as the shared helper)
            attempt = 0
            while response.status_code == 429:
                if attempt >= config.MAX_RETRIES:
                    logger.error(f"Rate limit (429) persisted after {config.MAX_RETRIES} retries: {url}")
                    return rate_limit_error()
                delay = rate_limit_delay(response, attempt)
                logger.warning(f"Rate limited (429) on {url}; retrying in {delay:.0f}s")
                await asyncio.sleep(delay)
                attempt += 1
                response = await self.client.request(
                    method, url, **self._with_auth(kwargs, self.access_token)
                )

            # Log response body for debugging (guarded: .text decodes the
            # whole body, so only pay for it when DEBUG is actually on)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Response body for {method} {url}: {response.text}")

            return response

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            logger.warning(f"Network error (will retry): {str(e)}")
            raise  # Let tenacity handle the retry
        except Exception as e:
            logger.error(f"Request error: {str(e)}")
            return ApiError.from_exception(e, f"Request to {url} failed")

    async def _ensure_case_id(self) -> Union[str, Dict[str, Any]]:
        """Return the case id of the current session, establishing one if needed.

        Callers should use the returned value rather than re-reading
        ``self.case_id``, so that a concurrent refresh cannot swap the case id
        out from under a request that is mid-assembly.

        Returns:
            The case id string, or an ApiError dict when a session could not
            be established (so callers never send ``caseId: null`` upstream).
        """
        if self.case_id is None:
            await self.get_session()
        if self.case_id is None:
            return ApiError.create(
                message=(
                    "Could not establish a session with USPTO Public Search — "
                    "the upstream session service may be down. Try again shortly."
                ),
                status_code=503,
                error_code="SESSION_ERROR",
            )
        return self.case_id

    async def run_query(
        self,
        query: str,
        start: int = Defaults.SEARCH_START,
        limit: int = Defaults.SEARCH_LIMIT,
        sort: str = "score desc",
        default_operator: str = "OR",
        sources: Optional[List[str]] = None,
        expand_plurals: bool = True,
        british_equivalents: bool = True,
    ) -> Dict[str, Any]:
        """Run a search query against USPTO Public Search.

        Legacy field codes with no PPUBS index are rewritten first (see
        FIELD_CODE_ALIASES; e.g. ".an." becomes ".as."). The returned
        dict's "numFound" is the query-wide document total (taken from
        the counts endpoint, falling back to the query-wide family
        count); the raw per-page family-group count the search endpoint
        reported as "numFound" is preserved as "familiesOnPage".

        Args:
            query: Search query string
            start: Starting position for results
            limit: Maximum number of results
            sort: Sort order
            default_operator: Default query operator (AND/OR)
            sources: List of source types to search
            expand_plurals: Whether to expand plural forms
            british_equivalents: Whether to include British spellings

        Returns:
            Dictionary containing search results or error
        """
        # Default sources
        if sources is None:
            sources = [Sources.PUBLISHED_APPLICATIONS, Sources.GRANTED_PATENTS, Sources.OCR]

        # Translate legacy field codes (e.g. ".an." -> ".as.") to the
        # indexes this backend actually has, so assignee queries do not
        # silently return zero results.
        query = rewrite_field_aliases(query)

        # Ensure we have a session
        case_id = await self._ensure_case_id()
        if isinstance(case_id, dict):
            return case_id

        logger.info(f"Running query: {query}")

        # Prepare query data. Deep copy: the template holds nested dicts, and
        # a shallow copy would let concurrent queries overwrite each other's
        # nested "query" fields (and permanently mutate the shared template).
        data = copy.deepcopy(self.search_query)
        data["start"] = start
        data["pageCount"] = min(limit, Defaults.SEARCH_LIMIT_MAX)
        data["sort"] = sort
        data["query"]["caseId"] = case_id
        data["query"]["op"] = default_operator
        data["query"]["q"] = query
        data["query"]["queryName"] = query
        data["query"]["userEnteredQuery"] = query
        data["query"]["databaseFilters"] = [
            {"databaseName": s, "countryCodes": []} for s in sources
        ]
        data["query"]["plurals"] = expand_plurals
        data["query"]["britishEquivalents"] = british_equivalents

        # Get counts first. This is the only endpoint that reports the
        # query-wide document total ("numResults"); the search endpoint's
        # "numFound" only counts family groups on the returned page.
        logger.info("Getting search counts")
        counts_url = f"{config.PPUBS_BASE_URL}/api/searches/counts"
        counts_response = await self.make_request(HTTPMethods.POST, counts_url, json=data["query"])

        # make_request returns a dict only for errors
        if isinstance(counts_response, dict):
            return counts_response

        counts_total: Optional[int] = None
        if counts_response.status_code == 200:
            try:
                counts_total = counts_response.json().get("numResults")
            except ValueError:
                counts_total = None

        # Execute search
        logger.info("Executing search query")
        search_url = f"{config.PPUBS_BASE_URL}/api/searches/searchWithBeFamily"
        search_response = await self.make_request(HTTPMethods.POST, search_url, json=data)

        if isinstance(search_response, dict):
            return search_response

        # Process response
        if search_response.status_code != 200:
            return ApiError.create(
                message=search_response.text[:1000],
                status_code=search_response.status_code
            )

        result = search_response.json()

        # Check for API errors
        if result.get(Fields.ERROR, None) is not None:
            error_obj = result[Fields.ERROR]
            return ApiError.create(
                message=error_obj.get(Fields.ERROR_MESSAGE, "Unknown error"),
                error_code=error_obj.get(Fields.ERROR_CODE)
            )

        # The raw "numFound" is the number of family groups on this page,
        # which downstream normalization would misreport as the query
        # total. Preserve it under an honest name and replace "numFound"
        # with the query-wide document total.
        result["familiesOnPage"] = result.get("numFound")
        result["numFound"] = resolve_search_total(result, counts_total)

        # Log search results for debugging (guarded: serializing the full
        # result is expensive, so only pay for it when DEBUG is actually on)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Search results: {json.dumps(result, indent=2, default=str)}")

        return result

    async def get_document(self, guid: str, source_type: str) -> Dict[str, Any]:
        """Get full document details by GUID.

        Args:
            guid: Document GUID
            source_type: Source type (USPAT, US-PGPUB, etc.)

        Returns:
            Dictionary containing document data or error
        """
        # Ensure we have a session
        case_id = await self._ensure_case_id()
        if isinstance(case_id, dict):
            return case_id

        logger.info(f"Getting document: {guid}")

        url = f"{config.PPUBS_BASE_URL}/api/patents/highlight/{guid}"
        params = {
            "queryId": 1,
            "source": source_type,
            "includeSections": True,
            "uniqueId": None,
        }

        response = await self.make_request(HTTPMethods.GET, url, params=params)

        if isinstance(response, dict):
            return response

        if response.status_code != 200:
            return ApiError.create(
                message=response.text[:1000],
                status_code=response.status_code
            )

        # Log document data for debugging (guarded, as in run_query)
        document_data = response.json()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Document data: {json.dumps(document_data, indent=2, default=str)}")

        return document_data

    async def _request_save(
        self,
        guid: str,
        image_location: str,
        page_count: int,
        document_type: str
    ) -> Union[str, Dict[str, Any]]:
        """Request generation of a PDF for download.

        Args:
            guid: Document GUID
            image_location: Path to document images
            page_count: Number of pages
            document_type: Document type

        Returns:
            Print job ID string or error dictionary
        """
        # Ensure we have a session
        case_id = await self._ensure_case_id()
        if isinstance(case_id, dict):
            return case_id

        logger.info(f"Requesting PDF save for: {guid}")

        page_keys = [
            f"{image_location}/{i:0>8}.tif"
            for i in range(1, page_count + 1)
        ]

        response = await self.make_request(
            HTTPMethods.POST,
            f"{config.PPUBS_BASE_URL}/api/print/imageviewer",
            json={
                "caseId": case_id,
                "pageKeys": page_keys,
                "patentGuid": guid,
                "saveOrPrint": "save",
                "source": document_type,
            },
        )

        if isinstance(response, dict):
            return response

        if response.status_code != 200:
            return ApiError.create(
                message=response.text[:1000],
                status_code=response.status_code
            )

        return response.text  # This is the print job ID

    async def download_image(
        self,
        guid: str,
        image_location: str,
        page_count: int,
        document_type: str
    ) -> Dict[str, Any]:
        """Download a patent document as PDF.

        Args:
            guid: Document GUID
            image_location: Path to document images
            page_count: Number of pages
            document_type: Document type

        Returns:
            Dictionary with raw PDF bytes under "content" on success, or an
            error dict on failure. Shape mirrors api_uspto_gov.download_file().
        """
        logger.info(f"Downloading document images for: {guid}")

        try:
            # Request the document save (establishes the session if needed)
            print_job_id = await self._request_save(guid, image_location, page_count, document_type)

            if isinstance(print_job_id, dict):
                return print_job_id

            # Poll for completion, bounded so a stuck or failed print job
            # cannot hang the tool call forever.
            print_data = None
            for _ in range(Defaults.PRINT_POLL_MAX_ATTEMPTS):
                logger.info(f"Checking print job status: {print_job_id}")
                response = await self.make_request(
                    HTTPMethods.POST,
                    f"{config.PPUBS_BASE_URL}/api/print/print-process",
                    json=[print_job_id],
                )

                if isinstance(response, dict):
                    return response

                if response.status_code != 200:
                    return ApiError.create(
                        message=response.text[:1000],
                        status_code=response.status_code
                    )

                print_data = response.json()
                print_status = print_data[0]["printStatus"]

                if print_status == PrintStatus.COMPLETED:
                    break

                if print_status == PrintStatus.FAILED:
                    return ApiError.create(
                        message=f"PPUBS PDF generation failed for print job {print_job_id}",
                        error_code="PDF_GENERATION_FAILED",
                    )

                await asyncio.sleep(Defaults.RETRY_DELAY)
            else:
                return ApiError.create(
                    message=(
                        f"PPUBS PDF generation did not complete within "
                        f"{Defaults.PRINT_POLL_MAX_ATTEMPTS} status checks"
                    ),
                    error_code="PDF_GENERATION_TIMEOUT",
                )

            # Get the PDF name
            pdf_name = print_data[0]["pdfName"]

            # Download the PDF
            logger.info(f"Downloading PDF: {pdf_name}")
            response = await self.make_request(
                HTTPMethods.GET,
                f"{config.PPUBS_BASE_URL}/api/print/save/{pdf_name}",
            )

            if isinstance(response, dict):
                return response

            if response.status_code != 200:
                return ApiError.create(
                    message="Failed to download PDF",
                    status_code=response.status_code
                )

            # Return raw PDF bytes; caller decides where to save.
            content = response.content

            return {
                "success": True,
                "filename": f"{guid}.pdf",
                "content_type": "application/pdf",
                "content": content,
                "size_bytes": len(content),
            }

        except Exception as e:
            logger.error(f"Error downloading document: {str(e)}")
            return ApiError.from_exception(e, "Document download failed")

    async def close(self):
        """Close the client connections and clean up resources."""
        logger.info("Closing ppubs client connections")
        await self.client.aclose()
