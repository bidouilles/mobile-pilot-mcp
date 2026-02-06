"""iOS Simulator management via xcrun simctl."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SimulatorState(str, Enum):
    """Simulator runtime state."""

    SHUTDOWN = "Shutdown"
    BOOTED = "Booted"
    BOOTING = "Booting"
    SHUTTING_DOWN = "Shutting Down"


@dataclass
class SimulatorDevice:
    """Represents an iOS Simulator device."""

    udid: str
    name: str
    state: SimulatorState
    runtime: str
    device_type: str | None = None
    is_available: bool = True
    data_path: str | None = None
    log_path: str | None = None

    @property
    def ios_version(self) -> str:
        """Extract iOS version from runtime string."""
        # Runtime looks like "com.apple.CoreSimulator.SimRuntime.iOS-17-4"
        match = re.search(r"iOS[.-](\d+)[.-](\d+)", self.runtime)
        if match:
            return f"{match.group(1)}.{match.group(2)}"
        return "Unknown"

    @property
    def is_booted(self) -> bool:
        return self.state == SimulatorState.BOOTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "udid": self.udid,
            "name": self.name,
            "state": self.state.value,
            "ios_version": self.ios_version,
            "runtime": self.runtime,
            "device_type": self.device_type,
            "is_available": self.is_available,
            "is_booted": self.is_booted,
        }


@dataclass
class InstalledApp:
    """Represents an installed app on the simulator."""

    bundle_id: str
    name: str
    path: str | None = None
    version: str | None = None


class SimulatorError(Exception):
    """Simulator operation error."""

    pass


class SimulatorManager:
    """Manages iOS Simulators via xcrun simctl."""

    def __init__(self):
        self._devices_cache: dict[str, SimulatorDevice] = {}
        self._cache_timestamp: float = 0
        self._cache_ttl: float = 5.0  # Cache devices for 5 seconds

    async def _run_simctl(
        self,
        *args: str,
        timeout: float = 30.0,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run xcrun simctl command."""
        cmd = ["xcrun", "simctl", *args]
        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise SimulatorError(f"Command timed out: {' '.join(cmd)}") from e
        except FileNotFoundError as e:
            raise SimulatorError(
                "xcrun not found. Make sure Xcode Command Line Tools are installed."
            ) from e

        result = subprocess.CompletedProcess(
            cmd,
            proc.returncode or 0,
            stdout.decode("utf-8"),
            stderr.decode("utf-8"),
        )

        if check and result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            raise SimulatorError(f"simctl command failed: {error_msg}")

        return result

    async def list_devices(self, refresh: bool = False) -> list[SimulatorDevice]:
        """List all available simulators.

        Args:
            refresh: Force refresh the cache

        Returns:
            List of SimulatorDevice objects
        """
        import time

        now = time.time()
        if not refresh and self._devices_cache and (now - self._cache_timestamp) < self._cache_ttl:
            return list(self._devices_cache.values())

        result = await self._run_simctl("list", "devices", "-j")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise SimulatorError(f"Failed to parse simctl output: {e}") from e

        devices: list[SimulatorDevice] = []
        for runtime, device_list in data.get("devices", {}).items():
            for device_data in device_list:
                state_str = device_data.get("state", "Shutdown")
                try:
                    state = SimulatorState(state_str)
                except ValueError:
                    state = SimulatorState.SHUTDOWN

                device = SimulatorDevice(
                    udid=device_data["udid"],
                    name=device_data["name"],
                    state=state,
                    runtime=runtime,
                    device_type=device_data.get("deviceTypeIdentifier"),
                    is_available=device_data.get("isAvailable", True),
                    data_path=device_data.get("dataPath"),
                    log_path=device_data.get("logPath"),
                )
                devices.append(device)
                self._devices_cache[device.udid] = device

        self._cache_timestamp = now
        return devices

    async def get_device(self, udid: str) -> SimulatorDevice | None:
        """Get a specific device by UDID."""
        devices = await self.list_devices()
        for device in devices:
            if device.udid == udid:
                return device
        return None

    async def get_booted_devices(self) -> list[SimulatorDevice]:
        """Get all booted simulators."""
        devices = await self.list_devices(refresh=True)
        return [d for d in devices if d.is_booted]

    async def boot(self, udid: str) -> None:
        """Boot a simulator."""
        device = await self.get_device(udid)
        if not device:
            raise SimulatorError(f"Device not found: {udid}")

        if device.is_booted:
            logger.info(f"Simulator {udid} is already booted")
            return

        await self._run_simctl("boot", udid, timeout=60.0)
        logger.info(f"Booted simulator: {udid}")

        # Clear cache to reflect new state
        self._devices_cache.clear()

    async def shutdown(self, udid: str) -> None:
        """Shutdown a simulator."""
        device = await self.get_device(udid)
        if not device:
            raise SimulatorError(f"Device not found: {udid}")

        if not device.is_booted:
            logger.info(f"Simulator {udid} is already shut down")
            return

        await self._run_simctl("shutdown", udid, timeout=30.0)
        logger.info(f"Shut down simulator: {udid}")

        # Clear cache
        self._devices_cache.clear()

    async def open_simulator_app(self) -> None:
        """Open the Simulator.app."""
        proc = await asyncio.create_subprocess_exec(
            "open", "-a", "Simulator",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def screenshot(self, udid: str, output_path: str | Path) -> Path:
        """Take a screenshot using simctl.

        Args:
            udid: Simulator UDID
            output_path: Where to save the screenshot

        Returns:
            Path to the saved screenshot
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        await self._run_simctl("io", udid, "screenshot", str(output_path), timeout=30.0)
        return output_path

    async def install_app(self, udid: str, app_path: str | Path) -> None:
        """Install an app on the simulator."""
        await self._run_simctl("install", udid, str(app_path), timeout=120.0)

    async def uninstall_app(self, udid: str, bundle_id: str) -> None:
        """Uninstall an app from the simulator."""
        await self._run_simctl("uninstall", udid, bundle_id, timeout=30.0)

    async def launch_app(self, udid: str, bundle_id: str) -> None:
        """Launch an app on the simulator."""
        await self._run_simctl("launch", udid, bundle_id, timeout=30.0)

    async def terminate_app(self, udid: str, bundle_id: str) -> None:
        """Terminate an app on the simulator."""
        await self._run_simctl("terminate", udid, bundle_id, timeout=30.0, check=False)

    async def list_apps(self, udid: str) -> list[InstalledApp]:
        """List installed apps on the simulator.

        Note: This uses a workaround since simctl doesn't have a direct command.
        """
        device = await self.get_device(udid)
        if not device or not device.data_path:
            return []

        apps: list[InstalledApp] = []

        # Check installed apps in the simulator's data directory
        apps_dir = Path(device.data_path) / "Containers" / "Bundle" / "Application"
        if apps_dir.exists():
            for app_container in apps_dir.iterdir():
                if app_container.is_dir():
                    for item in app_container.iterdir():
                        if item.suffix == ".app":
                            # Try to read Info.plist
                            info_plist = item / "Info.plist"
                            if info_plist.exists():
                                try:
                                    # Use plutil to read plist
                                    proc = await asyncio.create_subprocess_exec(
                                        "plutil",
                                        "-convert",
                                        "json",
                                        "-o",
                                        "-",
                                        str(info_plist),
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                    )
                                    stdout, _ = await proc.communicate()
                                    if proc.returncode == 0:
                                        info = json.loads(stdout.decode())
                                        apps.append(
                                            InstalledApp(
                                                bundle_id=info.get(
                                                    "CFBundleIdentifier", "unknown"
                                                ),
                                                name=info.get(
                                                    "CFBundleDisplayName",
                                                    info.get("CFBundleName", item.stem),
                                                ),
                                                path=str(item),
                                                version=info.get("CFBundleShortVersionString"),
                                            )
                                        )
                                except Exception as e:
                                    logger.debug(f"Failed to read app info: {e}")

        # Also list some common system apps
        system_apps = [
            InstalledApp(bundle_id="com.apple.Preferences", name="Settings"),
            InstalledApp(bundle_id="com.apple.mobilesafari", name="Safari"),
            InstalledApp(bundle_id="com.apple.mobilecal", name="Calendar"),
            InstalledApp(bundle_id="com.apple.mobilemail", name="Mail"),
            InstalledApp(bundle_id="com.apple.mobilenotes", name="Notes"),
            InstalledApp(bundle_id="com.apple.reminders", name="Reminders"),
            InstalledApp(bundle_id="com.apple.Maps", name="Maps"),
            InstalledApp(bundle_id="com.apple.Photos", name="Photos"),
            InstalledApp(bundle_id="com.apple.camera", name="Camera"),
            InstalledApp(bundle_id="com.apple.AppStore", name="App Store"),
            InstalledApp(bundle_id="com.apple.weather", name="Weather"),
            InstalledApp(bundle_id="com.apple.calculator", name="Calculator"),
            InstalledApp(bundle_id="com.apple.compass", name="Compass"),
            InstalledApp(bundle_id="com.apple.clock", name="Clock"),
            InstalledApp(bundle_id="com.apple.Health", name="Health"),
            InstalledApp(bundle_id="com.apple.Fitness", name="Fitness"),
            InstalledApp(bundle_id="com.apple.Passbook", name="Wallet"),
            InstalledApp(bundle_id="com.apple.tips", name="Tips"),
            InstalledApp(bundle_id="com.apple.podcasts", name="Podcasts"),
            InstalledApp(bundle_id="com.apple.tv", name="TV"),
            InstalledApp(bundle_id="com.apple.facetime", name="FaceTime"),
            InstalledApp(bundle_id="com.apple.MobileStore", name="iTunes Store"),
        ]

        # Add system apps if not already in the list
        existing_bundle_ids = {app.bundle_id for app in apps}
        for sys_app in system_apps:
            if sys_app.bundle_id not in existing_bundle_ids:
                apps.append(sys_app)

        return sorted(apps, key=lambda a: a.name.lower())

    async def open_url(self, udid: str, url: str) -> None:
        """Open a URL in the simulator."""
        await self._run_simctl("openurl", udid, url, timeout=30.0)

    async def add_media(self, udid: str, media_path: str | Path) -> None:
        """Add media (photo/video) to the simulator."""
        await self._run_simctl("addmedia", udid, str(media_path), timeout=30.0)

    async def set_location(self, udid: str, latitude: float, longitude: float) -> None:
        """Set the simulator's location."""
        await self._run_simctl(
            "location", udid, "set", f"{latitude},{longitude}", timeout=10.0
        )

    async def get_app_container(
        self,
        udid: str,
        bundle_id: str,
        container_type: str = "app",
    ) -> str | None:
        """Get the path to an app's container.

        Args:
            udid: Simulator UDID
            bundle_id: App bundle ID
            container_type: 'app', 'data', 'groups', or specific group ID

        Returns:
            Container path or None if not found
        """
        try:
            result = await self._run_simctl(
                "get_app_container", udid, bundle_id, container_type, check=False
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except SimulatorError:
            pass
        return None

    async def push_notification(
        self,
        udid: str,
        bundle_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Send a push notification to an app.

        Args:
            udid: Simulator UDID
            bundle_id: App bundle ID
            payload: Notification payload dict
        """
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            payload_path = f.name

        try:
            await self._run_simctl("push", udid, bundle_id, payload_path, timeout=10.0)
        finally:
            Path(payload_path).unlink(missing_ok=True)

    async def clear_keychain(self, udid: str) -> None:
        """Clear the simulator's keychain."""
        await self._run_simctl("keychain", udid, "reset", timeout=30.0)

    async def status_bar_override(
        self,
        udid: str,
        time: str | None = None,
        battery_level: int | None = None,
        battery_state: str | None = None,
        data_network: str | None = None,
        wifi_mode: str | None = None,
        wifi_bars: int | None = None,
        cellular_mode: str | None = None,
        cellular_bars: int | None = None,
        operator_name: str | None = None,
    ) -> None:
        """Override status bar appearance.

        Args:
            udid: Simulator UDID
            time: Time string (e.g., "9:41")
            battery_level: Battery level 0-100
            battery_state: 'charging', 'charged', 'discharging'
            data_network: 'hide', 'wifi', '3g', '4g', 'lte', 'lte-a', 'lte+',
                '5g', '5g+', '5g-uwb', '5g-uc'
            wifi_mode: 'searching', 'failed', 'active'
            wifi_bars: 0-3
            cellular_mode: 'notSupported', 'searching', 'failed', 'active'
            cellular_bars: 0-4
            operator_name: Carrier name (empty string to hide)
        """
        args = ["status_bar", udid, "override"]
        if time is not None:
            args.extend(["--time", time])
        if battery_level is not None:
            args.extend(["--batteryLevel", str(battery_level)])
        if battery_state is not None:
            args.extend(["--batteryState", battery_state])
        if data_network is not None:
            args.extend(["--dataNetwork", data_network])
        if wifi_mode is not None:
            args.extend(["--wifiMode", wifi_mode])
        if wifi_bars is not None:
            args.extend(["--wifiBars", str(wifi_bars)])
        if cellular_mode is not None:
            args.extend(["--cellularMode", cellular_mode])
        if cellular_bars is not None:
            args.extend(["--cellularBars", str(cellular_bars)])
        if operator_name is not None:
            args.extend(["--operatorName", operator_name])

        await self._run_simctl(*args, timeout=10.0)

    async def status_bar_clear(self, udid: str) -> None:
        """Clear status bar overrides."""
        await self._run_simctl("status_bar", udid, "clear", timeout=10.0)

    # === Screen Recording ===

    _recording_processes: dict[str, asyncio.subprocess.Process] = {}

    async def start_recording(
        self,
        udid: str,
        output_path: str | Path,
        codec: str = "hevc",
    ) -> None:
        """Start screen recording using simctl.

        Args:
            udid: Simulator UDID
            output_path: Path to save the video file (.mov)
            codec: Video codec ('h264' or 'hevc', default: hevc)
        """
        if udid in self._recording_processes:
            raise SimulatorError(f"Recording already in progress for {udid}")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["xcrun", "simctl", "io", udid, "recordVideo", f"--codec={codec}", str(output_path)]
        logger.debug(f"Starting recording: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._recording_processes[udid] = proc
        logger.info(f"Recording started for {udid}: {output_path}")

    async def stop_recording(self, udid: str) -> bool:
        """Stop screen recording.

        Args:
            udid: Simulator UDID

        Returns:
            True if recording was stopped, False if no recording was active
        """
        proc = self._recording_processes.pop(udid, None)
        if not proc:
            return False

        # Send SIGINT to gracefully stop recording
        proc.send_signal(2)  # SIGINT

        try:
            await asyncio.wait_for(proc.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

        logger.info(f"Recording stopped for {udid}")
        return True

    def is_recording(self, udid: str) -> bool:
        """Check if recording is in progress for a device."""
        proc = self._recording_processes.get(udid)
        return proc is not None and proc.returncode is None


# Global instance
simulator_manager = SimulatorManager()
