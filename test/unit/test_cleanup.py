"""Unit tests for server shutdown cleanup.

cleanup() runs once from the server lifespan (on the live event loop) and
again from the atexit fallback — the second call must be a no-op so clients
are not closed twice on a possibly-dead loop.
"""

import pytest

import patent_mcp_server.patents as patents


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_cleanup_closes_each_client_only_once(monkeypatch):
    calls = {"count": 0}

    async def counting_close():
        calls["count"] += 1

    for client in (patents.ppubs_client, patents.api_client,
                   patents.ptab_client, patents.dsapi_client):
        monkeypatch.setattr(client, "close", counting_close)
    monkeypatch.setattr(patents, "_cleanup_done", False)

    await patents.cleanup()
    assert calls["count"] == 4

    await patents.cleanup()
    assert calls["count"] == 4, "Second cleanup() call must not re-close clients"
