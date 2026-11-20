from __future__ import annotations

import asyncio

from mobile_pilot_mcp import server as server_module
from mobile_pilot_mcp.wda_client import WDAError


class _SettingsClient:
    def __init__(self, fail: bool = False) -> None:
        self.settings: list[dict] = []
        self.fail = fail

    async def update_settings(self, settings: dict) -> dict:
        if self.fail:
            raise WDAError("unknown command")
        self.settings.append(settings)
        return settings


def test_fast_gestures_sets_timeout_to_zero():
    client = _SettingsClient()
    text = asyncio.run(server_module.apply_gesture_speed(client, True))
    assert client.settings == [{"animationCoolOffTimeout": 0.0}]
    assert "on" in text


def test_default_restores_wda_default():
    # The setting is global to WDA, so "off" must undo an earlier "on".
    client = _SettingsClient()
    text = asyncio.run(server_module.apply_gesture_speed(client, False))
    assert client.settings == [{"animationCoolOffTimeout": 2.0}]
    assert "off" in text


def test_unsupported_wda_does_not_fail_the_bridge():
    text = asyncio.run(server_module.apply_gesture_speed(_SettingsClient(fail=True), True))
    assert "could not be set" in text
