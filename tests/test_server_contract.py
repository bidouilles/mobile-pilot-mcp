from __future__ import annotations

import asyncio

from mobile_pilot_mcp import server as server_module


def test_server_compat_export_points_to_mcp() -> None:
    """The legacy `server` export should continue to reference the FastMCP instance."""
    assert server_module.server is server_module.mcp


def test_fastmcp_tools_registered() -> None:
    tools = asyncio.run(server_module.mcp.list_tools())
    tool_names = {tool.name for tool in tools}

    expected_tools = {
        "list_devices",
        "start_bridge",
        "get_screenshot",
        "tap",
        "discover_dtd_uris",
        "wait_for_element",
        "get_app_state",
        "install_app",
        "uninstall_app",
        "set_orientation",
        "send_push_notification",
        "add_media",
        "clear_keychain",
    }
    assert expected_tools.issubset(tool_names)

    start_bridge_tool = asyncio.run(server_module.mcp.get_tool("start_bridge"))
    assert start_bridge_tool.name == "start_bridge"
    assert start_bridge_tool.description


def test_fastmcp_resources_registered() -> None:
    resources = asyncio.run(server_module.mcp.list_resources())
    resource_keys = {str(resource.uri) for resource in resources}

    assert "mobile-pilot://api-reference" in resource_keys
    assert "mobile-pilot://automation-guide" in resource_keys


def test_dashboard_middleware_tracks_tool_calls(monkeypatch) -> None:
    from types import SimpleNamespace

    tracked: list[tuple] = []
    monkeypatch.setattr(server_module, "_track_tool_call", lambda name, args: (name, args))
    monkeypatch.setattr(
        server_module,
        "_complete_tool_call",
        lambda call, result=None, error=None: tracked.append((call, result, error)),
    )
    middleware = server_module.DashboardTrackingMiddleware()
    context = SimpleNamespace(message=SimpleNamespace(name="tap", arguments={"x": 1}))

    async def ok(_context):
        return SimpleNamespace(content=[SimpleNamespace(text="Tapped")])

    async def boom(_context):
        raise RuntimeError("no WDA")

    asyncio.run(middleware.on_call_tool(context, ok))
    try:
        asyncio.run(middleware.on_call_tool(context, boom))
    except RuntimeError:
        pass

    assert tracked == [
        (("tap", {"x": 1}), "Tapped", None),
        (("tap", {"x": 1}), None, "no WDA"),
    ]
