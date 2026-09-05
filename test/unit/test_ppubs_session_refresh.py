"""Unit tests for PPUBS session renewal inside ``make_request``.

No network. A fake USPTO is stood up with ``httpx.MockTransport``.

Background (observed 2026-09-04): after a long sweep every ppubs call came
back ``401 unauthorized`` while ``check_api_status`` said the site was up.
The USPTO issued a fresh token on request, so the machine was not blocked;
the client simply kept sending its expired token because it only renewed
the session on a 403. A 401 must trigger the same renewal.
"""

import httpx
import pytest

from patent_mcp_server.config import config
from patent_mcp_server.uspto.ppubs_uspto_gov import PpubsClient

pytestmark = pytest.mark.unit

OLD_TOKEN = "old-expired-token"
NEW_TOKEN = "new-fresh-token"
SEARCH_URL = f"{config.PPUBS_BASE_URL}/api/searches/counts"


def _fake_uspto(rejection_status: int):
    """Build a MockTransport that rejects OLD_TOKEN with ``rejection_status``.

    Returns the handler's call log alongside the transport so a test can
    assert on what was actually sent upstream.
    """
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("X-Access-Token")))

        if request.url.path == "/pubwebapp/":
            return httpx.Response(200, text="<html></html>")

        if request.url.path == "/api/users/me/session":
            return httpx.Response(
                200,
                json={"userCase": {"caseId": "case-2"}},
                headers={"X-Access-Token": NEW_TOKEN},
            )

        token = request.headers.get("X-Access-Token")
        if token == NEW_TOKEN:
            return httpx.Response(200, json={"numResults": 1})
        return httpx.Response(rejection_status, json={"error": "rejected"})

    return httpx.MockTransport(handler), calls


async def _client_with_stale_token(transport: httpx.MockTransport) -> PpubsClient:
    client = PpubsClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=transport, headers=client.headers)
    client.access_token = OLD_TOKEN
    client.case_id = "case-1"
    return client


async def test_401_renews_session_and_retries_with_new_token():
    transport, calls = _fake_uspto(rejection_status=401)
    client = await _client_with_stale_token(transport)
    try:
        response = await client.make_request("POST", SEARCH_URL, json={"q": "x"})
    finally:
        await client.close()

    assert isinstance(response, httpx.Response)
    assert response.status_code == 200
    assert client.access_token == NEW_TOKEN
    assert client.case_id == "case-2"
    # First attempt with the stale token, session renewal, then a retry
    # signed with the fresh token.
    assert calls[0] == ("POST", "/api/searches/counts", OLD_TOKEN)
    assert ("POST", "/api/users/me/session", "null") in calls
    assert calls[-1] == ("POST", "/api/searches/counts", NEW_TOKEN)


async def test_403_renews_session_and_retries_with_new_token():
    # Control: the pre-existing 403 behavior must keep working unchanged.
    transport, calls = _fake_uspto(rejection_status=403)
    client = await _client_with_stale_token(transport)
    try:
        response = await client.make_request("POST", SEARCH_URL, json={"q": "x"})
    finally:
        await client.close()

    assert isinstance(response, httpx.Response)
    assert response.status_code == 200
    assert client.access_token == NEW_TOKEN
    assert calls[-1] == ("POST", "/api/searches/counts", NEW_TOKEN)
