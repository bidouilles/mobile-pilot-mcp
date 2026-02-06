"""MCP Server for iOS Simulator automation via WebDriverAgent.

This server uses FastMCP for cleaner decorator-based tool definitions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from .dashboard import DASHBOARD_PORT, dashboard_state, start_dashboard, stop_dashboard
from .simulator import SimulatorManager
from .ui_tree import UITreeParser, find_element_by_predicate
from .wda_client import WDAClient, WDAError

# Configure logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()


class FlushingStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


handler = FlushingStreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.DEBUG),
    handlers=[handler],
)
logger = logging.getLogger("ios-simulator-mcp")

# Constants
SCREENSHOT_DIR = Path("/tmp/ios-simulator-mcp/screenshots")
DEFAULT_WDA_PORT = 8100

# Global state
simulator_manager = SimulatorManager()
wda_clients: dict[str, WDAClient] = {}
_last_ui_elements: dict[str, list] = {}
_recording_paths: dict[str, Path] = {}
_dashboard_wrapped_tools: set[str] = set()

# WDA host configuration
WDA_HOST = os.environ.get("WDA_HOST", "127.0.0.1")


# === Helper Functions ===


def get_wda_client(
    device_id: str,
    port: int = DEFAULT_WDA_PORT,
    host: str | None = None,
) -> WDAClient:
    """Get or create a WDA client for a device."""
    actual_host = host or WDA_HOST
    key = f"{device_id}:{actual_host}:{port}"

    if key not in wda_clients:
        wda_clients[key] = WDAClient(host=actual_host, port=port)
        wda_clients[device_id] = wda_clients[key]

    return wda_clients[key]


async def reset_wda_session(device_id: str) -> None:
    """Reset WDA session for a device."""
    client = wda_clients.get(device_id)
    if client:
        await client.delete_session()
        client.session_id = None


def ensure_screenshot_dir() -> None:
    """Ensure screenshot directory exists."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def save_screenshot(data: bytes, prefix: str = "screenshot") -> str:
    """Save screenshot to file and return path."""
    ensure_screenshot_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{prefix}-{timestamp}.png"
    filepath = SCREENSHOT_DIR / filename
    filepath.write_bytes(data)
    return str(filepath)


def _track_tool_call(name: str, args: dict[str, Any]):
    """Track tool call in dashboard and return the call object."""
    return dashboard_state.add_tool_call(name, args)


def _complete_tool_call(tool_call, result: str | None = None, error: str | None = None):
    """Mark a tool call as complete in the dashboard."""
    dashboard_state.complete_tool_call(tool_call, result=result, error=error)


# === Lifespan for Dashboard ===


def _wrap_tool_with_tracking(tool_name: str, original_fn):
    """Wrap a tool function to track calls in the dashboard."""
    import functools

    @functools.wraps(original_fn)
    async def tracked_fn(*args, **kwargs):
        # Track the call
        tool_call = _track_tool_call(tool_name, kwargs)
        try:
            result = await original_fn(*args, **kwargs)
            _complete_tool_call(tool_call, result=result)
            return result
        except Exception as e:
            _complete_tool_call(tool_call, error=str(e))
            raise

    return tracked_fn


@asynccontextmanager
async def lifespan(mcp: FastMCP):
    """Manage dashboard server lifecycle."""
    logger.info("=" * 60)
    logger.info("iOS Simulator MCP Server starting...")
    logger.info(f"WDA_HOST: {WDA_HOST}")
    logger.info(f"WDA_PORT: {DEFAULT_WDA_PORT}")
    logger.info("WDA port override: pass `port` to `start_bridge` per connection")
    logger.info(f"Screenshot dir: {SCREENSHOT_DIR}")
    logger.info(f"Dashboard port: {DASHBOARD_PORT}")
    logger.info(f"Log level: {LOG_LEVEL}")
    logger.info("=" * 60)

    # Wrap all registered tools with dashboard tracking
    # This makes MCP protocol calls appear in the dashboard
    tools = await mcp.get_tools()
    wrapped_count = 0
    for tool_name, tool in tools.items():
        if tool_name in _dashboard_wrapped_tools:
            continue

        original_fn = tool.fn
        tool.fn = _wrap_tool_with_tracking(tool_name, original_fn)
        _dashboard_wrapped_tools.add(tool_name)
        wrapped_count += 1
    logger.info(f"Wrapped {wrapped_count} tools with dashboard tracking")

    # Wire up tool executor for dashboard quick actions
    async def execute_tool_from_dashboard(name: str, args: dict[str, Any]) -> str:
        """Execute a tool from dashboard quick actions."""
        # Tools are already wrapped, so just call the function directly.
        try:
            tool = await mcp.get_tool(name)
        except Exception as exc:
            raise ValueError(f"Unknown tool: {name}") from exc
        return await tool.fn(**args)

    dashboard_state.tool_executor = execute_tool_from_dashboard

    # Start dashboard server
    dashboard_runner = await start_dashboard()
    logger.info("Server ready, waiting for MCP client connection...")

    try:
        yield
    finally:
        await stop_dashboard(dashboard_runner)


