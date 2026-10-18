"""Start the server as a subprocess over stdio and list its tools (no network needed)."""

import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.conftest import run

EXPECTED = {"search", "get_page", "get_page_section", "get_page_citations"}


def test_stdio_lists_tools():
    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "grokipedia_mcp_server"])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools = await s.list_tools()
                return init, tools

    init, tools = run(go())
    assert init.serverInfo.name == "grokipedia"
    assert {t.name for t in tools.tools} == EXPECTED
    assert all(t.annotations and t.annotations.readOnlyHint for t in tools.tools)
