from __future__ import annotations

import asyncio
import json
import subprocess

from mobile_pilot_mcp import server as server_module
from mobile_pilot_mcp.simulator import SimulatorManager
from mobile_pilot_mcp.ui_tree import UIElement


def test_tap_rejects_negative_index(monkeypatch) -> None:
    class DummyClient:
        async def tap(self, x: int, y: int) -> None:
            raise AssertionError(f"tap should not be called: {x}, {y}")

    monkeypatch.setattr(server_module, "get_wda_client", lambda *_args, **_kwargs: DummyClient())
    server_module._last_ui_elements["test-device"] = [
        UIElement(index=0, element_type="Button", x=10, y=10, width=20, height=20),
    ]

    try:
        tap_tool = asyncio.run(server_module.mcp.get_tool("tap"))
        result = asyncio.run(tap_tool.fn(device_id="test-device", index=-1))
    finally:
        server_module._last_ui_elements.pop("test-device", None)

    assert result == "Invalid index -1. Max index is 0"


def test_list_devices_refresh_rebuilds_cache() -> None:
    async def run_test() -> None:
        manager = SimulatorManager()

        payloads = [
            {
                "devices": {
                    "com.apple.CoreSimulator.SimRuntime.iOS-17-4": [
                        {
                            "udid": "A-UDID",
                            "name": "iPhone 15",
                            "state": "Shutdown",
                            "isAvailable": True,
                        }
                    ]
                }
            },
            {"devices": {}},
        ]

        async def fake_run_simctl(*_args, **_kwargs):
            payload = payloads.pop(0)
            return subprocess.CompletedProcess(
                args=["xcrun", "simctl", "list", "devices", "-j"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )

        manager._run_simctl = fake_run_simctl  # type: ignore[method-assign]

        first = await manager.list_devices(refresh=True)
        assert len(first) == 1
        assert "A-UDID" in manager._devices_cache

        second = await manager.list_devices(refresh=True)
        assert second == []
        assert manager._devices_cache == {}

    asyncio.run(run_test())