# === Create FastMCP Server ===

mcp = FastMCP(
    "ios-simulator-mcp",
    lifespan=lifespan,
)


# === Device Management Tools ===


@mcp.tool
async def list_devices(
    only_booted: Annotated[bool, Field(description="Only list booted simulators")] = False,
) -> str:
    """List all iOS simulators (booted and available)."""
    devices = await simulator_manager.list_devices(refresh=True)

    if only_booted:
        devices = [d for d in devices if d.is_booted]

    if not devices:
        return "No simulators found" if not only_booted else "No booted simulators"

    result = []
    for d in devices:
        status = "Booted" if d.is_booted else "Shutdown"
        result.append(f"- {d.name} (iOS {d.ios_version}) [{status}]\n  UDID: {d.udid}")

    return "\n".join(result)


@mcp.tool
async def get_device(
    device_id: Annotated[str, Field(description="Simulator UDID")],
) -> str:
    """Get information about a specific simulator."""
    device = await simulator_manager.get_device(device_id)
    if not device:
        return f"Device not found: {device_id}"
    return json.dumps(device.to_dict(), indent=2)


@mcp.tool
async def boot_simulator(
    device_id: Annotated[str, Field(description="Simulator UDID")],
) -> str:
    """Boot an iOS simulator."""
    await simulator_manager.boot(device_id)
    await simulator_manager.open_simulator_app()
    return f"Simulator {device_id} booted successfully"


@mcp.tool
async def shutdown_simulator(
    device_id: Annotated[str, Field(description="Simulator UDID")],
) -> str:
    """Shutdown an iOS simulator."""
    await simulator_manager.shutdown(device_id)
    return f"Simulator {device_id} shut down successfully"


@mcp.tool
async def start_bridge(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    port: Annotated[int, Field(description="WDA port")] = DEFAULT_WDA_PORT,
    host: Annotated[str | None, Field(description="WDA host (default: WDA_HOST env var)")] = None,
) -> str:
    """Check WebDriverAgent connection (WDA must be running separately via xcodebuild)."""
    actual_host = host or WDA_HOST
    client = get_wda_client(device_id, port, actual_host)

    if await client.health_check():
        await client.create_session()

        # Update dashboard with device info
        device = await simulator_manager.get_device(device_id)
        if device:
            dashboard_state.update_device_info({
                "name": device.name,
                "udid": device.udid,
                "ios_version": device.ios_version,
                "state": device.state.value,
                "wda_connected": True,
                "wda_host": f"{actual_host}:{port}",
            })

        return (
            f"WebDriverAgent is running at {actual_host}:{port}. "
            f"Session created (ID: {client.session_id})."
        )
    else:
        return (
            f"WebDriverAgent is not responding at {actual_host}:{port}.\n\n"
            "To start WDA, run:\n"
            "  xcodebuild -project WebDriverAgent.xcodeproj "
            "-scheme WebDriverAgentRunner -destination "
            f"'platform=iOS Simulator,id={device_id}' test\n\n"
            "Or use a tool like appium-webdriveragent.\n\n"
            "If WDA is running on a different host, set WDA_HOST environment variable\n"
            "or pass the host parameter."
        )


# === Screenshot ===


@mcp.tool
async def get_screenshot(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    scale: Annotated[float, Field(description="Scale factor 0.1-1.0")] = 0.5,
    format: Annotated[Literal["png", "jpeg"], Field(description="Image format")] = "jpeg",
    quality: Annotated[int, Field(ge=1, le=100, description="JPEG quality 1-100")] = 85,
) -> str:
    """Capture a screenshot from the simulator with resizing and format options."""
    from PIL import Image

    ensure_screenshot_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    temp_filepath = SCREENSHOT_DIR / f"temp-{timestamp}.png"
    await simulator_manager.screenshot(device_id, temp_filepath)

    with Image.open(temp_filepath) as img:
        original_size = img.size
        original_file_size = temp_filepath.stat().st_size

        if scale and scale < 1.0:
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        if format == "jpeg":
            if img.mode == "RGBA":
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            filepath = SCREENSHOT_DIR / f"screenshot-{timestamp}.jpg"
            img.save(filepath, "JPEG", quality=quality, optimize=True)
        else:
            filepath = SCREENSHOT_DIR / f"screenshot-{timestamp}.png"
            img.save(filepath, "PNG", optimize=True)

        new_file_size = filepath.stat().st_size
        new_size = img.size

    temp_filepath.unlink(missing_ok=True)
    reduction = ((original_file_size - new_file_size) / original_file_size) * 100

    return (
        f"Screenshot saved: {filepath}\n"
        f"Original: {original_size[0]}x{original_size[1]} ({original_file_size / 1024:.1f}KB)\n"
        f"Optimized: {new_size[0]}x{new_size[1]} ({new_file_size / 1024:.1f}KB)\n"
        f"Reduction: {reduction:.1f}%"
    )


