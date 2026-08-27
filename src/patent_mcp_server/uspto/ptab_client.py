"""
USPTO PTAB API Client (Patent Trial and Appeal Board)

This module provides access to the PTAB API v3 at api.uspto.gov for accessing
Patent Trial and Appeal Board data: trial proceedings (IPR, PGR, CBM,
derivation), trial decisions, documents filed in trials, ex parte appeal
decisions, and interference decisions.

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
        # Appeals and interferences live under their own base paths,
        # NOT under /trials (live API layout, verified 2026-08-19).
        self.appeals_url = f"{config.API_BASE_URL}/api/v1/patent/appeals"
        self.interferences_url = f"{config.API_BASE_URL}/api/v1/patent/interferences"
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
        data: Optional[Dict[str, Any]] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Make a request to the PTAB API.

        Args:
            endpoint: API endpoint path
            method: HTTP method
            params: Query parameters for GET requests
            data: JSON body for POST requests
            base_url: Base URL override (defaults to the /trials base)

        Returns:
            Response JSON dictionary or error dictionary
        """
        url = f"{base_url or self.base_url}{endpoint}"
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

        Filters are compiled into the ODP DSL ``q`` parameter as
        field:value clauses joined with " AND " — the live API silently
        ignores bespoke query parameters (verified 2026-08-27).

        Args:
            query: Free-form query or field:value clauses (ODP DSL)
            trial_type: Type of trial (IPR, PGR, CBM, DER); built as a
                trialMetaData.trialTypeCode:<value> clause
            patent_number: Patent number involved in the proceeding;
                built as a patentOwnerData.patentNumber:<value> clause
            party_name: Real-party-in-interest name, matched against
                petitioner OR patent owner (quoted phrase)
            filing_date_from: Petition filing date range start (YYYY-MM-DD)
            filing_date_to: Petition filing date range end (YYYY-MM-DD)
            status: Proceeding status (e.g., Pending, Instituted,
                Terminated, Final Written Decision); built as a
                trialMetaData.trialStatusCategory clause
            offset: Starting position for pagination
            limit: Maximum results to return

        Returns:
            Dictionary containing search results
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}

        q_parts = []
        if query:
            q_parts.append(query)
        if trial_type and trial_type in PTABTrialTypes.ALL:
            q_parts.append(f"trialMetaData.trialTypeCode:{trial_type}")
        if patent_number:
            q_parts.append(f"patentOwnerData.patentNumber:{patent_number}")
        if party_name:
            name = party_name.replace('"', " ").strip()
            q_parts.append(
                f'(regularPetitionerData.realPartyInInterestName:"{name}"'
                f' OR patentOwnerData.realPartyInInterestName:"{name}")'
            )
        if filing_date_from or filing_date_to:
            q_parts.append(
                "trialMetaData.petitionFilingDate:"
                f"[{filing_date_from or '*'} TO {filing_date_to or '*'}]"
            )
        if status:
            status_value = status.replace('"', " ").strip()
            q_parts.append(f'trialMetaData.trialStatusCategory:"{status_value}"')
        if q_parts:
            params["q"] = " AND ".join(q_parts)

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

        Filters are compiled into the ODP DSL ``q`` parameter as
        field:value clauses joined with " AND " — the live API silently
        ignores bespoke query parameters (verified 2026-08-27).

        Args:
            query: Free-form query or field:value clauses (ODP DSL)
            decision_type: Decision type prefix (institution, final,
                termination); built as a documentTypeDescriptionText
                prefix-wildcard clause
            proceeding_number: Trial number (e.g., IPR2023-00037); built
                as a trialNumber:<value> clause
            patent_number: Patent number; built as a
                patentOwnerData.patentNumber:<value> clause
            decision_date_from: Decision filing date range start (YYYY-MM-DD)
            decision_date_to: Decision filing date range end (YYYY-MM-DD)
            offset: Starting position for pagination
            limit: Maximum results to return

        Returns:
            Dictionary containing decision search results
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}

        q_parts = []
        if query:
            q_parts.append(query)
        if decision_type:
            type_prefix = decision_type.split()[0] if decision_type.split() else ""
            if type_prefix:
                q_parts.append(
                    f"documentData.documentTypeDescriptionText:{type_prefix}*"
                )
        if proceeding_number:
            q_parts.append(f"trialNumber:{proceeding_number}")
        if patent_number:
            q_parts.append(f"patentOwnerData.patentNumber:{patent_number}")
        if decision_date_from or decision_date_to:
            q_parts.append(
                "documentData.documentFilingDate:"
                f"[{decision_date_from or '*'} TO {decision_date_to or '*'}]"
            )
        if q_parts:
            params["q"] = " AND ".join(q_parts)

        return await self._make_request("/decisions/search", params=params)

    async def get_decision(self, decision_id: str) -> Dict[str, Any]:
        """Get details of a specific PTAB decision.

        Args:
            decision_id: The decision identifier

        Returns:
            Dictionary containing decision details
        """
        return await self._make_request(f"/decisions/{decision_id}")

    async def search_documents(
        self,
        query: Optional[str] = None,
        proceeding_number: Optional[str] = None,
        offset: int = Defaults.SEARCH_START,
        limit: int = Defaults.API_LIMIT,
    ) -> Dict[str, Any]:
        """Search documents filed in PTAB trial proceedings.

        Args:
            query: Free-form query or field:value clauses (ODP DSL)
            proceeding_number: Restrict to one trial (e.g., IPR2023-00037);
                built as a trialNumber:<value> clause
            offset: Starting position for pagination
            limit: Maximum results to return

        Returns:
            Dictionary containing document search results
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}

        q_parts = []
        if query:
            q_parts.append(query)
        if proceeding_number:
            q_parts.append(f"trialNumber:{proceeding_number}")
        if q_parts:
            params["q"] = " AND ".join(q_parts)

        return await self._make_request("/documents/search", params=params)

    async def get_proceeding_documents(self, proceeding_number: str) -> Dict[str, Any]:
        """Get all documents filed in one PTAB proceeding.

        Args:
            proceeding_number: The proceeding number (e.g., IPR2023-00037)

        Returns:
            Dictionary containing the proceeding's documents
        """
        return await self._make_request(f"/{proceeding_number}/documents")

    async def search_appeal_decisions(
        self,
        query: Optional[str] = None,
        appeal_number: Optional[str] = None,
        app_num: Optional[str] = None,
        offset: int = Defaults.SEARCH_START,
        limit: int = Defaults.API_LIMIT,
    ) -> Dict[str, Any]:
        """Search PTAB ex parte appeal decisions.

        Args:
            query: Free-form query or field:value clauses (ODP DSL)
            appeal_number: Restrict to one appeal (e.g., 2026002664)
            app_num: Restrict to one application number
            offset: Starting position for pagination
            limit: Maximum results to return

        Returns:
            Dictionary containing appeal decision search results
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}

        q_parts = []
        if query:
            q_parts.append(query)
        if appeal_number:
            q_parts.append(f"appealNumber:{appeal_number}")
        if app_num:
            q_parts.append(f"appellantData.applicationNumberText:{app_num}")
        if q_parts:
            params["q"] = " AND ".join(q_parts)

        return await self._make_request(
            "/decisions/search", params=params, base_url=self.appeals_url
        )

    async def get_appeal_decisions(self, appeal_number: str) -> Dict[str, Any]:
        """Get decisions for one PTAB ex parte appeal.

        Args:
            appeal_number: The appeal number (e.g., 2026002664)

        Returns:
            Dictionary containing the appeal's decisions
        """
        return await self._make_request(
            f"/{appeal_number}/decisions", base_url=self.appeals_url
        )

    async def search_interference_decisions(
        self,
        query: Optional[str] = None,
        interference_number: Optional[str] = None,
        offset: int = Defaults.SEARCH_START,
        limit: int = Defaults.API_LIMIT,
    ) -> Dict[str, Any]:
        """Search PTAB interference decisions.

        Args:
            query: Free-form query or field:value clauses (ODP DSL)
            interference_number: Restrict to one interference (e.g., 106130)
            offset: Starting position for pagination
            limit: Maximum results to return

        Returns:
            Dictionary containing interference decision search results
        """
        params: Dict[str, Any] = {"offset": offset, "limit": limit}

        q_parts = []
        if query:
            q_parts.append(query)
        if interference_number:
            q_parts.append(f"interferenceNumber:{interference_number}")
        if q_parts:
            params["q"] = " AND ".join(q_parts)

        return await self._make_request(
            "/decisions/search", params=params, base_url=self.interferences_url
        )

    async def get_interference_decisions(self, interference_number: str) -> Dict[str, Any]:
        """Get decisions for one PTAB interference.

        Args:
            interference_number: The interference number (e.g., 106130)

        Returns:
            Dictionary containing the interference's decisions
        """
        return await self._make_request(
            f"/{interference_number}/decisions", base_url=self.interferences_url
        )

    async def close(self):
        """Close the client connections."""
        logger.info("Closing PTAB client connections")
        await self.client.aclose()
