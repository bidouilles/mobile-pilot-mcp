"""MCP Server for iOS Simulator automation via WebDriverAgent."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
    Resource,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)

from .simulator import SimulatorManager, SimulatorError
from .wda_client import WDAClient, WDAError
from .ui_tree import UITreeParser, find_element_by_predicate
from .dashboard import dashboard_state, start_dashboard, stop_dashboard, DASHBOARD_PORT

# Configure logging - DEBUG level shows all tool calls
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()

# Custom handler that flushes immediately
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
wda_clients: dict[str, WDAClient] = {}  # udid -> WDAClient

# WDA host configuration (can be set via environment variable)
WDA_HOST = os.environ.get("WDA_HOST", "127.0.0.1")


def get_wda_client(device_id: str, port: int = DEFAULT_WDA_PORT, host: str | None = None) -> WDAClient:
    """Get or create a WDA client for a device."""
    actual_host = host or WDA_HOST
    key = f"{device_id}:{actual_host}:{port}"

    if key not in wda_clients:
        wda_clients[key] = WDAClient(host=actual_host, port=port)
        # Also store with just device_id for backward compatibility
        wda_clients[device_id] = wda_clients[key]

    return wda_clients[key]


async def reset_wda_session(device_id: str) -> None:
    """Reset WDA session for a device (useful if session expires)."""
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


async def discover_dart_vm_services(timeout: float = 2.0) -> list[dict[str, Any]]:
    """
    Discover running Dart VM services (DTD URIs) on the local machine.

    The DTD URI is the WebSocket URI used by the Dart Tooling Daemon, which enables
    features like hot reload, widget inspection, and runtime error reporting.

    Returns a list of dicts with:
    - dtd_uri: The WebSocket URI for the DTD (e.g., ws://127.0.0.1:PORT/TOKEN=/ws)
    - http_uri: The HTTP URI (e.g., http://127.0.0.1:PORT/TOKEN=/)
    - process: Process info if available
    - vm_name: VM name if available
    """
    import subprocess
    import httpx
    import re
    import glob

    discovered = []
    seen_uris: set[str] = set()

    def add_uri(dtd_uri: str, http_uri: str = "", process: str = "", vm_name: str = ""):
        """Add a URI if not already seen."""
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
    # This is the most reliable way to get the full URI with auth token
    try:
        # Use ps to get all process command lines
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Pattern to match Dart VM service URIs
        # Format: http://127.0.0.1:PORT/TOKEN=/ or ws://127.0.0.1:PORT/TOKEN=/ws
        uri_pattern = re.compile(
            r'((?:ws|http)s?://(?:127\.0\.0\.1|localhost):\d+/[A-Za-z0-9+/]+=*/?)(?:ws)?'
        )

        for line in result.stdout.split("\n"):
            lower_line = line.lower()
            if "dart" in lower_line or "flutter" in lower_line:
                # Extract URI from the command line
                matches = uri_pattern.findall(line)
                for match in matches:
                    # Normalize to HTTP URI first
                    http_uri = match.replace("ws://", "http://").replace("wss://", "https://")
                    http_uri = http_uri.rstrip("/")
                    if http_uri.endswith("/ws"):
                        http_uri = http_uri[:-3]
                    if not http_uri.endswith("="):
                        http_uri += "="
                    http_uri += "/"

                    # Create WebSocket URI
                    ws_uri = http_uri.replace("http://", "ws://").replace("https://", "wss://")
                    ws_uri = ws_uri.rstrip("/") + "/ws"

                    # Extract process name
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
            for state_dir in glob.glob(pattern):
                if os.path.isdir(state_dir):
                    # Look for files that might contain VM service URIs
                    for filename in os.listdir(state_dir):
                        filepath = os.path.join(state_dir, filename)
                        if os.path.isfile(filepath):
                            try:
                                with open(filepath) as f:
                                    content = f.read()
                                    # Look for URIs in the file
                                    uri_pattern = re.compile(
                                        r'((?:ws|http)s?://(?:127\.0\.0\.1|localhost):\d+/[A-Za-z0-9+/]+=*/?)(?:ws)?'
                                    )
                                    matches = uri_pattern.findall(content)
                                    for match in matches:
                                        # Normalize to HTTP URI first
                                        http_uri = match.replace("ws://", "http://").replace("wss://", "https://")
                                        http_uri = http_uri.rstrip("/")
                                        if http_uri.endswith("/ws"):
                                            http_uri = http_uri[:-3]
                                        if not http_uri.endswith("="):
                                            http_uri += "="
                                        http_uri += "/"

                                        # Create WebSocket URI
                                        ws_uri = http_uri.replace("http://", "ws://").replace("https://", "wss://")
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
            capture_output=True,
            text=True,
            timeout=10,
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

        # Probe each candidate port
        async with httpx.AsyncClient(timeout=timeout) as client:
            for port, process_name in candidate_ports:
                # Skip if we already found URIs for this port
                if any(str(port) in uri for uri in seen_uris):
                    continue

                try:
                    # Try to access the VM service without auth token
                    # Some endpoints might respond
                    base_url = f"http://127.0.0.1:{port}"

                    # Try getVM endpoint
                    response = await client.get(f"{base_url}/getVM", timeout=timeout)
                    if response.status_code == 200:
                        data = response.json()
                        vm_name = data.get("result", {}).get("name", "Dart VM")

                        # Note: Without auth token, this might not be the full URI
                        # but we report it as a partial match
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

    # Step 4: Check macOS Console logs for recent VM service URIs
    try:
        # Look for recent log entries with VM service URIs
        result = subprocess.run(
            [
                "log", "show",
                "--predicate", 'processImagePath CONTAINS "dart" OR processImagePath CONTAINS "flutter"',
                "--last", "5m",
                "--style", "compact",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        uri_pattern = re.compile(
            r'((?:ws|http)s?://(?:127\.0\.0\.1|localhost):\d+/[A-Za-z0-9+/]+=*/?)(?:ws)?'
        )
        matches = uri_pattern.findall(result.stdout)
        for match in matches:
            # Normalize to HTTP URI first
            http_uri = match.replace("ws://", "http://").replace("wss://", "https://")
            http_uri = http_uri.rstrip("/")
            if http_uri.endswith("/ws"):
                http_uri = http_uri[:-3]
            if not http_uri.endswith("="):
                http_uri += "="
            http_uri += "/"

            # Create WebSocket URI
            ws_uri = http_uri.replace("http://", "ws://").replace("https://", "wss://")
            ws_uri = ws_uri.rstrip("/") + "/ws"

            add_uri(ws_uri, http_uri, "flutter (from system logs)")
    except Exception as e:
        logger.debug(f"Error checking system logs: {e}")

    return discovered


# Create the MCP server
server = Server("ios-simulator-mcp")


# === Tool Definitions ===

TOOLS = [
    Tool(
        name="list_devices",
        description="List all iOS simulators (booted and available)",
        inputSchema={
            "type": "object",
            "properties": {
                "only_booted": {
                    "type": "boolean",
                    "description": "Only list booted simulators (default: false)",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_device",
        description="Get information about a specific simulator",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="boot_simulator",
        description="Boot an iOS simulator",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="shutdown_simulator",
        description="Shutdown an iOS simulator",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="start_bridge",
        description="Check WebDriverAgent connection (WDA must be running separately via xcodebuild or other means)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "port": {
                    "type": "integer",
                    "description": "WDA port (default: 8100)",
                },
                "host": {
                    "type": "string",
                    "description": "WDA host (default: 127.0.0.1, or WDA_HOST env var)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="get_screenshot",
        description="Capture a screenshot from the simulator. Returns the file path. Supports resizing and format options to reduce file size.",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "scale": {
                    "type": "number",
                    "description": "Scale factor 0.1-1.0 (default: 0.5 = half size, good balance of quality/size)",
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "jpeg"],
                    "description": "Image format (default: jpeg for smaller files)",
                },
                "quality": {
                    "type": "integer",
                    "description": "JPEG quality 1-100 (default: 85, ignored for PNG)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="get_ui_tree",
        description="Get the UI accessibility tree showing all visible elements with indices for tapping",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "verbose": {
                    "type": "boolean",
                    "description": "Include element bounds (default: false)",
                },
                "only_visible": {
                    "type": "boolean",
                    "description": "Only visible elements (default: true)",
                },
                "format": {
                    "type": "string",
                    "enum": ["tree", "flat", "json"],
                    "description": "Output format (default: tree)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="tap",
        description="Tap an element by index (from UI tree) or coordinates",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "index": {
                    "type": "integer",
                    "description": "Element index from UI tree",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate (use with y)",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate (use with x)",
                },
                "predicate": {
                    "type": "object",
                    "description": "Element predicate (text, text_contains, type, etc.)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="type_text",
        description="Type text (tap input field first to focus, or provide predicate)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type",
                },
                "predicate": {
                    "type": "object",
                    "description": "Optional: tap this element first",
                },
            },
            "required": ["device_id", "text"],
        },
    ),
    Tool(
        name="swipe",
        description="Perform a swipe gesture",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "from_x": {
                    "type": "integer",
                    "description": "Starting X coordinate",
                },
                "from_y": {
                    "type": "integer",
                    "description": "Starting Y coordinate",
                },
                "to_x": {
                    "type": "integer",
                    "description": "Ending X coordinate",
                },
                "to_y": {
                    "type": "integer",
                    "description": "Ending Y coordinate",
                },
                "duration_ms": {
                    "type": "integer",
                    "description": "Duration in milliseconds (default: 300)",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "description": "Swipe direction (alternative to coordinates)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="go_home",
        description="Navigate to home screen",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="launch_app",
        description="Launch an application by bundle ID",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "bundle_id": {
                    "type": "string",
                    "description": "App bundle ID (e.g., com.apple.Preferences)",
                },
            },
            "required": ["device_id", "bundle_id"],
        },
    ),
    Tool(
        name="terminate_app",
        description="Terminate an application",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "bundle_id": {
                    "type": "string",
                    "description": "App bundle ID",
                },
            },
            "required": ["device_id", "bundle_id"],
        },
    ),
    Tool(
        name="list_apps",
        description="List installed applications on the simulator",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="open_url",
        description="Open a URL in the simulator (opens in Safari or associated app)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "url": {
                    "type": "string",
                    "description": "URL to open",
                },
            },
            "required": ["device_id", "url"],
        },
    ),
    Tool(
        name="press_button",
        description="Press a hardware button (home, volumeUp, volumeDown)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "button": {
                    "type": "string",
                    "enum": ["home", "volumeUp", "volumeDown"],
                    "description": "Button to press",
                },
            },
            "required": ["device_id", "button"],
        },
    ),
    Tool(
        name="set_location",
        description="Set the simulator's GPS location",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "latitude": {
                    "type": "number",
                    "description": "Latitude coordinate",
                },
                "longitude": {
                    "type": "number",
                    "description": "Longitude coordinate",
                },
            },
            "required": ["device_id", "latitude", "longitude"],
        },
    ),
    Tool(
        name="get_clipboard",
        description="Get clipboard/pasteboard content",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="set_clipboard",
        description="Set clipboard/pasteboard content",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "content": {
                    "type": "string",
                    "description": "Content to set",
                },
            },
            "required": ["device_id", "content"],
        },
    ),
    Tool(
        name="get_window_size",
        description="Get the simulator window/screen size",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="double_tap",
        description="Double tap at coordinates",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate",
                },
            },
            "required": ["device_id", "x", "y"],
        },
    ),
    Tool(
        name="long_press",
        description="Long press at coordinates",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate",
                },
                "duration": {
                    "type": "number",
                    "description": "Duration in seconds (default: 1.0)",
                },
            },
            "required": ["device_id", "x", "y"],
        },
    ),
    Tool(
        name="accept_alert",
        description="Accept the current alert dialog",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="dismiss_alert",
        description="Dismiss the current alert dialog",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="get_alert_text",
        description="Get the text of the current alert dialog",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="reset_session",
        description="Reset the WDA session (useful if session expires or has errors)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="set_status_bar",
        description="Override status bar appearance (time, battery, network). Useful for consistent screenshots.",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "time": {
                    "type": "string",
                    "description": "Time string to display (e.g., '9:41')",
                },
                "battery_level": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Battery level 0-100",
                },
                "battery_state": {
                    "type": "string",
                    "enum": ["charging", "charged", "discharging"],
                    "description": "Battery state",
                },
                "data_network": {
                    "type": "string",
                    "enum": ["hide", "wifi", "3g", "4g", "lte", "lte-a", "lte+", "5g", "5g+", "5g-uwb", "5g-uc"],
                    "description": "Data network type to display",
                },
                "wifi_mode": {
                    "type": "string",
                    "enum": ["searching", "failed", "active"],
                    "description": "WiFi mode",
                },
                "wifi_bars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3,
                    "description": "WiFi signal bars 0-3",
                },
                "cellular_mode": {
                    "type": "string",
                    "enum": ["notSupported", "searching", "failed", "active"],
                    "description": "Cellular mode",
                },
                "cellular_bars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 4,
                    "description": "Cellular signal bars 0-4",
                },
                "operator_name": {
                    "type": "string",
                    "description": "Carrier/operator name (empty string to hide)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="clear_status_bar",
        description="Clear all status bar overrides and return to normal",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="discover_dtd_uris",
        description="Discover running Dart Tooling Daemon (DTD) URIs on the local machine. "
                    "These URIs can be used with the Dart MCP server's connect_dart_tooling_daemon tool "
                    "for hot reload, widget inspection, and other Flutter debugging features.",
        inputSchema={
            "type": "object",
            "properties": {
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds for probing each port (default: 2.0)",
                },
            },
            "required": [],
        },
    ),
    # === New High-Impact Tools ===
    Tool(
        name="dismiss_keyboard",
        description="Dismiss the on-screen keyboard if visible",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="set_appearance",
        description="Set device appearance (dark mode or light mode)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "appearance": {
                    "type": "string",
                    "enum": ["dark", "light"],
                    "description": "Appearance mode to set",
                },
            },
            "required": ["device_id", "appearance"],
        },
    ),
    Tool(
        name="get_appearance",
        description="Get current device appearance (dark/light mode)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="simulate_biometrics",
        description="Simulate Touch ID or Face ID authentication (success or failure)",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "match": {
                    "type": "boolean",
                    "description": "True for successful authentication, False for failure (default: true)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="start_recording",
        description="Start screen recording. Use stop_recording to save the video (.mov file).",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "codec": {
                    "type": "string",
                    "enum": ["hevc", "h264"],
                    "description": "Video codec (default: hevc, more efficient)",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="stop_recording",
        description="Stop screen recording and save the video file",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
            },
            "required": ["device_id"],
        },
    ),
    Tool(
        name="pinch",
        description="Perform a pinch gesture (zoom in/out) at coordinates",
        inputSchema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Simulator UDID",
                },
                "x": {
                    "type": "integer",
                    "description": "Center X coordinate for pinch",
                },
                "y": {
                    "type": "integer",
                    "description": "Center Y coordinate for pinch",
                },
                "scale": {
                    "type": "number",
                    "description": "Scale factor: <1.0 to zoom out (pinch in), >1.0 to zoom in (pinch out). E.g., 0.5 zooms out, 2.0 zooms in",
                },
                "velocity": {
                    "type": "number",
                    "description": "Pinch velocity in scale factor per second (default: 1.0)",
                },
            },
            "required": ["device_id", "x", "y", "scale"],
        },
    ),
]


# === Tool Handlers ===

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    log_msg = f">>> LIST_TOOLS: returning {len(TOOLS)} tools"
    logger.info(log_msg)
    print(log_msg, file=sys.stderr, flush=True)
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    """Handle tool calls."""
    args = arguments or {}

    # Log incoming tool call (both logger and stderr print for visibility)
    args_summary = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else "none"
    log_msg = f">>> TOOL: {name}({args_summary})"
    logger.info(log_msg)
    print(log_msg, file=sys.stderr, flush=True)

    # Track tool call in dashboard
    tool_call = dashboard_state.add_tool_call(name, args)

    try:
        result = await handle_tool(name, args)
        # Log result (truncate if too long)
        result_preview = result[:200] + "..." if len(result) > 200 else result
        log_msg = f"<<< RESULT: {result_preview}"
        logger.info(log_msg)
        print(log_msg, file=sys.stderr, flush=True)

        # Complete tool call in dashboard
        dashboard_state.complete_tool_call(tool_call, result=result)

        return [TextContent(type="text", text=result)]
    except (SimulatorError, WDAError) as e:
        log_msg = f"<<< ERROR: {e}"
        logger.error(log_msg)
        print(log_msg, file=sys.stderr, flush=True)

        # Track error in dashboard
        dashboard_state.complete_tool_call(tool_call, error=str(e))

        return [TextContent(type="text", text=f"Error: {e}")]
    except Exception as e:
        log_msg = f"<<< EXCEPTION in tool {name}: {e}"
        logger.exception(log_msg)
        print(log_msg, file=sys.stderr, flush=True)

        # Track error in dashboard
        dashboard_state.complete_tool_call(tool_call, error=str(e))

        return [TextContent(type="text", text=f"Internal error: {e}")]


async def execute_tool_from_dashboard(name: str, args: dict[str, Any]) -> str:
    """Execute a tool from the dashboard quick actions.

    This wraps handle_tool with dashboard tracking.
    """
    # Track tool call in dashboard
    tool_call = dashboard_state.add_tool_call(name, args)

    try:
        result = await handle_tool(name, args)
        dashboard_state.complete_tool_call(tool_call, result=result)
        return result
    except Exception as e:
        dashboard_state.complete_tool_call(tool_call, error=str(e))
        raise


async def handle_tool(name: str, args: dict[str, Any]) -> str:
    """Handle a tool call and return the result as a string."""

    # === Device Management ===

    if name == "list_devices":
        only_booted = args.get("only_booted", False)
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

    elif name == "get_device":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        device = await simulator_manager.get_device(device_id)
        if not device:
            return f"Device not found: {device_id}"

        return json.dumps(device.to_dict(), indent=2)

    elif name == "boot_simulator":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        await simulator_manager.boot(device_id)
        await simulator_manager.open_simulator_app()
        return f"Simulator {device_id} booted successfully"

    elif name == "shutdown_simulator":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        await simulator_manager.shutdown(device_id)
        return f"Simulator {device_id} shut down successfully"

    elif name == "start_bridge":
        device_id = args.get("device_id")
        port = args.get("port", DEFAULT_WDA_PORT)
        host = args.get("host", WDA_HOST)
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id, port, host)
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
                    "wda_host": f"{host}:{port}",
                })

            return f"WebDriverAgent is running at {host}:{port}. Session created (ID: {client.session_id})."
        else:
            return (
                f"WebDriverAgent is not responding at {host}:{port}.\n\n"
                "To start WDA, run:\n"
                "  xcodebuild -project WebDriverAgent.xcodeproj "
                "-scheme WebDriverAgentRunner -destination "
                f"'platform=iOS Simulator,id={device_id}' test\n\n"
                "Or use a tool like appium-webdriveragent.\n\n"
                "If WDA is running on a different host, set WDA_HOST environment variable\n"
                "or pass the host parameter."
            )

    # === Screenshot ===

    elif name == "get_screenshot":
        device_id = args.get("device_id")
        scale = args.get("scale", 0.5)  # Default to half size
        img_format = args.get("format", "jpeg")  # Default to JPEG for smaller files
        quality = args.get("quality", 85)  # Default JPEG quality
        if not device_id:
            raise ValueError("device_id is required")

        # Capture screenshot via simctl (more reliable)
        ensure_screenshot_dir()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        temp_filepath = SCREENSHOT_DIR / f"temp-{timestamp}.png"
        await simulator_manager.screenshot(device_id, temp_filepath)

        # Process image with Pillow
        from PIL import Image
        with Image.open(temp_filepath) as img:
            original_size = img.size
            original_file_size = temp_filepath.stat().st_size

            # Resize if scale < 1
            if scale and scale < 1.0:
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            # Save in requested format
            if img_format == "jpeg":
                # Convert RGBA to RGB for JPEG
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

        # Clean up temp file
        temp_filepath.unlink(missing_ok=True)

        # Report sizes
        reduction = ((original_file_size - new_file_size) / original_file_size) * 100
        return (
            f"Screenshot saved: {filepath}\n"
            f"Original: {original_size[0]}x{original_size[1]} ({original_file_size / 1024:.1f}KB)\n"
            f"Optimized: {new_size[0]}x{new_size[1]} ({new_file_size / 1024:.1f}KB)\n"
            f"Reduction: {reduction:.1f}%"
        )

    # === UI Tree ===

    elif name == "get_ui_tree":
        device_id = args.get("device_id")
        verbose = args.get("verbose", False)
        only_visible = args.get("only_visible", True)
        output_format = args.get("format", "tree")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        source = await client.get_source(format="json")

        parser = UITreeParser()
        root, elements = parser.parse(source, only_visible=only_visible)

        if not root:
            return "No UI elements found"

        # Store elements for later use (tap by index)
        # This is a simplification - in production, use proper state management
        global _last_ui_elements
        _last_ui_elements = {device_id: elements}

        if output_format == "json":
            return json.dumps([e.to_dict(include_children=False) for e in elements], indent=2)
        elif output_format == "flat":
            return parser.format_flat_list(elements, verbose=verbose)
        else:
            return parser.format_tree(root, elements, verbose=verbose)

    # === Tap ===

    elif name == "tap":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)

        index = args.get("index")
        x = args.get("x")
        y = args.get("y")
        predicate = args.get("predicate")

        if index is not None:
            # Tap by index from UI tree
            elements = _last_ui_elements.get(device_id, [])
            if not elements:
                # Need to get UI tree first
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
            # Tap by predicate
            source = await client.get_source(format="json")
            parser = UITreeParser()
            _, elements = parser.parse(source, only_visible=True)

            elem = find_element_by_predicate(elements, predicate)
            if not elem:
                return f"No element found matching predicate: {predicate}"

            await client.tap(elem.center_x, elem.center_y)
            return f"Tapped element [{elem.index}] {elem.element_type} at ({elem.center_x}, {elem.center_y})"

        elif x is not None and y is not None:
            # Tap by coordinates
            await client.tap(x, y)
            return f"Tapped at ({x}, {y})"

        else:
            return "Please provide index, predicate, or x/y coordinates"

    # === Type Text ===

    elif name == "type_text":
        device_id = args.get("device_id")
        text = args.get("text")
        predicate = args.get("predicate")
        if not device_id:
            raise ValueError("device_id is required")
        if not text:
            raise ValueError("text is required")

        client = get_wda_client(device_id)

        # If predicate provided, tap element first
        if predicate:
            source = await client.get_source(format="json")
            parser = UITreeParser()
            _, elements = parser.parse(source, only_visible=True)

            elem = find_element_by_predicate(elements, predicate)
            if not elem:
                return f"No element found matching predicate: {predicate}"

            await client.tap(elem.center_x, elem.center_y)
            await asyncio.sleep(0.3)  # Wait for keyboard

        await client.send_keys(text)
        return f"Typed: {text}"

    # === Swipe ===

    elif name == "swipe":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)

        direction = args.get("direction")
        from_x = args.get("from_x")
        from_y = args.get("from_y")
        to_x = args.get("to_x")
        to_y = args.get("to_y")
        duration_ms = args.get("duration_ms", 300)

        if direction:
            # Get screen size for direction-based swipe
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

    elif name == "go_home":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        await client.go_home()
        return "Navigated to home screen"

    elif name == "launch_app":
        device_id = args.get("device_id")
        bundle_id = args.get("bundle_id")
        if not device_id:
            raise ValueError("device_id is required")
        if not bundle_id:
            raise ValueError("bundle_id is required")

        # Try WDA first, fall back to simctl
        try:
            client = get_wda_client(device_id)
            await client.launch_app(bundle_id)
        except WDAError:
            await simulator_manager.launch_app(device_id, bundle_id)

        return f"Launched app: {bundle_id}"

    elif name == "terminate_app":
        device_id = args.get("device_id")
        bundle_id = args.get("bundle_id")
        if not device_id:
            raise ValueError("device_id is required")
        if not bundle_id:
            raise ValueError("bundle_id is required")

        try:
            client = get_wda_client(device_id)
            await client.terminate_app(bundle_id)
        except WDAError:
            await simulator_manager.terminate_app(device_id, bundle_id)

        return f"Terminated app: {bundle_id}"

    elif name == "list_apps":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        apps = await simulator_manager.list_apps(device_id)
        if not apps:
            return "No apps found"

        result = []
        for app in apps:
            version_part = f" v{app.version}" if app.version else ""
            result.append(f"- {app.name}{version_part}\n  Bundle ID: {app.bundle_id}")

        return "\n".join(result)

    elif name == "open_url":
        device_id = args.get("device_id")
        url = args.get("url")
        if not device_id:
            raise ValueError("device_id is required")
        if not url:
            raise ValueError("url is required")

        await simulator_manager.open_url(device_id, url)
        return f"Opened URL: {url}"

    elif name == "press_button":
        device_id = args.get("device_id")
        button = args.get("button")
        if not device_id:
            raise ValueError("device_id is required")
        if not button:
            raise ValueError("button is required")

        client = get_wda_client(device_id)
        await client.press_button(button)
        return f"Pressed button: {button}"

    elif name == "set_location":
        device_id = args.get("device_id")
        latitude = args.get("latitude")
        longitude = args.get("longitude")
        if not device_id:
            raise ValueError("device_id is required")
        if latitude is None or longitude is None:
            raise ValueError("latitude and longitude are required")

        await simulator_manager.set_location(device_id, latitude, longitude)
        return f"Location set to ({latitude}, {longitude})"

    # === Status Bar ===

    elif name == "set_status_bar":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        # Extract all optional parameters
        time = args.get("time")
        battery_level = args.get("battery_level")
        battery_state = args.get("battery_state")
        data_network = args.get("data_network")
        wifi_mode = args.get("wifi_mode")
        wifi_bars = args.get("wifi_bars")
        cellular_mode = args.get("cellular_mode")
        cellular_bars = args.get("cellular_bars")
        operator_name = args.get("operator_name")

        # Check that at least one override is specified
        overrides = [time, battery_level, battery_state, data_network,
                     wifi_mode, wifi_bars, cellular_mode, cellular_bars, operator_name]
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

        # Build response message
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

    elif name == "clear_status_bar":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        await simulator_manager.status_bar_clear(device_id)
        return "Status bar overrides cleared"

    # === DTD Discovery ===

    elif name == "discover_dtd_uris":
        timeout = args.get("timeout", 2.0)
        uris = await discover_dart_vm_services(timeout=timeout)

        if not uris:
            return (
                "No running Dart VM services found.\n\n"
                "To get a DTD URI:\n"
                "1. Run a Flutter app in debug mode: flutter run\n"
                "2. The DTD URI is printed when the app starts (looks like ws://127.0.0.1:XXXXX/...)\n"
                "3. In VS Code, use 'Dart: Copy DTD Uri to Clipboard' command\n"
                "4. In Android Studio, check the Debug console for the VM service URI"
            )

        result = ["Found running Dart VM services:\n"]
        for uri_info in uris:
            result.append(f"- {uri_info['dtd_uri']}")
            if uri_info.get("process"):
                result.append(f"  Process: {uri_info['process']}")
            if uri_info.get("vm_name"):
                result.append(f"  VM: {uri_info['vm_name']}")
            result.append("")

        result.append("\nUse one of these URIs with the Dart MCP server's connect_dart_tooling_daemon tool.")
        return "\n".join(result)

    # === Clipboard ===

    elif name == "get_clipboard":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        content = await client.get_pasteboard()
        return f"Clipboard content: {content}"

    elif name == "set_clipboard":
        device_id = args.get("device_id")
        content = args.get("content")
        if not device_id:
            raise ValueError("device_id is required")
        if content is None:
            raise ValueError("content is required")

        client = get_wda_client(device_id)
        await client.set_pasteboard(content)
        return f"Clipboard set to: {content}"

    # === Window/Screen ===

    elif name == "get_window_size":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        size = await client.get_window_size()
        return f"Window size: {size['width']}x{size['height']}"

    # === Touch Gestures ===

    elif name == "double_tap":
        device_id = args.get("device_id")
        x = args.get("x")
        y = args.get("y")
        if not device_id:
            raise ValueError("device_id is required")
        if x is None or y is None:
            raise ValueError("x and y are required")

        client = get_wda_client(device_id)
        await client.double_tap(x, y)
        return f"Double tapped at ({x}, {y})"

    elif name == "long_press":
        device_id = args.get("device_id")
        x = args.get("x")
        y = args.get("y")
        duration = args.get("duration", 1.0)
        if not device_id:
            raise ValueError("device_id is required")
        if x is None or y is None:
            raise ValueError("x and y are required")

        client = get_wda_client(device_id)
        await client.long_press(x, y, duration)
        return f"Long pressed at ({x}, {y}) for {duration}s"

    # === Alerts ===

    elif name == "accept_alert":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        await client.accept_alert()
        return "Alert accepted"

    elif name == "dismiss_alert":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        await client.dismiss_alert()
        return "Alert dismissed"

    elif name == "get_alert_text":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        text = await client.get_alert_text()
        if text:
            return f"Alert text: {text}"
        else:
            return "No alert present"

    elif name == "reset_session":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        await reset_wda_session(device_id)
        # Create a fresh session
        client = get_wda_client(device_id)
        if await client.health_check():
            await client.create_session()
            return f"Session reset. New session created (ID: {client.session_id})."
        else:
            return "Session reset, but WDA is not responding. Please restart WDA."

    # === Keyboard ===

    elif name == "dismiss_keyboard":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        await client.dismiss_keyboard()
        return "Keyboard dismissed"

    # === Appearance (Dark/Light Mode) ===

    elif name == "set_appearance":
        device_id = args.get("device_id")
        appearance = args.get("appearance")
        if not device_id:
            raise ValueError("device_id is required")
        if not appearance:
            raise ValueError("appearance is required ('dark' or 'light')")

        client = get_wda_client(device_id)
        await client.set_appearance(appearance)
        return f"Appearance set to: {appearance}"

    elif name == "get_appearance":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        appearance = await client.get_appearance()
        return f"Current appearance: {appearance}"

    # === Biometrics (Touch ID / Face ID) ===

    elif name == "simulate_biometrics":
        device_id = args.get("device_id")
        match = args.get("match", True)
        if not device_id:
            raise ValueError("device_id is required")

        client = get_wda_client(device_id)
        await client.simulate_biometrics(match=match)
        result = "successful" if match else "failed"
        return f"Simulated biometric authentication: {result}"

    # === Screen Recording ===

    elif name == "start_recording":
        device_id = args.get("device_id")
        codec = args.get("codec", "hevc")
        if not device_id:
            raise ValueError("device_id is required")

        # Check if already recording
        if simulator_manager.is_recording(device_id):
            return "Recording already in progress. Use stop_recording first."

        # Generate output path
        ensure_screenshot_dir()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        video_dir = SCREENSHOT_DIR.parent / "recordings"
        video_dir.mkdir(parents=True, exist_ok=True)
        filepath = video_dir / f"recording-{timestamp}.mov"

        # Store filepath for later retrieval
        _recording_paths[device_id] = filepath

        await simulator_manager.start_recording(device_id, filepath, codec=codec)
        return f"Screen recording started (codec={codec})\nWill save to: {filepath}"

    elif name == "stop_recording":
        device_id = args.get("device_id")
        if not device_id:
            raise ValueError("device_id is required")

        if not simulator_manager.is_recording(device_id):
            return "No recording in progress"

        filepath = _recording_paths.pop(device_id, None)
        stopped = await simulator_manager.stop_recording(device_id)

        if stopped and filepath and filepath.exists():
            file_size = filepath.stat().st_size / 1024  # KB
            return f"Screen recording saved: {filepath}\nSize: {file_size:.1f}KB"
        elif stopped:
            return "Recording stopped but file may still be processing"
        else:
            return "No recording was in progress"

    # === Pinch Gesture ===

    elif name == "pinch":
        device_id = args.get("device_id")
        x = args.get("x")
        y = args.get("y")
        scale = args.get("scale")
        velocity = args.get("velocity", 1.0)
        if not device_id:
            raise ValueError("device_id is required")
        if x is None or y is None:
            raise ValueError("x and y coordinates are required")
        if scale is None:
            raise ValueError("scale is required")

        client = get_wda_client(device_id)
        await client.pinch(x, y, scale, velocity)

        action = "zoom in" if scale > 1.0 else "zoom out"
        return f"Pinch gesture at ({x}, {y}) with scale {scale} ({action})"

    else:
        return f"Unknown tool: {name}"


# Global state for UI elements (simplified - use proper state management in production)
_last_ui_elements: dict[str, list] = {}

# Global state for recording file paths
_recording_paths: dict[str, Path] = {}


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


RESOURCES = [
    Resource(
        uri="ios-sim://api-reference",
        name="iOS Simulator MCP API Reference",
        description="Complete API documentation",
        mimeType="text/markdown",
    ),
    Resource(
        uri="ios-sim://automation-guide",
        name="iOS Simulator Automation Guide",
        description="Guide for automating iOS simulators",
        mimeType="text/markdown",
    ),
]


@server.list_resources()
async def list_resources() -> list[Resource]:
    """List available resources."""
    return RESOURCES


@server.read_resource()
async def read_resource(uri: str) -> str:
    """Read a resource."""
    if uri == "ios-sim://api-reference":
        return API_REFERENCE
    elif uri == "ios-sim://automation-guide":
        return AUTOMATION_GUIDE
    else:
        raise ValueError(f"Unknown resource: {uri}")


# === Main Entry Point ===

def main():
    """Run the MCP server."""
    logger.info("=" * 60)
    logger.info("iOS Simulator MCP Server starting...")
    logger.info(f"WDA_HOST: {WDA_HOST}")
    logger.info(f"WDA_PORT: {DEFAULT_WDA_PORT}")
    logger.info(f"Screenshot dir: {SCREENSHOT_DIR}")
    logger.info(f"Dashboard port: {DASHBOARD_PORT}")
    logger.info(f"Log level: {LOG_LEVEL}")
    logger.info("=" * 60)

    async def run():
        # Wire up tool executor for dashboard quick actions
        dashboard_state.tool_executor = execute_tool_from_dashboard

        # Start the dashboard server
        dashboard_runner = await start_dashboard()

        try:
            logger.info("Server ready, waiting for MCP client connection...")
            async with stdio_server() as (read_stream, write_stream):
                logger.info("MCP client connected")
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )
        finally:
            # Stop the dashboard when MCP server stops
            await stop_dashboard(dashboard_runner)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        raise


if __name__ == "__main__":
    main()
