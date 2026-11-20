from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from mobile_pilot_mcp import wda_launcher as launcher_module
from mobile_pilot_mcp.wda_launcher import WDALauncher, WDALaunchError, is_local_host


def test_only_local_hosts_are_started():
    assert is_local_host("127.0.0.1")
    assert is_local_host("localhost")
    assert is_local_host("::1")
    assert not is_local_host("192.168.1.30")
    assert not is_local_host("wda.example.com")


def test_wda_path_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("WDA_PATH", str(tmp_path))
    assert launcher_module.resolve_wda_path() == tmp_path


def test_missing_project_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("WDA_PATH", str(tmp_path))

    async def never() -> bool:
        return False

    with pytest.raises(WDALaunchError, match="not found"):
        asyncio.run(WDALauncher().start("dev", 8100, never, timeout=1))


def _fake_project(monkeypatch, tmp_path: Path, script: str) -> None:
    """Point the launcher at a fake project whose `xcodebuild` runs `script`."""
    project = tmp_path / "wda"
    (project / "WebDriverAgent.xcodeproj").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "xcodebuild"
    fake.write_text(f"#!{sys.executable}\n{script}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("WDA_PATH", str(project))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(launcher_module, "WDA_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(launcher_module, "POLL_INTERVAL", 0.05)


def test_build_failure_returns_the_log_tail(monkeypatch, tmp_path):
    _fake_project(monkeypatch, tmp_path, "print('** BUILD FAILED **'); raise SystemExit(65)")

    async def never() -> bool:
        return False

    with pytest.raises(WDALaunchError, match="BUILD FAILED"):
        asyncio.run(WDALauncher().start("dev", 8100, never, timeout=5))


def test_started_process_is_waited_for_then_stopped(monkeypatch, tmp_path):
    _fake_project(monkeypatch, tmp_path, "import time; time.sleep(60)")

    async def run() -> None:
        launcher = WDALauncher()
        calls = 0

        async def ready_on_third_poll() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 3

        log = await launcher.start("dev", 8100, ready_on_third_poll, timeout=5)
        assert log.name == "wda-dev.log"
        assert launcher.is_running("dev")

        # A second start while it runs must not spawn another xcodebuild.
        process = launcher._processes["dev"]
        await launcher.start("dev", 8100, ready_on_third_poll, timeout=5)
        assert launcher._processes["dev"] is process

        await launcher.stop_all()
        assert process.returncode is not None
        assert not launcher.is_running("dev")

    asyncio.run(run())


def test_timeout_leaves_the_build_running(monkeypatch, tmp_path):
    _fake_project(monkeypatch, tmp_path, "import time; time.sleep(60)")

    async def run() -> None:
        launcher = WDALauncher()

        async def never() -> bool:
            return False

        with pytest.raises(WDALaunchError, match="still starting"):
            await launcher.start("dev", 8100, never, timeout=0.2)
        assert launcher.is_running("dev")
        await launcher.stop_all()

    asyncio.run(run())
