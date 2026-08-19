"""Unit tests for DSAPI client pagination bounds (IR-05).

Pure internal logic: invalid pagination must be rejected at the client
boundary BEFORE any HTTP request is attempted, so these tests never touch
the network.
"""

import pytest

from patent_mcp_server.uspto.dsapi_client import DsapiClient, DSAPI_MAX_ROWS


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def client():
    c = DsapiClient()
    yield c
    await c.close()


async def test_search_rejects_negative_start(client):
    result = await client.search("oa_rejections", "v2", start=-1)
    assert result.get("error") is True
    assert "start" in result.get("message", "")


async def test_search_rejects_nonpositive_rows(client):
    result = await client.search("oa_rejections", "v2", rows=0)
    assert result.get("error") is True
    assert "rows" in result.get("message", "")


async def test_search_rejects_rows_over_cap(client):
    result = await client.search("oa_rejections", "v2", rows=DSAPI_MAX_ROWS + 1)
    assert result.get("error") is True
    assert "rows" in result.get("message", "")
