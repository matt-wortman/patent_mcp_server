"""Smoke-suite fixtures.

Closes the module-level API clients on the live session event loop after
the last smoke test. Without this, the only close happens in the atexit
fallback on a fresh loop, where pools created during the tests cannot be
closed ("Event loop is closed").
"""

import pytest_asyncio

from patent_mcp_server.patents import cleanup


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _close_clients_after_session():
    yield
    await cleanup()
