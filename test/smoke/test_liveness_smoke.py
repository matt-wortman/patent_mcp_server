"""
Smoke test for check_api_status liveness probe.

Verifies that the tool runs and returns a well-formed response with all
four expected data sources present. Does NOT assert each source is alive —
a degraded upstream should not fail the smoke suite.

These tests make real HTTP calls to live USPTO endpoints.
Run with: uv run pytest -m smoke
Skip if USPTO_API_KEY is not set.
"""

import os
import pytest

if not os.getenv("USPTO_API_KEY"):
    pytest.skip("USPTO_API_KEY not set — skipping smoke tests", allow_module_level=True)

from patent_mcp_server.patents import check_api_status

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

EXPECTED_SOURCES = {"ppubs", "odp", "ptab", "dsapi"}


async def test_check_api_status_returns_all_sources():
    """check_api_status returns success and all four sources with an 'alive' key each."""
    result = await check_api_status()

    assert result.get("success") is True, f"Expected success=True, got: {result}"
    sources = result.get("results", {})
    assert isinstance(sources, dict), f"Expected 'results' to be a dict, got: {type(sources)}"

    missing = EXPECTED_SOURCES - set(sources.keys())
    assert not missing, f"Missing expected sources in results: {missing}"

    for source_name in EXPECTED_SOURCES:
        source_data = sources[source_name]
        assert "alive" in source_data, (
            f"Expected 'alive' key for source '{source_name}', got keys: {list(source_data.keys())}"
        )
