"""Unit tests for pure logic in util/http.py (no network).

The retry/429 loop itself crosses the network and is covered by live smoke
tests per the project's no-mocked-HTTP rule; only the header-parsing logic
is testable here.
"""

import httpx
import pytest

from patent_mcp_server.constants import Defaults
from patent_mcp_server.util import http as http_utils
from patent_mcp_server.util.http import rate_limit_delay, MAX_RATE_LIMIT_SLEEP


def _response(headers=None):
    return httpx.Response(429, headers=headers or {})


@pytest.mark.unit
class TestRateLimitDelay:
    def test_reads_retry_after_header(self):
        assert rate_limit_delay(_response({"Retry-After": "17"}), attempt=0) == 17.0

    def test_falls_back_to_uspto_header(self):
        resp = _response({"x-rate-limit-retry-after-seconds": "8"})
        assert rate_limit_delay(resp, attempt=0) == 8.0

    def test_retry_after_wins_over_uspto_header(self):
        resp = _response({
            "Retry-After": "3",
            "x-rate-limit-retry-after-seconds": "40",
        })
        assert rate_limit_delay(resp, attempt=0) == 3.0

    def test_default_grows_with_attempt_when_headers_absent(self):
        assert rate_limit_delay(_response(), attempt=0) == Defaults.RATE_LIMIT_RETRY_DELAY
        assert rate_limit_delay(_response(), attempt=2) == Defaults.RATE_LIMIT_RETRY_DELAY * 3

    def test_unparseable_header_falls_through_to_default(self):
        resp = _response({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert rate_limit_delay(resp, attempt=0) == Defaults.RATE_LIMIT_RETRY_DELAY

    def test_header_delay_is_clamped_to_cap(self):
        resp = _response({"Retry-After": "600"})
        assert rate_limit_delay(resp, attempt=0) == MAX_RATE_LIMIT_SLEEP

    def test_default_delay_is_clamped_to_cap(self):
        # A huge attempt count must not produce an unbounded sleep either
        assert rate_limit_delay(_response(), attempt=1000) == MAX_RATE_LIMIT_SLEEP


@pytest.mark.unit
def test_cross_origin_redirect_strips_api_credentials():
    """A signed USPTO redirect must not forward credentials to another host."""
    next_url, headers = http_utils.prepare_safe_redirect(
        "https://api.uspto.gov/download/file",
        "https://data-documents.uspto.gov/signed/file",
        {
            "x-api-key": "secret-key",
            "Authorization": "Bearer secret-token",
            "User-Agent": "test-agent",
        },
        allowed_hosts={"api.uspto.gov", "data-documents.uspto.gov"},
    )

    assert next_url == "https://data-documents.uspto.gov/signed/file"
    assert "secret-key" not in headers.values()
    assert "Bearer secret-token" not in headers.values()
    assert headers["X-API-KEY"] == ""
    assert headers["Authorization"] == ""
    assert headers["User-Agent"] == "test-agent"
    with httpx.Client(headers={"X-API-KEY": "client-default-secret"}) as client:
        redirected_request = client.build_request("GET", next_url, headers=headers)
    assert redirected_request.headers["X-API-KEY"] == ""


@pytest.mark.unit
def test_redirect_to_unapproved_host_is_rejected():
    """A compromised USPTO redirect must not create an SSRF request."""
    with pytest.raises(ValueError, match="unapproved host"):
        http_utils.prepare_safe_redirect(
            "https://api.uspto.gov/download/file",
            "https://evil.example.com/collect",
            {"X-API-KEY": "secret-key"},
            allowed_hosts={"api.uspto.gov", "data-documents.uspto.gov"},
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_binary_response_reader_stops_at_size_limit():
    """A single offered media file must not be buffered past its safety bound."""
    reader = getattr(http_utils, "read_bounded_response", None)
    assert reader is not None, "bounded response reader is required"
    response = httpx.Response(200, content=b"01234567890")

    result = await reader(response, max_bytes=10)

    assert result["error"] is True
    assert result["error_code"] == "DOWNLOAD_TOO_LARGE"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_streamed_error_reader_returns_only_bounded_prefix():
    """A hostile HTTP error body must not be read into memory without a bound."""
    reader = getattr(http_utils, "read_response_prefix", None)
    assert reader is not None, "bounded error-prefix reader is required"
    response = httpx.Response(500, content=b"x" * 100)

    prefix = await reader(response, max_bytes=10)

    assert prefix == b"x" * 10
