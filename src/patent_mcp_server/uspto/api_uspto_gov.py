"""
USPTO Open Data Portal (ODP) API Module (api.uspto.gov)

This module provides tools for accessing the USPTO Open Data Portal API at api.uspto.gov,
which provides metadata, continuity information, transactions, and assignment data
for patents and applications.

Note: Requires an ODP API key obtained from https://data.uspto.gov ("My ODP").
The API endpoint is api.uspto.gov; data.uspto.gov is the web portal only.
"""

from typing import Any, Optional, Dict
import logging
import urllib.parse

from patent_mcp_server.util.errors import ApiError
from patent_mcp_server.util.http import make_logged_client, request_json, request_bytes
from patent_mcp_server.config import config
from patent_mcp_server.constants import HTTPMethods

# Set up logging
logger = logging.getLogger('api_uspto_gov')


class ApiUsptoClient:
    """Client for the USPTO Open Data Portal (ODP) API at api.uspto.gov.

    This client provides access to patent and patent application metadata.
    Requires an ODP API key (register at https://data.uspto.gov).

    Supports context manager protocol for proper resource cleanup.
    """

    def __init__(self):
        self.headers = {
            "User-Agent": config.USER_AGENT,
            "X-API-KEY": config.USPTO_API_KEY if config.USPTO_API_KEY else ""
        }
        self.client = make_logged_client(self.headers)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.close()

    def build_query_string(self, params: Dict[str, Any]) -> str:
        """Build a query string from a dictionary of parameters.

        Args:
            params: Dictionary of query parameters

        Returns:
            URL-encoded query string
        """
        query_parts = []
        for key, value in params.items():
            if value is None:
                continue

            if isinstance(value, bool):
                value = str(value).lower()
            elif isinstance(value, (list, tuple)):
                value = ",".join(str(v) for v in value)

            query_parts.append(f"{key}={urllib.parse.quote(str(value))}")

        return "&".join(query_parts)

    async def make_request(
        self,
        url: str,
        method: str = HTTPMethods.GET,
        data: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Make a request to the USPTO API with retry, 429, and error handling.

        Args:
            url: Request URL
            method: HTTP method (GET or POST)
            data: Request body data for POST requests

        Returns:
            Response JSON dictionary or error dictionary
        """
        method = method.upper()
        if method not in (HTTPMethods.GET, HTTPMethods.POST):
            logger.error(f"Unsupported HTTP method: {method}")
            return ApiError.create(
                message=f"Unsupported HTTP method: {method}",
                status_code=400
            )

        logger.info(f"Making {method} request to {url}")
        return await request_json(
            self.client,
            method,
            url,
            json_body=data if method == HTTPMethods.POST else None,
            context=f"Request to {url} failed",
        )

    async def download_file(self, url: str) -> Dict[str, Any]:
        """Download a file from the USPTO API and return raw bytes.

        Uses the same X-API-KEY authentication as make_request but handles
        binary content (PDFs, DOCX files) instead of JSON. Downloads are
        rate-limited upstream to 4 requests/minute.

        Args:
            url: Full download URL (from odp_get_documents downloadUrl field)

        Returns:
            Dict with 'content' (bytes), 'content_type', and 'size_bytes' on success,
            or error dict on failure.
        """
        logger.info(f"Downloading file from {url}")
        return await request_bytes(
            self.client,
            url,
            context=f"Download from {url} failed",
        )

    async def close(self):
        """Close the client connections and clean up resources."""
        logger.info("Closing api.uspto.gov client connections")
        await self.client.aclose()
