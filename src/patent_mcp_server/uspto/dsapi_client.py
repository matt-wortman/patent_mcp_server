"""
USPTO Data Set API (DSAPI) Client (api.uspto.gov — Open Data Portal)

This module provides a generic client for accessing the USPTO DSAPI datasets
after their April 20, 2026 migration from developer.uspto.gov/ds-api to the
Open Data Portal at api.uspto.gov. All datasets share a uniform interface:

- Fields: GET /api/v1/patent/oa/{dataset}/{version}/fields
- Search: POST /api/v1/patent/oa/{dataset}/{version}/records (form-encoded)

Authentication: requires X-API-KEY header (USPTO_API_KEY, same key used by
the odp_* tools).

Office Action datasets (confirmed migrated to api.uspto.gov):
- oa_actions/v1 — Office action full text
- oa_citations/v2 — Office action citations
- oa_rejections/v2 — Office action rejections
- enriched_cited_reference_metadata/v3 — Enriched cited reference metadata

Legacy datasets (migration status uncertain as of April 2026):
- oce_patent_litigation_cases/v1 — may 404 on new base URL
- oce_patent_examination_status_codes/v1 — may be covered by
  /api/v1/patent/status-codes on ODP
"""

from typing import Any, Dict
import httpx
import logging

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from patent_mcp_server.util.logging import LoggingTransport
from patent_mcp_server.util.errors import ApiError
from patent_mcp_server.config import config

# Set up logging
logger = logging.getLogger('dsapi_client')

DSAPI_BASE_URL = "https://api.uspto.gov"
DSAPI_PATH_PREFIX = "/api/v1/patent/oa"


class DsapiClient:
    """Client for the USPTO Data Set API (DSAPI) on api.uspto.gov.

    Provides a uniform interface for querying any DSAPI dataset using
    Lucene query syntax. Requires USPTO_API_KEY (same key used by odp_* tools).

    Supports context manager protocol for proper resource cleanup.
    """

    def __init__(self):
        self.headers = {
            "User-Agent": config.USER_AGENT,
            "X-API-KEY": config.USPTO_API_KEY if config.USPTO_API_KEY else "",
        }

        # Create a custom transport that logs all requests and responses
        transport = httpx.AsyncHTTPTransport()
        logging_transport = LoggingTransport(transport)

        self.client = httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            transport=logging_transport,
            timeout=config.REQUEST_TIMEOUT,
        )

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.close()

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(
            multiplier=config.RETRY_DELAY,
            min=config.RETRY_MIN_WAIT,
            max=config.RETRY_MAX_WAIT,
        ),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        reraise=True,
    )
    async def search(
        self,
        dataset: str,
        version: str,
        criteria: str = "*:*",
        start: int = 0,
        rows: int = 25,
    ) -> Dict[str, Any]:
        """Search a dataset using Lucene query syntax.

        The DSAPI search endpoint accepts form-encoded POST data with:
        - criteria: Lucene query string (default "*:*" for all records)
        - start: Offset for pagination (default 0)
        - rows: Number of results to return (default 25)

        Response format: {"response": {"start": N, "numFound": N, "docs": [...]}}

        Args:
            dataset: Dataset name (e.g., "oa_rejections")
            version: Dataset version (e.g., "v2")
            criteria: Lucene query string
            start: Pagination offset
            rows: Number of rows to return

        Returns:
            Search results dictionary or error dictionary.
        """
        url = f"{DSAPI_BASE_URL}{DSAPI_PATH_PREFIX}/{dataset}/{version}/records"
        logger.info(f"Searching {dataset}/{version}: criteria={criteria}, start={start}, rows={rows}")

        form_data = {
            "criteria": criteria,
            "start": str(start),
            "rows": str(rows),
        }

        try:
            response = await self.client.post(
                url,
                data=form_data,
                timeout=config.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            logger.info(f"Search request successful: {response.status_code}")
            result = response.json()
            if not result:
                logger.warning(
                    f"DSAPI returned empty response for {dataset}/{version} — "
                    "endpoint may be unavailable or dataset decommissioned"
                )
                return ApiError.create(
                    message=(
                        f"DSAPI returned empty response for {dataset}/{version} — "
                        "endpoint may be unavailable or dataset decommissioned"
                    ),
                    error_code="UPSTREAM_EMPTY",
                )
            return result

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            logger.error(f"HTTP error: {status_code} - {e.response.text}")
            try:
                error_json = e.response.json()
                return ApiError.from_http_error(
                    status_code=status_code,
                    response_text=e.response.text,
                    response_json=error_json,
                )
            except Exception:
                return ApiError.from_http_error(
                    status_code=status_code,
                    response_text=e.response.text,
                )

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            logger.warning(f"Network error (will retry): {str(e)}")
            raise  # Let tenacity handle the retry

        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return ApiError.from_exception(e, f"DSAPI search for {dataset}/{version} failed")

    async def close(self):
        """Close the client connections and clean up resources."""
        logger.info("Closing DSAPI client connections")
        await self.client.aclose()
