"""
USPTO PTAB API Client (Patent Trial and Appeal Board)

This module provides access to the PTAB API v3 at api.uspto.gov for accessing
Patent Trial and Appeal Board data: trial proceedings (IPR, PGR, CBM,
derivation) and trial decisions.

Note: PTAB API v3 has been migrated to the Open Data Portal (ODP).
Requires an ODP API key obtained from https://data.uspto.gov ("My ODP").
"""

import logging
from typing import Any, Optional, Dict

from patent_mcp_server.util.http import make_logged_client, request_json
from patent_mcp_server.config import config
from patent_mcp_server.constants import HTTPMethods, Defaults, PTABTrialTypes

logger = logging.getLogger('ptab_client')


class PTABClient:
    """Client for the USPTO PTAB API v3.

    Provides access to Patent Trial and Appeal Board data:
    - Trial proceedings (IPR, PGR, CBM, derivation)
    - Trial decisions (institution, final written decisions, terminations)
    """

    def __init__(self):
        self.base_url = f"{config.API_BASE_URL}/api/v1/patent/trials"
        self.headers = {
            "User-Agent": config.USER_AGENT,
            "X-API-KEY": config.USPTO_API_KEY if config.USPTO_API_KEY else "",
            "Accept": "application/json",
        }

        self.client = make_logged_client(self.headers)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _make_request(
        self,
        endpoint: str,
        method: str = HTTPMethods.GET,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make a request to the PTAB API.

        Args:
            endpoint: API endpoint path
            method: HTTP method
            params: Query parameters for GET requests
            data: JSON body for POST requests

        Returns:
            Response JSON dictionary or error dictionary
        """
        url = f"{self.base_url}{endpoint}"
        logger.info(f"Making {method} request to {url}")
        return await request_json(
            self.client,
            method,
            url,
            params=params if method == HTTPMethods.GET else None,
            json_body=data if method != HTTPMethods.GET else None,
            context="PTAB API request failed",
        )

    async def search_proceedings(
        self,
        query: Optional[str] = None,
        trial_type: Optional[str] = None,
        patent_number: Optional[str] = None,
        party_name: Optional[str] = None,
        filing_date_from: Optional[str] = None,
        filing_date_to: Optional[str] = None,
        status: Optional[str] = None,
        offset: int = Defaults.SEARCH_START,
        limit: int = Defaults.API_LIMIT,
    ) -> Dict[str, Any]:
        """Search PTAB trial proceedings.

        Args:
            query: Full-text search query
            trial_type: Type of trial (IPR, PGR, CBM, DER)
            patent_number: Patent number involved in the proceeding
            party_name: Name of petitioner or patent owner
            filing_date_from: Filing date range start (YYYY-MM-DD)
            filing_date_to: Filing date range end (YYYY-MM-DD)
            status: Proceeding status
            offset: Starting position for pagination
            limit: Maximum results to return

        Returns:
            Dictionary containing search results
        """
        params: Dict[str, Any] = {
            "offset": offset,
            "limit": limit,
        }

        if query:
            params["q"] = query
        if trial_type and trial_type in PTABTrialTypes.ALL:
            params["trialType"] = trial_type
        if patent_number:
            params["patentNumber"] = patent_number
        if party_name:
            params["partyName"] = party_name
        if filing_date_from:
            params["filingDateFrom"] = filing_date_from
        if filing_date_to:
            params["filingDateTo"] = filing_date_to
        if status:
            params["status"] = status

        return await self._make_request("/proceedings/search", params=params)

    async def get_proceeding(self, proceeding_number: str) -> Dict[str, Any]:
        """Get details of a specific PTAB proceeding.

        Args:
            proceeding_number: The proceeding number (e.g., IPR2023-00001)

        Returns:
            Dictionary containing proceeding details
        """
        return await self._make_request(f"/proceedings/{proceeding_number}")

    async def search_decisions(
        self,
        query: Optional[str] = None,
        decision_type: Optional[str] = None,
        proceeding_number: Optional[str] = None,
        patent_number: Optional[str] = None,
        decision_date_from: Optional[str] = None,
        decision_date_to: Optional[str] = None,
        offset: int = Defaults.SEARCH_START,
        limit: int = Defaults.API_LIMIT,
    ) -> Dict[str, Any]:
        """Search PTAB trial decisions.

        Args:
            query: Full-text search in decision documents
            decision_type: Type of decision (institution, final, termination)
            proceeding_number: Proceeding number
            patent_number: Patent number
            decision_date_from: Decision date range start (YYYY-MM-DD)
            decision_date_to: Decision date range end (YYYY-MM-DD)
            offset: Starting position for pagination
            limit: Maximum results to return

        Returns:
            Dictionary containing decision search results
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}

        if query:
            params["q"] = query
        if decision_type:
            params["decisionType"] = decision_type
        if proceeding_number:
            params["proceedingNumber"] = proceeding_number
        if patent_number:
            params["patentNumber"] = patent_number
        if decision_date_from:
            params["decisionDateFrom"] = decision_date_from
        if decision_date_to:
            params["decisionDateTo"] = decision_date_to

        return await self._make_request("/decisions/search", params=params)

    async def get_decision(self, decision_id: str) -> Dict[str, Any]:
        """Get details of a specific PTAB decision.

        Args:
            decision_id: The decision identifier

        Returns:
            Dictionary containing decision details
        """
        return await self._make_request(f"/decisions/{decision_id}")

    async def close(self):
        """Close the client connections."""
        logger.info("Closing PTAB client connections")
        await self.client.aclose()
