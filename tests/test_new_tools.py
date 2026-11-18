from __future__ import annotations

import asyncio

from mobile_pilot_mcp import server as server_module


def _node(type_: str, label: str | None = None) -> dict:
    return {
        "type": type_,
        "label": label,
        "isVisible": "1",
        "rect": {"x": 0, "y": 0, "width": 100, "height": 40},
        "children": [],
    }


class _SourceClient:
    """Serves one page source per call, repeating the last one."""

    def __init__(self, sources: list[dict]) -> None:
        self.sources = sources
        self.calls = 0

    async def get_source(self, format: str = "json") -> dict:
        source = self.sources[min(self.calls, len(self.sources) - 1)]
        self.calls += 1
        return source


def _call(name: str, **kwargs) -> str:
    async def run() -> str:
        tool = await server_module.mcp.get_tool(name)
        return await tool.fn(**kwargs)

    return asyncio.run(run())


EMPTY = {**_node("Application"), "children": []}
WITH_DONE = {**_node("Application"), "children": [_node("Button", "Done")]}


def test_wait_for_element_polls_until_found_and_caches_indices(monkeypatch) -> None:
    client = _SourceClient([EMPTY, EMPTY, WITH_DONE])
    monkeypatch.setattr(server_module, "get_wda_client", lambda *_a, **_k: client)
    monkeypatch.setattr(server_module, "_last_ui_elements", {})

    result = _call("wait_for_element", device_id="dev", predicate={"text": "Done"}, timeout=5)

    assert result.startswith('Found [1] Button "Done" at (50, 20)')
    assert client.calls == 3
    assert server_module._last_ui_elements["dev"][1].label == "Done"


def test_wait_for_element_times_out(monkeypatch) -> None:
    client = _SourceClient([EMPTY])
    monkeypatch.setattr(server_module, "get_wda_client", lambda *_a, **_k: client)

    result = _call("wait_for_element", device_id="dev", predicate={"text": "Done"}, timeout=0)

    assert result.startswith("Timed out") and "not found" in result


def test_wait_for_element_gone(monkeypatch) -> None:
    client = _SourceClient([WITH_DONE, EMPTY])
    monkeypatch.setattr(server_module, "get_wda_client", lambda *_a, **_k: client)

    result = _call(
        "wait_for_element", device_id="dev", predicate={"text": "Done"}, timeout=5, gone=True
    )

    assert result.startswith("Element gone")


def test_send_push_notification_builds_payload_from_title_and_body(monkeypatch) -> None:
    sent: list[tuple] = []

    async def fake_push(udid, bundle_id, payload):
        sent.append((udid, bundle_id, payload))

    monkeypatch.setattr(server_module.simulator_manager, "push_notification", fake_push)

    _call("send_push_notification", device_id="dev", bundle_id="a.b", title="Hi", body="There")
    assert sent == [("dev", "a.b", {"aps": {"alert": {"title": "Hi", "body": "There"}}})]

    assert "provide" in _call("send_push_notification", device_id="dev", bundle_id="a.b")
    assert len(sent) == 1


def test_install_app_reports_a_missing_path(monkeypatch, tmp_path) -> None:
    async def fail(*_a, **_k):
        raise AssertionError("simctl should not be called")

    monkeypatch.setattr(server_module.simulator_manager, "install_app", fail)

    result = _call("install_app", device_id="dev", app_path=str(tmp_path / "Missing.app"))

    assert result.startswith("App not found")


def test_screenshot_is_rotated_for_landscape_without_start_bridge(monkeypatch, tmp_path) -> None:
    from PIL import Image

    class LandscapeClient:
        async def get_orientation(self) -> str:
            return "LANDSCAPE"

    async def fake_screenshot(_udid, path):
        Image.new("RGB", (40, 80), "white").save(path)

    monkeypatch.setattr(server_module, "SCREENSHOT_DIR", tmp_path)
    monkeypatch.setattr(server_module, "wda_clients", {})
    monkeypatch.setattr(server_module, "get_wda_client", lambda *_a, **_k: LandscapeClient())
    monkeypatch.setattr(server_module.simulator_manager, "screenshot", fake_screenshot)

    result = _call("get_screenshot", device_id="dev", scale=1.0, format="png")

    with Image.open(result.splitlines()[0].split(": ", 1)[1]) as img:
        assert img.size == (80, 40)
