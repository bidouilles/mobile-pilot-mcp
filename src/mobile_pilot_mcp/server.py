"""MCP Server for iOS Simulator automation via WebDriverAgent.

This server uses FastMCP for cleaner decorator-based tool definitions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
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
logger = logging.getLogger("mobile-pilot-mcp")

# Constants
SCREENSHOT_DIR = Path("/tmp/mobile-pilot-mcp/screenshots")
DEFAULT_WDA_PORT = 8100

# Global state
simulator_manager = SimulatorManager()
wda_clients: dict[str, WDAClient] = {}
_last_ui_elements: dict[str, list] = {}
_recording_paths: dict[str, Path] = {}

# WDA host configuration
WDA_HOST = os.environ.get("WDA_HOST", "127.0.0.1")


# === Helper Functions ===


def get_wda_client(
    device_id: str,
    port: int | None = None,
    host: str | None = None,
) -> WDAClient:
    """Get or create a WDA client for a device.

    Called without host/port, it returns the client the device was last bridged
    with, so tools keep using the address given to `start_bridge`.
    """
    if host is None and port is None and device_id in wda_clients:
        return wda_clients[device_id]

    actual_host = host or WDA_HOST
    actual_port = port or DEFAULT_WDA_PORT
    key = f"{device_id}:{actual_host}:{actual_port}"

    if key not in wda_clients:
        wda_clients[key] = WDAClient(host=actual_host, port=actual_port)
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


# === Dashboard Tracking ===


def _result_text(result: Any) -> str | None:
    """Text of a tool result, for the dashboard log."""
    if result is None or isinstance(result, str):
        return result
    parts = [c.text for c in getattr(result, "content", None) or [] if hasattr(c, "text")]
    return "\n".join(parts) if parts else str(result)


class DashboardTrackingMiddleware(Middleware):
    """Shows every MCP tool call in the dashboard."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        message = context.message
        tool_call = _track_tool_call(message.name, dict(message.arguments or {}))
        try:
            result = await call_next(context)
        except Exception as e:
            _complete_tool_call(tool_call, error=str(e))
            raise
        _complete_tool_call(tool_call, result=_result_text(result))
        return result


# === Lifespan for Dashboard ===


@asynccontextmanager
async def lifespan(mcp: FastMCP):
    """Manage dashboard server lifecycle."""
    logger.info("=" * 60)
    logger.info("Mobile Pilot MCP Server starting...")
    logger.info(f"WDA_HOST: {WDA_HOST}")
    logger.info(f"WDA_PORT: {DEFAULT_WDA_PORT}")
    logger.info("WDA port override: pass `port` to `start_bridge` per connection")
    logger.info(f"Screenshot dir: {SCREENSHOT_DIR}")
    logger.info(f"Dashboard port: {DASHBOARD_PORT}")
    logger.info(f"Log level: {LOG_LEVEL}")
    logger.info("=" * 60)

    # Dashboard quick actions call the tool function directly (they do not go
    # through the MCP protocol), so they are tracked here, not by the middleware.
    async def execute_tool_from_dashboard(name: str, args: dict[str, Any]) -> str:
        """Execute a tool from dashboard quick actions."""
        try:
            tool = await mcp.get_tool(name)
        except Exception as exc:
            raise ValueError(f"Unknown tool: {name}") from exc
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        tool_call = _track_tool_call(name, args)
        try:
            result = await tool.fn(**args)
        except Exception as exc:
            _complete_tool_call(tool_call, error=str(exc))
            raise
        _complete_tool_call(tool_call, result=_result_text(result))
        return result

    dashboard_state.tool_executor = execute_tool_from_dashboard

    # Start dashboard server (best-effort).
    # Some MCP runtimes restrict local port binding; the MCP server should
    # still run even when the dashboard cannot start.
    dashboard_runner = None
    try:
        dashboard_runner = await start_dashboard()
    except Exception as exc:
        logger.warning("Dashboard disabled (failed to start): %s", exc)
    logger.info("Server ready, waiting for MCP client connection...")

    try:
        yield
    finally:
        if dashboard_runner is not None:
            await stop_dashboard(dashboard_runner)

        # Finalize any recording still running so the .mov is not left corrupt.
        try:
            await simulator_manager.stop_all_recordings()
        except Exception as exc:
            logger.debug("Error stopping recordings: %s", exc)

        # Ensure HTTP clients are released when the server exits.
        unique_clients = {id(client): client for client in wda_clients.values()}.values()
        for client in unique_clients:
            try:
                await client.close()
            except Exception as exc:
                logger.debug("Error closing WDA client: %s", exc)

        wda_clients.clear()
        _last_ui_elements.clear()
        _recording_paths.clear()


