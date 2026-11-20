from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from mobile_pilot_mcp import dashboard as dashboard_module
from mobile_pilot_mcp import server as server_module
from mobile_pilot_mcp.dashboard import create_dashboard_app
from mobile_pilot_mcp.simulator import SimulatorError, SimulatorManager
from mobile_pilot_mcp.ui_tree import UITreeParser, find_element_by_predicate


def _node(type_: str, visible: str, children: list | None = None, **extra) -> dict:
    return {
        "type": type_,
        "isVisible": visible,
        "isEnabled": "1",
        "isAccessible": "1",
        "rect": {"x": 0, "y": 0, "width": 10, "height": 10},
        "children": children or [],
        **extra,
    }


# WDA sends flags as "1"/"0" strings and reports many containers as not visible.
SOURCE = _node(
    "Application",
    "1",
    [
        _node("Other", "0", [_node("Button", "1", label="Save", rawIdentifier="save_btn")]),
        _node("Button", "0", label="Hidden"),
    ],
)


def test_only_visible_drops_invisible_elements_but_keeps_their_visible_children() -> None:
    root, elements = UITreeParser().parse(SOURCE, only_visible=True)

    assert [e.element_type for e in elements] == ["Application", "Button"]
    assert elements[1].label == "Save"
    assert root is not None and root.children == [elements[1]]
    assert [e.index for e in elements] == [0, 1]


def test_only_visible_false_keeps_everything() -> None:
    _, elements = UITreeParser().parse(SOURCE, only_visible=False)

    assert len(elements) == 4
    assert [e.visible for e in elements] == [True, False, True, False]


def test_identifier_predicate_matches_raw_identifier() -> None:
    _, elements = UITreeParser().parse(SOURCE, only_visible=True)

    found = find_element_by_predicate(elements, {"identifier": "save_btn"})

    assert found is not None and found.label == "Save"


def test_tools_reuse_the_host_and_port_given_to_start_bridge(monkeypatch) -> None:
    monkeypatch.setattr(server_module, "wda_clients", {})

    bridged = server_module.get_wda_client("dev", 8101, "192.168.1.30")
    later = server_module.get_wda_client("dev")

    assert later is bridged
    assert later.base_url == "http://192.168.1.30:8101"


def test_unbridged_device_uses_defaults(monkeypatch) -> None:
    monkeypatch.setattr(server_module, "wda_clients", {})

    client = server_module.get_wda_client("dev")

    assert client.base_url == f"http://{server_module.WDA_HOST}:{server_module.DEFAULT_WDA_PORT}"


class _FakeProc:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.signals: list[int] = []

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        self.returncode = 0

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


def test_recording_can_restart_after_recorder_died(monkeypatch, tmp_path) -> None:
    async def run_test() -> None:
        manager = SimulatorManager()
        manager._recording_processes["dev"] = _FakeProc(returncode=1)  # type: ignore[assignment]
        fresh = _FakeProc(returncode=None)

        async def fake_exec(*_args, **_kwargs):
            return fresh

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        await manager.start_recording("dev", tmp_path / "a.mov")
        assert manager._recording_processes["dev"] is fresh

        with pytest.raises(SimulatorError):
            await manager.start_recording("dev", tmp_path / "b.mov")

    asyncio.run(run_test())


def test_stop_recording_clears_a_dead_recorder_without_signalling() -> None:
    async def run_test() -> None:
        manager = SimulatorManager()
        dead = _FakeProc(returncode=1)
        manager._recording_processes["dev"] = dead  # type: ignore[assignment]

        assert await manager.stop_recording("dev") is True
        assert dead.signals == []
        assert "dev" not in manager._recording_processes
        assert await manager.stop_recording("dev") is False

    asyncio.run(run_test())


def test_stop_all_recordings_stops_live_recorders() -> None:
    async def run_test() -> None:
        manager = SimulatorManager()
        live = _FakeProc(returncode=None)
        manager._recording_processes["dev"] = live  # type: ignore[assignment]

        await manager.stop_all_recordings()

        assert len(live.signals) == 1
        assert manager._recording_processes == {}

    asyncio.run(run_test())


def test_dashboard_rejects_cross_site_requests(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_module, "DASHBOARD_HOST", "127.0.0.1")

    async def run_test() -> None:
        async with TestClient(TestServer(create_dashboard_app())) as client:
            own_origin = f"http://{client.host}:{client.port}"

            assert (await client.get("/api/state")).status == 200
            same = await client.post(
                "/api/action", json={}, headers={"Origin": own_origin}
            )
            assert same.status == 400  # passed the origin check, failed on missing tool

            evil = {"Origin": "https://evil.example"}
            assert (await client.post("/api/action", json={}, headers=evil)).status == 403
            assert (await client.get("/ws", headers=evil)).status == 403
            rebound = {"Host": "evil.example:8200"}
            assert (await client.get("/api/state", headers=rebound)).status == 403

    asyncio.run(run_test())


def test_recording_that_exits_immediately_is_reported(monkeypatch, tmp_path) -> None:
    async def run_test() -> None:
        manager = SimulatorManager()

        async def fake_exec(*_args, **_kwargs):
            return _FakeProc(returncode=3)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        with pytest.raises(SimulatorError, match="exit code 3"):
            await manager.start_recording("dev", tmp_path / "a.mov")
        assert "dev" not in manager._recording_processes

    asyncio.run(run_test())


class _FakeWDA:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy
        self.session_id = "S"
        self.base_url = "http://fake:1"

    async def health_check(self) -> bool:
        return self.healthy

    async def create_session(self) -> str:
        return "S"

    async def update_settings(self, settings: dict) -> dict:
        return settings


def _start_bridge(**kwargs) -> str:
    async def run() -> str:
        tool = await server_module.mcp.get_tool("start_bridge")
        return await tool.fn(**kwargs)

    return asyncio.run(run())


def test_start_bridge_without_arguments_keeps_the_bridged_address(monkeypatch) -> None:
    good = _FakeWDA(healthy=True)
    monkeypatch.setattr(server_module, "wda_clients", {"dev": good})

    async def no_device(_udid):
        return None

    monkeypatch.setattr(server_module.simulator_manager, "get_device", no_device)

    assert "running at fake:1" in _start_bridge(device_id="dev")
    assert server_module.wda_clients["dev"] is good


def test_failed_start_bridge_does_not_replace_a_working_bridge(monkeypatch) -> None:
    good = _FakeWDA(healthy=True)
    monkeypatch.setattr(server_module, "wda_clients", {"dev": good})
    monkeypatch.setattr(server_module, "WDAClient", lambda **_kw: _FakeWDA(healthy=False))

    assert "not responding" in _start_bridge(device_id="dev", host="typo.invalid")
    assert server_module.wda_clients["dev"] is good


def test_filtered_out_root_is_printed_without_an_index() -> None:
    source = _node("Application", "0", [_node("Button", "1", label="A"), _node("Button", "1")])
    parser = UITreeParser()

    root, elements = parser.parse(source, only_visible=True)

    assert root is not None and [e.index for e in elements] == [0, 1]
    assert parser.format_tree(root, elements).splitlines()[0] == "Application"
