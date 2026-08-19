"""
USPTO Data Set API (DSAPI) Client (api.uspto.gov — Open Data Portal)

This module provides a generic client for accessing the USPTO DSAPI datasets
after their April 20, 2026 migration from developer.uspto.gov/ds-api to the
Open Data Portal at api.uspto.gov. All datasets share a uniform interface:

- Search: POST /api/v1/patent/oa/{dataset}/{version}/records (form-encoded)

Authentication: requires X-API-KEY header (USPTO_API_KEY, same key used by
the odp_* tools).

Datasets (all confirmed live on api.uspto.gov, verified 2026-08-19):
- oa_actions/v1 — Office action full text
- oa_citations/v2 — Office action citations
- oa_rejections/v2 — Office action rejections
- enriched_cited_reference_metadata/v3 — Enriched cited reference metadata
- oce_patent_litigation_cases/v1 — Patent litigation cases
- oce_patent_examination_status_codes/v1 — Examination status codes
"""

from typing import Any, Dict
import logging

from patent_mcp_server.util.errors import ApiError
from patent_mcp_server.util.http import make_logged_client, request_json
from patent_mcp_server.config import config
from patent_mcp_server.constants import HTTPMethods

# Set up logging
logger = logging.getLogger('dsapi_client')

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

        self.client = make_logged_client(self.headers)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.close()

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
        url = f"{config.API_BASE_URL}{DSAPI_PATH_PREFIX}/{dataset}/{version}/records"
        logger.info(f"Searching {dataset}/{version}: criteria={criteria}, start={start}, rows={rows}")

        form_data = {
            "criteria": criteria,
            "start": str(start),
            "rows": str(rows),
        }

        result = await request_json(
            self.client,
            HTTPMethods.POST,
            url,
            form_data=form_data,
            context=f"DSAPI search for {dataset}/{version} failed",
        )

        if not result:
            message = (
                f"DSAPI returned empty response for {dataset}/{version} — "
                "endpoint may be unavailable or dataset decommissioned"
            )
            logger.warning(message)
            return ApiError.create(message=message, error_code="UPSTREAM_EMPTY")
        return result

    async def close(self):
        """Close the client connections and clean up resources."""
        logger.info("Closing DSAPI client connections")
        await self.client.aclose()