# === UI Tree ===


@mcp.tool
async def get_ui_tree(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    verbose: Annotated[bool, Field(description="Include element bounds")] = False,
    only_visible: Annotated[bool, Field(description="Only visible elements")] = True,
    format: Annotated[Literal["tree", "flat", "json"], Field(description="Output format")] = "tree",
) -> str:
    """Get the UI accessibility tree showing all visible elements with indices for tapping."""
    client = get_wda_client(device_id)
    source = await client.get_source(format="json")

    parser = UITreeParser()
    root, elements = parser.parse(source, only_visible=only_visible)

    if not root:
        return "No UI elements found"

    _last_ui_elements[device_id] = elements

    if format == "json":
        return json.dumps([e.to_dict(include_children=False) for e in elements], indent=2)
    elif format == "flat":
        return parser.format_flat_list(elements, verbose=verbose)
    else:
        return parser.format_tree(root, elements, verbose=verbose)


# === Tap ===


@mcp.tool
async def tap(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    index: Annotated[int | None, Field(description="Element index from UI tree")] = None,
    x: Annotated[int | None, Field(description="X coordinate")] = None,
    y: Annotated[int | None, Field(description="Y coordinate")] = None,
    predicate: Annotated[dict[str, Any] | None, Field(description="Element predicate")] = None,
) -> str:
    """Tap an element by index (from UI tree), predicate, or coordinates."""
    client = get_wda_client(device_id)

    if index is not None:
        elements = _last_ui_elements.get(device_id, [])
        if not elements:
            source = await client.get_source(format="json")
            parser = UITreeParser()
            _, elements = parser.parse(source, only_visible=True)
            _last_ui_elements[device_id] = elements

        if index >= len(elements):
            return f"Invalid index {index}. Max index is {len(elements) - 1}"

        elem = elements[index]
        await client.tap(elem.center_x, elem.center_y)
        return f"Tapped element [{index}] {elem.element_type} at ({elem.center_x}, {elem.center_y})"

    elif predicate:
        source = await client.get_source(format="json")
        parser = UITreeParser()
        _, elements = parser.parse(source, only_visible=True)

        elem = find_element_by_predicate(elements, predicate)
        if not elem:
            return f"No element found matching predicate: {predicate}"

        await client.tap(elem.center_x, elem.center_y)
        return (
            f"Tapped element [{elem.index}] {elem.element_type} "
            f"at ({elem.center_x}, {elem.center_y})"
        )

    elif x is not None and y is not None:
        await client.tap(x, y)
        return f"Tapped at ({x}, {y})"

    else:
        return "Please provide index, predicate, or x/y coordinates"


# === Type Text ===


@mcp.tool
async def type_text(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    text: Annotated[str, Field(description="Text to type")],
    predicate: Annotated[dict[str, Any] | None, Field(description="Tap element first")] = None,
) -> str:
    """Type text (tap input field first to focus, or provide predicate)."""
    client = get_wda_client(device_id)

    if predicate:
        source = await client.get_source(format="json")
        parser = UITreeParser()
        _, elements = parser.parse(source, only_visible=True)

        elem = find_element_by_predicate(elements, predicate)
        if not elem:
            return f"No element found matching predicate: {predicate}"

        await client.tap(elem.center_x, elem.center_y)
        await asyncio.sleep(0.3)

    await client.send_keys(text)
    return f"Typed: {text}"


# === Swipe ===


@mcp.tool
async def swipe(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    from_x: Annotated[int | None, Field(description="Starting X coordinate")] = None,
    from_y: Annotated[int | None, Field(description="Starting Y coordinate")] = None,
    to_x: Annotated[int | None, Field(description="Ending X coordinate")] = None,
    to_y: Annotated[int | None, Field(description="Ending Y coordinate")] = None,
    duration_ms: Annotated[int, Field(description="Duration in milliseconds")] = 300,
    direction: Annotated[
        Literal["up", "down", "left", "right"] | None,
        Field(description="Swipe direction"),
    ] = None,
) -> str:
    """Perform a swipe gesture."""
    client = get_wda_client(device_id)

    if direction:
        size = await client.get_window_size()
        width = size.get("width", 390)
        height = size.get("height", 844)
        center_x = width // 2
        center_y = height // 2
        distance = min(width, height) // 3

        if direction == "up":
            from_x, from_y = center_x, center_y + distance
            to_x, to_y = center_x, center_y - distance
        elif direction == "down":
            from_x, from_y = center_x, center_y - distance
            to_x, to_y = center_x, center_y + distance
        elif direction == "left":
            from_x, from_y = center_x + distance, center_y
            to_x, to_y = center_x - distance, center_y
        elif direction == "right":
            from_x, from_y = center_x - distance, center_y
            to_x, to_y = center_x + distance, center_y

    if from_x is None or from_y is None or to_x is None or to_y is None:
        return "Please provide direction or from_x, from_y, to_x, to_y"

    await client.swipe(from_x, from_y, to_x, to_y, duration_ms / 1000.0)
    return f"Swiped from ({from_x}, {from_y}) to ({to_x}, {to_y})"


