"""Live check that a request USPTO does not answer in time returns an error.

The request timeout is cut to one millisecond (both the client default and
the per-request setting the shared sender passes) so that a real request
to the real USPTO hosts times out on every attempt. Nothing is mocked: this
is the same path a slow 100-row search takes.
"""

import httpx
import pytest
from tenacity import wait_none

from patent_mcp_server.config import config
from patent_mcp_server.util import http as http_utils
from patent_mcp_server.uspto.api_uspto_gov import ApiUsptoClient
from patent_mcp_server.uspto.ppubs_uspto_gov import PpubsClient

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def no_retry_waits(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_TIMEOUT", 0.001)
    from patent_mcp_server.uspto.ppubs_uspto_gov import PpubsClient as Ppubs

    for retrying in (
        http_utils._send_with_retry,
        http_utils.request_bytes,
        Ppubs.make_request,
    ):
        monkeypatch.setattr(retrying.retry, "wait", wait_none())


def _starve(client):
    client.client = httpx.AsyncClient(headers=client.headers, timeout=0.001)
    return client


def _assert_timeout(result):
    assert isinstance(result, dict), f"Expected an error dict, got {result!r}"
    assert result.get("error") is True, result
    assert result["error_code"] == "TIMEOUT", result


async def test_odp_request_timeout_returns_error(no_retry_waits):
    client = _starve(ApiUsptoClient())
    try:
        result = await client.make_request(
            f"{config.API_BASE_URL}/api/v1/patent/applications/search?q=Raytheon"
        )
    finally:
        await client.client.aclose()
    _assert_timeout(result)


async def test_download_timeout_returns_error(no_retry_waits):
    client = _starve(ApiUsptoClient())
    try:
        result = await client.download_file(
            f"{config.API_BASE_URL}/api/v1/patent/applications/14412875/documents"
        )
    finally:
        await client.client.aclose()
    _assert_timeout(result)


async def test_ppubs_request_timeout_returns_error(no_retry_waits):
    client = _starve(PpubsClient())
    try:
        result = await client.make_request(
            "GET", f"{config.PPUBS_BASE_URL}/pubwebapp/"
        )
    finally:
        await client.client.aclose()
    _assert_timeout(result)
