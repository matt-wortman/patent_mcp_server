"""Every tool declares the result size Claude Code may show inline.

Claude Code saves any MCP text result over 50,000 characters to a file and
shows the agent only a file-path notice, unless the tool's tools/list entry
declares ``_meta["anthropic/maxResultSizeChars"]`` (hard ceiling 500,000).
"""
import asyncio

import pytest

from patent_mcp_server.patents import mcp

EXPECTED_TOOL_COUNT = 47


@pytest.mark.unit
def test_every_tool_declares_max_result_size():
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == EXPECTED_TOOL_COUNT
    missing = [
        tool.name
        for tool in tools
        if (tool.model_dump(by_alias=True).get("_meta") or {}).get(
            "anthropic/maxResultSizeChars"
        )
        != 500_000
    ]
    assert missing == []
