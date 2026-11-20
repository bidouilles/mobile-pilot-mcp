"""Start WebDriverAgent on a simulator when nothing answers.

`start_bridge` uses this so WDA no longer has to be started by hand, and comes
back by itself after the simulator or WDA has stopped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

WDA_AUTO_START = os.environ.get("WDA_AUTO_START", "true").lower() in ("true", "1", "yes")
# A first build of WDA takes a few minutes; later starts reuse the build.
WDA_START_TIMEOUT = float(os.environ.get("WDA_START_TIMEOUT", "300"))
WDA_LOG_DIR = Path("/tmp/mobile-pilot-mcp/wda")
POLL_INTERVAL = 2.0
STOP_TIMEOUT = 10.0


class WDALaunchError(Exception):
    """WebDriverAgent could not be started."""


def resolve_wda_path() -> Path:
    """Where the WebDriverAgent project is, using the same rules as scripts/start_wda.sh."""
    if os.environ.get("WDA_PATH"):
        return Path(os.environ["WDA_PATH"]).expanduser()
    monorepo = Path.home() / "appium-ios" / "packages" / "webdriveragent"
    legacy = Path.home() / "WebDriverAgent"
    if monorepo.is_dir() or not legacy.is_dir():
        return monorepo
    return legacy


def is_local_host(host: str) -> bool:
    """True when WDA on this host could have been started from this machine."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _log_tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class WDALauncher:
    """Runs `xcodebuild test` for WebDriverAgent, one process per simulator."""

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._logs: dict[str, Path] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def is_running(self, udid: str) -> bool:
        process = self._processes.get(udid)
        return process is not None and process.returncode is None

    def log_path(self, udid: str) -> Path:
        return WDA_LOG_DIR / f"wda-{udid}.log"

    async def start(
        self,
        udid: str,
        port: int,
        is_ready: Callable[[], Awaitable[bool]],
        timeout: float = WDA_START_TIMEOUT,
    ) -> Path:
        """Start WDA (unless this launcher already did) and wait until it answers.

        Returns:
            The path of the xcodebuild log.

        Raises:
            WDALaunchError: the project is missing, xcodebuild stopped, or WDA
                did not answer in time (it is left running in that case, so a
                later call can keep waiting for the same build).
        """
        lock = self._locks.setdefault(udid, asyncio.Lock())
        async with lock:
            if not self.is_running(udid):
                await self._spawn(udid, port)
            return await self._wait_until_ready(udid, is_ready, timeout)

    async def _spawn(self, udid: str, port: int) -> None:
        wda_path = resolve_wda_path()
        if not (wda_path / "WebDriverAgent.xcodeproj").is_dir():
            raise WDALaunchError(
                f"WebDriverAgent project not found in {wda_path}. Clone it with "
                "`git clone https://github.com/appium/appium-ios.git ~/appium-ios` "
                "or set WDA_PATH."
            )

        WDA_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = self.log_path(udid)
        with log_path.open("wb") as log:
            try:
                process = await asyncio.create_subprocess_exec(
                    "xcodebuild",
                    "-project", "WebDriverAgent.xcodeproj",
                    "-scheme", "WebDriverAgentRunner",
                    "-destination", f"platform=iOS Simulator,id={udid}",
                    "-derivedDataPath", "build",
                    f"USE_PORT={port}",
                    "test",
                    cwd=wda_path,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                    # Own process group, so stopping it also stops its children.
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                raise WDALaunchError("xcodebuild not found: install Xcode") from exc

        self._processes[udid] = process
        self._logs[udid] = log_path
        logger.info("Started WebDriverAgent for %s (pid %s, log %s)", udid, process.pid, log_path)

    async def _wait_until_ready(
        self,
        udid: str,
        is_ready: Callable[[], Awaitable[bool]],
        timeout: float,
    ) -> Path:
        process = self._processes[udid]
        log_path = self._logs[udid]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            if await is_ready():
                return log_path
            if process.returncode is not None:
                raise WDALaunchError(
                    f"xcodebuild exited with code {process.returncode} before WebDriverAgent "
                    f"answered. Log: {log_path}\n\n{_log_tail(log_path)}"
                )
            if loop.time() >= deadline:
                raise WDALaunchError(
                    f"WebDriverAgent did not answer within {timeout:.0f} s; it is still "
                    f"starting (a first build takes a few minutes). Call start_bridge again "
                    f"to keep waiting. Log: {log_path}"
                )
            await asyncio.sleep(POLL_INTERVAL)

    async def stop(self, udid: str) -> None:
        """Stop the WDA this launcher started for a simulator."""
        process = self._processes.pop(udid, None)
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), STOP_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        logger.info("Stopped WebDriverAgent for %s", udid)

    async def stop_all(self) -> None:
        for udid in list(self._processes):
            await self.stop(udid)
