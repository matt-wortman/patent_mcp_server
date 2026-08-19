"""Unit tests for pure logic in util/http.py (no network).

The retry/429 loop itself crosses the network and is covered by live smoke
tests per the project's no-mocked-HTTP rule; only the header-parsing logic
is testable here.
"""

import httpx
import pytest

from patent_mcp_server.constants import Defaults
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
