"""Web dashboard for iOS Simulator MCP server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import webbrowser
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType

logger = logging.getLogger(__name__)

# Dashboard configuration (can be overridden via environment variables)
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8200"))
DASHBOARD_AUTO_OPEN = os.environ.get("DASHBOARD_AUTO_OPEN", "true").lower() in ("true", "1", "yes")


@dataclass
class ToolCall:
    """Represents a single tool call."""

    id: int
    timestamp: float
    tool_name: str
    arguments: dict[str, Any]
    status: str = "pending"  # pending, success, error
    result: str | None = None
    error: str | None = None
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "time_str": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S"),
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "status": self.status,
            "result": self.result[:500] if self.result and len(self.result) > 500 else self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class DashboardState:
    """Holds the dashboard state."""

    def __init__(self, max_calls: int = 100):
        self.max_calls = max_calls
        self.tool_calls: list[ToolCall] = []
        self.call_counter = 0
        self.websockets: set[web.WebSocketResponse] = set()
        self.server_start_time = time.time()
        self.device_info: dict[str, Any] = {}
        self.wda_status: dict[str, Any] = {}
        self.last_screenshot: str | None = None
        self.recording_active: bool = False
        # Callback for executing tools from the dashboard
        self.tool_executor: Any = None  # Will be set by server.py

    def add_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> ToolCall:
        """Add a new tool call and return it."""
        self.call_counter += 1
        call = ToolCall(
            id=self.call_counter,
            timestamp=time.time(),
            tool_name=tool_name,
            arguments=arguments,
        )
        self.tool_calls.append(call)

        # Trim old calls
        if len(self.tool_calls) > self.max_calls:
            self.tool_calls = self.tool_calls[-self.max_calls:]

        # Broadcast to websockets
        asyncio.create_task(self._broadcast({
            "type": "tool_call",
            "data": call.to_dict(),
        }))

        return call

    def complete_tool_call(self, call: ToolCall, result: str | None = None, error: str | None = None):
        """Mark a tool call as complete."""
        call.duration_ms = (time.time() - call.timestamp) * 1000
        if error:
            call.status = "error"
            call.error = error
        else:
            call.status = "success"
            call.result = result

        # Track screenshots
        if call.tool_name == "get_screenshot" and result:
            for line in result.split("\n"):
                if line.startswith("Screenshot saved:"):
                    self.last_screenshot = line.split(": ", 1)[1].strip()
                    break

        # Track recording
        if call.tool_name == "start_recording" and call.status == "success":
            self.recording_active = True
        elif call.tool_name == "stop_recording" and call.status == "success":
            self.recording_active = False

        # Broadcast update
        asyncio.create_task(self._broadcast({
            "type": "tool_complete",
            "data": call.to_dict(),
        }))

    def update_device_info(self, info: dict[str, Any]):
        """Update device info."""
        self.device_info = info
        asyncio.create_task(self._broadcast({
            "type": "device_info",
            "data": info,
        }))

    def update_wda_status(self, status: dict[str, Any]):
        """Update WDA status."""
        self.wda_status = status
        asyncio.create_task(self._broadcast({
            "type": "wda_status",
            "data": status,
        }))

    async def _broadcast(self, message: dict[str, Any]):
        """Broadcast message to all connected websockets."""
        if not self.websockets:
            return

        msg_str = json.dumps(message)
        dead_ws = set()

        for ws in self.websockets:
            try:
                await ws.send_str(msg_str)
            except Exception:
                dead_ws.add(ws)

        self.websockets -= dead_ws

    def get_state(self) -> dict[str, Any]:
        """Get current state for initial load."""
        return {
            "uptime": time.time() - self.server_start_time,
            "tool_calls": [c.to_dict() for c in self.tool_calls[-50:]],
            "device_info": self.device_info,
            "wda_status": self.wda_status,
            "last_screenshot": self.last_screenshot,
            "recording_active": self.recording_active,
            "total_calls": self.call_counter,
        }


# Global dashboard state
dashboard_state = DashboardState()


# Template directory
TEMPLATE_DIR = Path(__file__).parent / "templates"

def _load_template() -> str:
    """Load the dashboard HTML template."""
    template_path = TEMPLATE_DIR / "dashboard.html"
    return template_path.read_text()

# Cache the template (loaded once at import time)
DASHBOARD_HTML = _load_template()



async def handle_index(request: web.Request) -> web.Response:
    """Serve the dashboard HTML."""
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def handle_api_state(request: web.Request) -> web.Response:
    """Return current dashboard state as JSON."""
    return web.json_response(dashboard_state.get_state())


async def handle_screenshot(request: web.Request) -> web.Response:
    """Serve the last screenshot."""
    if not dashboard_state.last_screenshot:
        return web.Response(status=404, text="No screenshot available")

    path = Path(dashboard_state.last_screenshot)
    if not path.exists():
        return web.Response(status=404, text="Screenshot file not found")

    content_type = "image/jpeg" if path.suffix.lower() in [".jpg", ".jpeg"] else "image/png"
    return web.Response(body=path.read_bytes(), content_type=content_type)


async def handle_action(request: web.Request) -> web.Response:
    """Execute a quick action (tool call) from the dashboard."""
    try:
        data = await request.json()
        tool = data.get("tool")
        args = data.get("args", {})

        if not tool:
            return web.json_response({"success": False, "error": "Missing tool name"}, status=400)

        if not dashboard_state.tool_executor:
            return web.json_response(
                {"success": False, "error": "Tool executor not configured"},
                status=503
            )

        # Execute the tool via the callback
        try:
            result = await dashboard_state.tool_executor(tool, args)
            return web.json_response({"success": True, "result": result})
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    except json.JSONDecodeError:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """Handle WebSocket connections."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    dashboard_state.websockets.add(ws)
    logger.info(f"WebSocket client connected ({len(dashboard_state.websockets)} total)")

    # Send initial state
    await ws.send_json({
        "type": "init",
        "data": dashboard_state.get_state(),
    })

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                # Handle any client messages if needed
                pass
            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        dashboard_state.websockets.discard(ws)
        logger.info(f"WebSocket client disconnected ({len(dashboard_state.websockets)} remaining)")

    return ws


def create_dashboard_app() -> web.Application:
    """Create the dashboard web application."""
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/state", handle_api_state)
    app.router.add_post("/api/action", handle_action)
    app.router.add_get("/screenshot", handle_screenshot)
    app.router.add_get("/ws", handle_websocket)
    return app


async def start_dashboard(port: int = DASHBOARD_PORT, auto_open: bool = DASHBOARD_AUTO_OPEN) -> web.AppRunner:
    """Start the dashboard server.

    Args:
        port: Port to run dashboard on
        auto_open: Whether to automatically open browser (default: True, set DASHBOARD_AUTO_OPEN=false to disable)
    """
    app = create_dashboard_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    url = f"http://localhost:{port}"
    logger.info(f"Dashboard started at {url}")
    print(f"\n{'='*50}", flush=True)
    print(f"  Dashboard: {url}", flush=True)
    print(f"{'='*50}\n", flush=True)

    # Auto-open browser
    if auto_open:
        try:
            webbrowser.open(url)
            logger.info("Opened dashboard in browser")
        except Exception as e:
            logger.warning(f"Could not open browser: {e}")

    return runner


async def stop_dashboard(runner: web.AppRunner) -> None:
    """Stop the dashboard server."""
    await runner.cleanup()
    logger.info("Dashboard stopped")