# === Navigation ===


@mcp.tool
async def go_home(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Navigate to home screen."""
    client = get_wda_client(device_id)
    await client.go_home()
    return "Navigated to home screen"


@mcp.tool
async def launch_app(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    bundle_id: Annotated[str, Field(description="App bundle ID (e.g., com.apple.Preferences)")],
) -> str:
    """Launch an application by bundle ID."""
    try:
        client = get_wda_client(device_id)
        await client.launch_app(bundle_id)
    except WDAError:
        await simulator_manager.launch_app(device_id, bundle_id)

    return f"Launched app: {bundle_id}"


@mcp.tool
async def terminate_app(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    bundle_id: Annotated[str, Field(description="App bundle ID")],
) -> str:
    """Terminate an application."""
    try:
        client = get_wda_client(device_id)
        await client.terminate_app(bundle_id)
    except WDAError:
        await simulator_manager.terminate_app(device_id, bundle_id)

    return f"Terminated app: {bundle_id}"


@mcp.tool
async def list_apps(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """List installed applications on the simulator."""
    apps = await simulator_manager.list_apps(device_id)
    if not apps:
        return "No apps found"

    result = []
    for app in apps:
        version_part = f" v{app.version}" if app.version else ""
        result.append(f"- {app.name}{version_part}\n  Bundle ID: {app.bundle_id}")

    return "\n".join(result)


@mcp.tool
async def open_url(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    url: Annotated[str, Field(description="URL to open")],
) -> str:
    """Open a URL in the simulator (opens in Safari or associated app)."""
    await simulator_manager.open_url(device_id, url)
    return f"Opened URL: {url}"


@mcp.tool
async def press_button(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    button: Annotated[
        Literal["home", "volumeUp", "volumeDown"],
        Field(description="Button to press"),
    ],
) -> str:
    """Press a hardware button (home, volumeUp, volumeDown)."""
    client = get_wda_client(device_id)
    await client.press_button(button)
    return f"Pressed button: {button}"


@mcp.tool
async def set_location(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    latitude: Annotated[float, Field(description="Latitude coordinate")],
    longitude: Annotated[float, Field(description="Longitude coordinate")],
) -> str:
    """Set the simulator's GPS location."""
    await simulator_manager.set_location(device_id, latitude, longitude)
    return f"Location set to ({latitude}, {longitude})"


# === Clipboard ===


@mcp.tool
async def get_clipboard(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Get clipboard/pasteboard content."""
    client = get_wda_client(device_id)
    content = await client.get_pasteboard()
    return f"Clipboard content: {content}"


@mcp.tool
async def set_clipboard(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    content: Annotated[str, Field(description="Content to set")],
) -> str:
    """Set clipboard/pasteboard content."""
    client = get_wda_client(device_id)
    await client.set_pasteboard(content)
    return f"Clipboard set to: {content}"


# === Window/Screen ===


@mcp.tool
async def get_window_size(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Get the simulator window/screen size."""
    client = get_wda_client(device_id)
    size = await client.get_window_size()
    return f"Window size: {size['width']}x{size['height']}"


# === Touch Gestures ===


@mcp.tool
async def double_tap(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    x: Annotated[int, Field(description="X coordinate")],
    y: Annotated[int, Field(description="Y coordinate")],
) -> str:
    """Double tap at coordinates."""
    client = get_wda_client(device_id)
    await client.double_tap(x, y)
    return f"Double tapped at ({x}, {y})"


@mcp.tool
async def long_press(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    x: Annotated[int, Field(description="X coordinate")],
    y: Annotated[int, Field(description="Y coordinate")],
    duration: Annotated[float, Field(description="Duration in seconds")] = 1.0,
) -> str:
    """Long press at coordinates."""
    client = get_wda_client(device_id)
    await client.long_press(x, y, duration)
    return f"Long pressed at ({x}, {y}) for {duration}s"


# === Alerts ===


@mcp.tool
async def accept_alert(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Accept the current alert dialog."""
    client = get_wda_client(device_id)
    await client.accept_alert()
    return "Alert accepted"


@mcp.tool
async def dismiss_alert(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Dismiss the current alert dialog."""
    client = get_wda_client(device_id)
    await client.dismiss_alert()
    return "Alert dismissed"


@mcp.tool
async def get_alert_text(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Get the text of the current alert dialog."""
    client = get_wda_client(device_id)
    text = await client.get_alert_text()
    if text:
        return f"Alert text: {text}"
    else:
        return "No alert present"


# === Session Management ===


@mcp.tool
async def reset_session(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Reset the WDA session (useful if session expires or has errors)."""
    await reset_wda_session(device_id)
    client = get_wda_client(device_id)
    if await client.health_check():
        await client.create_session()
        return f"Session reset. New session created (ID: {client.session_id})."
    else:
        return "Session reset, but WDA is not responding. Please restart WDA."


# === Status Bar ===


@mcp.tool
async def set_status_bar(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    time: Annotated[str | None, Field(description="Time string (e.g., '9:41')")] = None,
    battery_level: Annotated[
        int | None,
        Field(ge=0, le=100, description="Battery level 0-100"),
    ] = None,
    battery_state: Annotated[
        Literal["charging", "charged", "discharging"] | None,
        Field(description="Battery state"),
    ] = None,
    data_network: Annotated[
        Literal["hide", "wifi", "3g", "4g", "lte", "lte-a", "lte+", "5g", "5g+", "5g-uwb", "5g-uc"]
        | None,
        Field(description="Data network type"),
    ] = None,
    wifi_mode: Annotated[
        Literal["searching", "failed", "active"] | None,
        Field(description="WiFi mode"),
    ] = None,
    wifi_bars: Annotated[int | None, Field(ge=0, le=3, description="WiFi signal bars 0-3")] = None,
    cellular_mode: Annotated[
        Literal["notSupported", "searching", "failed", "active"] | None,
        Field(description="Cellular mode"),
    ] = None,
    cellular_bars: Annotated[int | None, Field(ge=0, le=4, description="Cellular bars 0-4")] = None,
    operator_name: Annotated[str | None, Field(description="Carrier name (empty to hide)")] = None,
) -> str:
    """Override status bar appearance for consistent screenshots."""
    overrides = [
        time,
        battery_level,
        battery_state,
        data_network,
        wifi_mode,
        wifi_bars,
        cellular_mode,
        cellular_bars,
        operator_name,
    ]
    if all(v is None for v in overrides):
        raise ValueError("At least one status bar override must be specified")

    await simulator_manager.status_bar_override(
        device_id,
        time=time,
        battery_level=battery_level,
        battery_state=battery_state,
        data_network=data_network,
        wifi_mode=wifi_mode,
        wifi_bars=wifi_bars,
        cellular_mode=cellular_mode,
        cellular_bars=cellular_bars,
        operator_name=operator_name,
    )

    changes = []
    if time is not None:
        changes.append(f"time={time}")
    if battery_level is not None:
        changes.append(f"battery={battery_level}%")
    if battery_state is not None:
        changes.append(f"battery_state={battery_state}")
    if data_network is not None:
        changes.append(f"network={data_network}")
    if wifi_mode is not None:
        changes.append(f"wifi={wifi_mode}")
    if wifi_bars is not None:
        changes.append(f"wifi_bars={wifi_bars}")
    if cellular_mode is not None:
        changes.append(f"cellular={cellular_mode}")
    if cellular_bars is not None:
        changes.append(f"cellular_bars={cellular_bars}")
    if operator_name is not None:
        changes.append(f"operator={operator_name or '(hidden)'}")

    return f"Status bar updated: {', '.join(changes)}"


@mcp.tool
async def clear_status_bar(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Clear all status bar overrides and return to normal."""
    await simulator_manager.status_bar_clear(device_id)
    return "Status bar overrides cleared"


# === Keyboard ===


@mcp.tool
async def dismiss_keyboard(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Dismiss the on-screen keyboard if visible."""
    client = get_wda_client(device_id)
    await client.dismiss_keyboard()
    return "Keyboard dismissed"


# === Appearance (Dark/Light Mode) ===


@mcp.tool
async def set_appearance(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    appearance: Annotated[Literal["dark", "light"], Field(description="Appearance mode to set")],
) -> str:
    """Set device appearance (dark mode or light mode)."""
    client = get_wda_client(device_id)
    await client.set_appearance(appearance)
    return f"Appearance set to: {appearance}"


@mcp.tool
async def get_appearance(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Get current device appearance (dark/light mode)."""
    client = get_wda_client(device_id)
    appearance = await client.get_appearance()
    return f"Current appearance: {appearance}"


# === Biometrics ===


@mcp.tool
async def simulate_biometrics(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    match: Annotated[
        bool,
        Field(description="True for successful authentication, False for failure"),
    ] = True,
) -> str:
    """Simulate Touch ID or Face ID authentication (success or failure)."""
    client = get_wda_client(device_id)
    await client.simulate_biometrics(match=match)
    result = "successful" if match else "failed"
    return f"Simulated biometric authentication: {result}"


# === Screen Recording ===


@mcp.tool
async def start_recording(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    codec: Annotated[Literal["hevc", "h264"], Field(description="Video codec")] = "hevc",
) -> str:
    """Start screen recording. Use stop_recording to save the video (.mov file)."""
    if simulator_manager.is_recording(device_id):
        return "Recording already in progress. Use stop_recording first."

    ensure_screenshot_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    video_dir = SCREENSHOT_DIR.parent / "recordings"
    video_dir.mkdir(parents=True, exist_ok=True)
    filepath = video_dir / f"recording-{timestamp}.mov"

    _recording_paths[device_id] = filepath

    await simulator_manager.start_recording(device_id, filepath, codec=codec)
    return f"Screen recording started (codec={codec})\nWill save to: {filepath}"


@mcp.tool
async def stop_recording(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Stop screen recording and save the video file."""
    if not simulator_manager.is_recording(device_id):
        return "No recording in progress"

    filepath = _recording_paths.pop(device_id, None)
    stopped = await simulator_manager.stop_recording(device_id)

    if stopped and filepath and filepath.exists():
        file_size = filepath.stat().st_size / 1024
        return f"Screen recording saved: {filepath}\nSize: {file_size:.1f}KB"
    elif stopped:
        return "Recording stopped but file may still be processing"
    else:
        return "No recording was in progress"


# === Pinch Gesture ===


@mcp.tool
async def pinch(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    x: Annotated[int, Field(description="Center X coordinate for pinch")],
    y: Annotated[int, Field(description="Center Y coordinate for pinch")],
    scale: Annotated[float, Field(description="Scale factor: <1.0 to zoom out, >1.0 to zoom in")],
    velocity: Annotated[
        float,
        Field(description="Pinch velocity in scale factor per second"),
    ] = 1.0,
) -> str:
    """Perform a pinch gesture (zoom in/out) at coordinates."""
    client = get_wda_client(device_id)
    await client.pinch(x, y, scale, velocity)

    action = "zoom in" if scale > 1.0 else "zoom out"
    return f"Pinch gesture at ({x}, {y}) with scale {scale} ({action})"


# === DTD Discovery ===


@mcp.tool
async def discover_dtd_uris(
    timeout: Annotated[float, Field(description="Timeout in seconds for probing each port")] = 2.0,
) -> str:
    """Discover running Dart Tooling Daemon (DTD) URIs for Flutter debugging.

    These URIs can be used with the Dart MCP server's connect_dart_tooling_daemon tool
    for hot reload, widget inspection, and other Flutter debugging features.
    """
    import glob as glob_module

    import httpx

    discovered = []
    seen_uris: set[str] = set()

    def add_uri(dtd_uri: str, http_uri: str = "", process: str = "", vm_name: str = ""):
        if dtd_uri in seen_uris:
            return
        seen_uris.add(dtd_uri)
        discovered.append({
            "dtd_uri": dtd_uri,
            "http_uri": http_uri or dtd_uri.replace("ws://", "http://").replace("/ws", "/"),
            "process": process,
            "vm_name": vm_name or "Dart VM",
        })

    # Step 1: Look for VM service URIs in process command lines
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
        uri_pattern = re.compile(
            r'((?:ws|http)s?://(?:127\.0\.0\.1|localhost):\d+/[A-Za-z0-9+/]+=*/?)(?:ws)?'
        )

        for line in result.stdout.split("\n"):
            lower_line = line.lower()
            if "dart" in lower_line or "flutter" in lower_line:
                matches = uri_pattern.findall(line)
                for match in matches:
                    http_uri = match.replace("ws://", "http://").replace("wss://", "https://")
                    http_uri = http_uri.rstrip("/")
                    if http_uri.endswith("/ws"):
                        http_uri = http_uri[:-3]
                    if not http_uri.endswith("="):
                        http_uri += "="
                    http_uri += "/"

                    ws_uri = http_uri.replace("http://", "ws://").replace("https://", "wss://")
                    ws_uri = ws_uri.rstrip("/") + "/ws"

                    parts = line.split()
                    process_name = parts[10] if len(parts) > 10 else "dart"
                    add_uri(ws_uri, http_uri, process_name)
    except Exception as e:
        logger.debug(f"Error scanning processes: {e}")

    # Step 2: Check Flutter tool state files
    try:
        flutter_state_patterns = [
            str(Path.home() / ".flutter_tool_state"),
            "/tmp/flutter_tools.*",
        ]

        for pattern in flutter_state_patterns:
            for state_dir in glob_module.glob(pattern):
                if os.path.isdir(state_dir):
                    for filename in os.listdir(state_dir):
                        filepath = os.path.join(state_dir, filename)
                        if os.path.isfile(filepath):
                            try:
                                with open(filepath) as f:
                                    content = f.read()
                                    uri_pattern = re.compile(
                                        r'((?:ws|http)s?://(?:127\.0\.0\.1|localhost):\d+/[A-Za-z0-9+/]+=*/?)(?:ws)?'
                                    )
                                    matches = uri_pattern.findall(content)
                                    for match in matches:
                                        http_uri = match.replace("ws://", "http://").replace(
                                            "wss://",
                                            "https://",
                                        )
                                        http_uri = http_uri.rstrip("/")
                                        if http_uri.endswith("/ws"):
                                            http_uri = http_uri[:-3]
                                        if not http_uri.endswith("="):
                                            http_uri += "="
                                        http_uri += "/"

                                        ws_uri = http_uri.replace("http://", "ws://").replace(
                                            "https://",
                                            "wss://",
                                        )
                                        ws_uri = ws_uri.rstrip("/") + "/ws"
                                        add_uri(ws_uri, http_uri, "flutter (from state file)")
                            except Exception:
                                pass
    except Exception as e:
        logger.debug(f"Error checking Flutter state files: {e}")

    # Step 3: Find dart processes with listening ports and probe them
    try:
        result = subprocess.run(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-n", "-P"],
            capture_output=True, text=True, timeout=10,
        )

        candidate_ports: list[tuple[int, str]] = []

        for line in result.stdout.split("\n"):
            lower_line = line.lower()
            if "dart" in lower_line or "flutter" in lower_line:
                match = re.search(r":(\d+)\s*$", line)
                if match:
                    port = int(match.group(1))
                    parts = line.split()
                    process_name = parts[0] if parts else "unknown"
                    candidate_ports.append((port, process_name))

        async with httpx.AsyncClient(timeout=timeout) as client:
            for port, process_name in candidate_ports:
                if any(str(port) in uri for uri in seen_uris):
                    continue

                try:
                    base_url = f"http://127.0.0.1:{port}"
                    response = await client.get(f"{base_url}/getVM", timeout=timeout)
                    if response.status_code == 200:
                        data = response.json()
                        vm_name = data.get("result", {}).get("name", "Dart VM")
                        ws_uri = f"ws://127.0.0.1:{port}/ws"
                        add_uri(
                            ws_uri,
                            base_url,
                            process_name,
                            f"{vm_name} (auth token may be required)",
                        )
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Error probing ports: {e}")

    # Step 4: Check macOS Console logs
    try:
        result = subprocess.run(
            [
                "log", "show",
                "--predicate",
                'processImagePath CONTAINS "dart" OR processImagePath CONTAINS "flutter"',
                "--last", "5m",
                "--style", "compact",
            ],
            capture_output=True, text=True, timeout=15,
        )

        uri_pattern = re.compile(
            r'((?:ws|http)s?://(?:127\.0\.0\.1|localhost):\d+/[A-Za-z0-9+/]+=*/?)(?:ws)?'
        )
        matches = uri_pattern.findall(result.stdout)
        for match in matches:
            http_uri = match.replace("ws://", "http://").replace("wss://", "https://")
            http_uri = http_uri.rstrip("/")
            if http_uri.endswith("/ws"):
                http_uri = http_uri[:-3]
            if not http_uri.endswith("="):
                http_uri += "="
            http_uri += "/"

            ws_uri = http_uri.replace("http://", "ws://").replace("https://", "wss://")
            ws_uri = ws_uri.rstrip("/") + "/ws"
            add_uri(ws_uri, http_uri, "flutter (from system logs)")
    except Exception as e:
        logger.debug(f"Error checking system logs: {e}")

    if not discovered:
        return (
            "No running Dart VM services found.\n\n"
            "To get a DTD URI:\n"
            "1. Run a Flutter app in debug mode: flutter run\n"
            "2. The DTD URI is printed when the app starts (looks like ws://127.0.0.1:XXXXX/...)\n"
            "3. In VS Code, use 'Dart: Copy DTD Uri to Clipboard' command\n"
            "4. In Android Studio, check the Debug console for the VM service URI"
        )

    result_lines = ["Found running Dart VM services:\n"]
    for uri_info in discovered:
        result_lines.append(f"- {uri_info['dtd_uri']}")
        if uri_info.get("process"):
            result_lines.append(f"  Process: {uri_info['process']}")
        if uri_info.get("vm_name"):
            result_lines.append(f"  VM: {uri_info['vm_name']}")
        result_lines.append("")

    result_lines.append(
        "\nUse one of these URIs with the Dart MCP server's "
        "connect_dart_tooling_daemon tool."
    )
    return "\n".join(result_lines)


# === Resources ===


API_REFERENCE = """# iOS Simulator MCP API Reference

## Device Management

| Tool | Description |
|------|-------------|
| list_devices | List all iOS simulators |
| get_device | Get device info by UDID |
| boot_simulator | Boot a simulator |
| shutdown_simulator | Shutdown a simulator |
| start_bridge | Check/connect to WebDriverAgent |

## UI Automation

| Tool | Description |
|------|-------------|
| get_screenshot | Capture screenshot |
| get_ui_tree | Get accessibility tree with element indices |
| tap | Tap element by index, predicate, or coordinates |
| type_text | Type text (optionally tap element first) |
| swipe | Swipe gesture by direction or coordinates |
| double_tap | Double tap at coordinates |
| long_press | Long press at coordinates |

## Navigation

| Tool | Description |
|------|-------------|
| go_home | Navigate to home screen |
| launch_app | Launch app by bundle ID |
| terminate_app | Terminate app |
| list_apps | List installed apps |
| open_url | Open URL in Safari |
| press_button | Press hardware button |

## System

| Tool | Description |
|------|-------------|
| set_location | Set GPS location |
| get_clipboard | Get clipboard content |
| set_clipboard | Set clipboard content |
| get_window_size | Get screen dimensions |
| set_status_bar | Override status bar appearance |
| clear_status_bar | Clear status bar overrides |
| dismiss_keyboard | Dismiss on-screen keyboard |
| set_appearance | Set dark/light mode |
| get_appearance | Get current dark/light mode |
| simulate_biometrics | Simulate Touch ID/Face ID |
| start_recording | Start screen recording |
| stop_recording | Stop recording and save video |
| pinch | Pinch gesture (zoom in/out) |

## Dart MCP Integration

| Tool | Description |
|------|-------------|
| discover_dtd_uris | Discover running Dart Tooling Daemon URIs for Flutter debugging |

## Alerts

| Tool | Description |
|------|-------------|
| accept_alert | Accept alert dialog |
| dismiss_alert | Dismiss alert dialog |
| get_alert_text | Get alert text |

## Prerequisites

1. **Xcode Command Line Tools**: `xcode-select --install`
2. **WebDriverAgent**: Required for most automation. Start with:
   ```
   xcodebuild -project WebDriverAgent.xcodeproj \\
     -scheme WebDriverAgentRunner \\
     -destination 'platform=iOS Simulator,id=<UDID>' test
   ```

## Common Bundle IDs

- Settings: `com.apple.Preferences`
- Safari: `com.apple.mobilesafari`
- Maps: `com.apple.Maps`
- Photos: `com.apple.Photos`
- Calendar: `com.apple.mobilecal`
- Notes: `com.apple.mobilenotes`
"""

AUTOMATION_GUIDE = """# iOS Simulator Automation Guide

## Workflow

1. **List/Boot Simulator**
   ```
   list_devices → get UDID
   boot_simulator → start simulator
   ```

2. **Start WebDriverAgent** (required for UI automation)
   ```
   start_bridge → check WDA status
   ```

3. **Get UI Tree**
   ```
   get_ui_tree → see elements with indices
   ```

4. **Interact**
   ```
   tap index=5 → tap element [5]
   type_text text="hello" → type text
   swipe direction="up" → scroll
   ```

## Element Selection

### By Index
```
tap device_id="..." index=5
```

### By Predicate
```
tap device_id="..." predicate={"text_contains": "Settings"}
tap device_id="..." predicate={"type": "Button", "text": "OK"}
```

### By Coordinates
```
tap device_id="..." x=200 y=400
```

## Predicate Fields

- `text`: Exact text match
- `text_contains`: Contains substring (case-insensitive)
- `text_starts_with`: Starts with prefix
- `type`: Element type (Button, TextField, etc.)
- `label`: Accessibility label
- `identifier`: Accessibility identifier
- `index`: Select Nth match (0-based)

## Tips

1. **Always get UI tree first** before tapping by index
2. **Use predicates** for more robust automation
3. **Add delays** after navigation for UI to settle
4. **Use simctl** for screenshots (faster than WDA)
5. **Check alerts** that might block automation
"""


@mcp.resource("ios-sim://api-reference")
def get_api_reference() -> str:
    """iOS Simulator MCP API Reference - Complete API documentation."""
    return API_REFERENCE


@mcp.resource("ios-sim://automation-guide")
def get_automation_guide() -> str:
    """iOS Simulator Automation Guide - Guide for automating iOS simulators."""
    return AUTOMATION_GUIDE


# Backward-compatible export for existing imports:
# `from ios_simulator_mcp.server import server`
server = mcp


# === Main Entry Point ===


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
