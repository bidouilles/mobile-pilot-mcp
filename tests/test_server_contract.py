from __future__ import annotations

import asyncio

from ios_simulator_mcp import server as server_module


def test_server_compat_export_points_to_mcp() -> None:
    """The legacy `server` export should continue to reference the FastMCP instance."""
    assert server_module.server is server_module.mcp


def test_fastmcp_tools_registered() -> None:
    tools = asyncio.run(server_module.mcp.get_tools())

    expected_tools = {
        "list_devices",
        "start_bridge",
        "get_screenshot",
        "tap",
        "discover_dtd_uris",
    }
    assert expected_tools.issubset(tools.keys())

    start_bridge_tool = asyncio.run(server_module.mcp.get_tool("start_bridge"))
    assert start_bridge_tool.name == "start_bridge"
    assert start_bridge_tool.description


def test_fastmcp_resources_registered() -> None:
    resources = asyncio.run(server_module.mcp.get_resources())
    assert "ios-sim://api-reference" in resources
    assert "ios-sim://automation-guide" in resources