# === Create FastMCP Server ===

mcp = FastMCP(
    "mobile-pilot-mcp",
    lifespan=lifespan,
    middleware=[DashboardTrackingMiddleware()],
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
    port: Annotated[
        int | None,
        Field(description="WDA port (default: the port already bridged, else 8100)"),
    ] = None,
    host: Annotated[
        str | None,
        Field(description="WDA host (default: the host already bridged, else WDA_HOST env var)"),
    ] = None,
) -> str:
    """Check WebDriverAgent connection (WDA must be running separately via xcodebuild)."""
    previous = wda_clients.get(device_id)
    client = get_wda_client(device_id, port, host)
    address = client.base_url.removeprefix("http://")

    healthy = await client.health_check()
    if not healthy:
        # Keep a failed attempt from replacing the bridge the device already had.
        if previous is not None:
            wda_clients[device_id] = previous
        else:
            wda_clients.pop(device_id, None)

    if healthy:
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
                "wda_host": address,
            })

        return (
            f"WebDriverAgent is running at {address}. "
            f"Session created (ID: {client.session_id})."
        )
    else:
        return (
            f"WebDriverAgent is not responding at {address}.\n\n"
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
    scale: Annotated[float, Field(ge=0.1, le=1.0, description="Scale factor 0.1-1.0")] = 0.5,
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
        # Fix landscape screenshots: simctl captures the raw framebuffer
        # in portrait orientation, so landscape content appears rotated.
        if img.height > img.width:
            # Ask WDA even when start_bridge was not called yet; without it a
            # landscape device would yield a sideways screenshot.
            try:
                client = get_wda_client(device_id)
                orientation = (await asyncio.wait_for(client.get_orientation(), 3.0)).upper()
                if orientation in ("LANDSCAPE", "LANDSCAPE_LEFT"):
                    img = img.transpose(Image.Transpose.ROTATE_90)
                elif orientation == "LANDSCAPE_RIGHT":
                    img = img.transpose(Image.Transpose.ROTATE_270)
            except Exception as e:
                logger.debug("Could not query orientation for rotation: %s", e)

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

        if index < 0 or index >= len(elements):
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


# === Wait ===


@mcp.tool
async def wait_for_element(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    predicate: Annotated[
        dict[str, Any],
        Field(description='Element predicate, e.g. {"text": "Done"} or {"identifier": "save"}'),
    ],
    timeout: Annotated[float, Field(ge=0, le=120, description="Seconds to wait")] = 10.0,
    gone: Annotated[
        bool,
        Field(description="Wait for the element to disappear instead of appear"),
    ] = False,
) -> str:
    """Wait until an element appears (or disappears). Use after navigation instead of polling.

    When the element is found, its index is valid for `tap index=N` right away.
    """
    client = get_wda_client(device_id)
    parser = UITreeParser()
    started = time.monotonic()

    while True:
        source = await client.get_source(format="json")
        _, elements = parser.parse(source, only_visible=True)
        elem = find_element_by_predicate(elements, predicate)
        waited = time.monotonic() - started

        if gone and elem is None:
            return f"Element gone after {waited:.1f}s: {predicate}"
        if not gone and elem is not None:
            _last_ui_elements[device_id] = elements
            text = f' "{elem.display_text}"' if elem.display_text else ""
            return (
                f"Found [{elem.index}] {elem.element_type}{text} "
                f"at ({elem.center_x}, {elem.center_y}) after {waited:.1f}s"
            )
        if waited >= timeout:
            state = "still present" if gone else "not found"
            return f"Timed out after {waited:.1f}s: element {state}: {predicate}"

        await asyncio.sleep(0.3)


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
async def get_app_state(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    bundle_id: Annotated[str, Field(description="App bundle ID")],
) -> str:
    """Report whether an app is installed, running in the background, or in the foreground."""
    states = {
        0: "not installed",
        1: "not running (or not installed)",
        2: "running in background (suspended)",
        3: "running in background",
        4: "running in foreground",
    }
    client = get_wda_client(device_id)
    state = await client.get_app_state(bundle_id)
    return f"{bundle_id}: {states.get(state, f'unknown state {state}')}"


@mcp.tool
async def install_app(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    app_path: Annotated[str, Field(description="Path to a simulator .app bundle on this Mac")],
) -> str:
    """Install a simulator build (.app) on the simulator."""
    path = Path(app_path).expanduser()
    if not path.exists():
        return f"App not found: {path}"
    await simulator_manager.install_app(device_id, path)
    return f"Installed: {path}"


@mcp.tool
async def uninstall_app(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    bundle_id: Annotated[str, Field(description="App bundle ID")],
) -> str:
    """Uninstall an app from the simulator (its data is removed too)."""
    await simulator_manager.uninstall_app(device_id, bundle_id)
    return f"Uninstalled: {bundle_id}"


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
        Literal["home", "action", "volumeUp", "volumeDown"],
        Field(description="Button to press (volume buttons need a real device)"),
    ],
) -> str:
    """Press a hardware button (home, action; volume buttons on real devices only)."""
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


