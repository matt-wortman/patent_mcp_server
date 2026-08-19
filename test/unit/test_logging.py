"""Unit tests for util/logging.py — header redaction and transport cleanup.

Pure internal logic: no network, no mocked upstream HTTP. The stub transport
below exists only to observe the wrapper's own behavior (what it logs, whether
it forwards close), not to simulate any USPTO API.
"""

import logging

import httpx
import pytest

from patent_mcp_server.util.logging import LoggingTransport, redact_headers


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# redact_headers
# ---------------------------------------------------------------------------

def test_redact_headers_masks_credential_headers():
    headers = {
        "X-API-KEY": "sentinel-api-key",
        "X-Access-Token": "sentinel-token",
        "Authorization": "Bearer sentinel",
        "Cookie": "session=sentinel",
        "Set-Cookie": "session=sentinel",
    }
    redacted = redact_headers(headers)
    for value in redacted.values():
        assert "sentinel" not in value
    assert set(redacted) == set(headers)


def test_redact_headers_preserves_harmless_headers():
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "patent-mcp-server/0.11.1",
    }
    assert redact_headers(headers) == headers


def test_redact_headers_masks_unknown_headers():
    # Allowlist semantics: a header we have never heard of must be masked,
    # so a new secret-bearing header can't leak by default.
    redacted = redact_headers({"X-Future-Secret": "sentinel"})
    assert "sentinel" not in redacted["X-Future-Secret"]


def test_redact_headers_is_case_insensitive():
    redacted = redact_headers({"x-api-key": "sentinel", "CONTENT-TYPE": "text/html"})
    assert "sentinel" not in redacted["x-api-key"]
    assert redacted["CONTENT-TYPE"] == "text/html"


# ---------------------------------------------------------------------------
# LoggingTransport
# ---------------------------------------------------------------------------

class _StubTransport(httpx.AsyncBaseTransport):
    """Inner transport stand-in: records calls, returns a canned response."""

    def __init__(self):
        self.closed = False

    async def handle_async_request(self, request):
        return httpx.Response(
            200,
            headers={"X-Access-Token": "response-sentinel"},
            request=request,
        )

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_logging_transport_redacts_credentials_in_debug_logs(caplog):
    transport = LoggingTransport(_StubTransport())
    request = httpx.Request(
        "GET",
        "https://api.uspto.gov/api/v1/test",
        headers={"X-API-KEY": "request-sentinel"},
    )
    with caplog.at_level(logging.DEBUG, logger="logging_transport"):
        await transport.handle_async_request(request)

    log_text = caplog.text
    assert "request-sentinel" not in log_text
    assert "response-sentinel" not in log_text
    # The header names should still appear so DEBUG logs stay useful.
    assert "x-api-key" in log_text.lower()


@pytest.mark.asyncio
async def test_logging_transport_aclose_closes_inner_transport():
    inner = _StubTransport()
    transport = LoggingTransport(inner)
    await transport.aclose()
    assert inner.closed is True