@mcp.tool
async def set_orientation(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    orientation: Annotated[Literal["PORTRAIT", "LANDSCAPE"], Field(description="Orientation")],
) -> str:
    """Rotate the device to portrait or landscape."""
    client = get_wda_client(device_id)
    await client.set_orientation(orientation)
    return f"Orientation set to: {orientation}"


@mcp.tool
async def send_push_notification(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    bundle_id: Annotated[str, Field(description="Bundle ID of the app that receives the push")],
    title: Annotated[str | None, Field(description="Alert title")] = None,
    body: Annotated[str | None, Field(description="Alert body")] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Field(description='Full APNs payload, e.g. {"aps": {...}}; replaces title and body'),
    ] = None,
) -> str:
    """Deliver a simulated push notification to an app."""
    if payload is None:
        if title is None and body is None:
            return "Please provide title/body or a payload"
        alert = {k: v for k, v in (("title", title), ("body", body)) if v is not None}
        payload = {"aps": {"alert": alert}}
    await simulator_manager.push_notification(device_id, bundle_id, payload)
    return f"Push notification sent to {bundle_id}"


@mcp.tool
async def add_media(
    device_id: Annotated[str, Field(description="Simulator UDID")],
    media_path: Annotated[str, Field(description="Path to a photo or video on this Mac")],
) -> str:
    """Add a photo or video to the simulator's Photos library."""
    path = Path(media_path).expanduser()
    if not path.exists():
        return f"File not found: {path}"
    await simulator_manager.add_media(device_id, path)
    return f"Added to Photos: {path}"


@mcp.tool
async def clear_keychain(device_id: Annotated[str, Field(description="Simulator UDID")]) -> str:
    """Reset the simulator's keychain (signs apps out of anything stored there)."""
    await simulator_manager.clear_keychain(device_id)
    return "Keychain cleared"


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
    filepath = _recording_paths.pop(device_id, None)
    exit_code = simulator_manager.recording_exit_code(device_id)
    stopped = await simulator_manager.stop_recording(device_id)

    if stopped and exit_code is not None:
        return (
            f"The recorder had already exited on its own (code {exit_code}), "
            f"so the video may be missing or incomplete: {filepath}"
        )
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


# === Resources ===


API_REFERENCE = """# Mobile Pilot MCP API Reference

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
| wait_for_element | Wait until an element appears or disappears |
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
| get_app_state | Check if an app is installed / running |
| install_app | Install a simulator .app |
| uninstall_app | Uninstall an app |
| open_url | Open URL in Safari |
| press_button | Press hardware button |

## System

| Tool | Description |
|------|-------------|
| set_location | Set GPS location |
| set_orientation | Rotate to portrait or landscape |
| send_push_notification | Deliver a simulated push notification |
| add_media | Add a photo or video to Photos |
| clear_keychain | Reset the keychain |
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

AUTOMATION_GUIDE = """# Mobile Pilot MCP Automation Guide

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
3. **Use wait_for_element** after navigation instead of fixed delays
4. **Use simctl** for screenshots (faster than WDA)
5. **Check alerts** that might block automation
"""


@mcp.resource("mobile-pilot://api-reference")
def get_api_reference() -> str:
    """Mobile Pilot MCP API Reference - Complete API documentation."""
    return API_REFERENCE


@mcp.resource("mobile-pilot://automation-guide")
def get_automation_guide() -> str:
    """Mobile Pilot MCP Automation Guide - Guide for automating mobile simulators."""
    return AUTOMATION_GUIDE


# Backward-compatible export for existing imports:
# `from mobile_pilot_mcp.server import server`
server = mcp


# === Main Entry Point ===


def main():
    """Run the MCP server."""
    # Codex connects over stdio; any non-protocol stdout output can break the
    # MCP handshake, so disable FastMCP banner output explicitly.
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
