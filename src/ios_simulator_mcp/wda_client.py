"""WebDriverAgent HTTP client for iOS simulator automation."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_WDA_PORT = 8100
DEFAULT_TIMEOUT = 60.0


@dataclass
class WDAElement:
    """Represents a UI element from WebDriverAgent."""

    element_id: str
    label: str | None = None
    name: str | None = None
    element_type: str | None = None
    value: str | None = None
    enabled: bool = True
    visible: bool = True
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2


class WDAError(Exception):
    """WebDriverAgent error."""

    def __init__(self, message: str, status_code: int | None = None, error: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error = error


class WDAClient:
    """HTTP client for WebDriverAgent.

    WebDriverAgent (WDA) is a WebDriver server implementation for iOS
    that runs on the device/simulator.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_WDA_PORT):
        self.base_url = f"http://{host}:{port}"
        self.session_id: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=DEFAULT_TIMEOUT,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to WDA."""
        client = await self._get_client()
        kwargs: dict[str, Any] = {}
        if json is not None:
            kwargs["json"] = json
        if timeout is not None:
            kwargs["timeout"] = timeout

        logger.debug(f"WDA request: {method} {path} json={json}")

        try:
            response = await client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise WDAError(
                f"Cannot connect to WebDriverAgent at {self.base_url}. "
                "Make sure WDA is running on the simulator."
            ) from e
        except httpx.TimeoutException as e:
            raise WDAError(f"Request timed out: {path}") from e

        try:
            data = response.json()
        except Exception:
            if response.status_code >= 400:
                raise WDAError(
                    f"WDA request failed: {response.status_code} - {response.text}",
                    status_code=response.status_code,
                )
            return {"value": response.text}

        logger.debug(f"WDA response: status={response.status_code} data={data}")

        # WDA returns errors in the response body with various formats
        # Check for WebDriver protocol error format
        if isinstance(data, dict):
            # Standard WebDriver error format
            if "error" in data and data["error"]:
                error_msg = data.get("error", "Unknown error")
                error_details = data.get("message", "")
                if error_details:
                    error_msg = f"{error_msg}: {error_details}"
                raise WDAError(
                    f"WDA error: {error_msg}",
                    status_code=response.status_code,
                    error=data.get("error"),
                )

            # WDA-specific error format (status != 0)
            value = data.get("value", {})
            if isinstance(value, dict) and value.get("error"):
                error_msg = value.get("error", "Unknown error")
                error_details = value.get("message", "")
                if error_details:
                    error_msg = f"{error_msg}: {error_details}"
                raise WDAError(
                    f"WDA error: {error_msg}",
                    status_code=response.status_code,
                    error=value.get("error"),
                )

            # Check status field (older WDA format)
            status = data.get("status")
            if status is not None and status != 0:
                error_msg = f"WDA returned status {status}"
                if isinstance(value, dict) and "message" in value:
                    error_msg += f": {value['message']}"
                elif isinstance(value, str):
                    error_msg += f": {value}"
                raise WDAError(error_msg, status_code=response.status_code)

        if response.status_code >= 400:
            raise WDAError(
                f"WDA request failed: {response.status_code} - {data}",
                status_code=response.status_code,
            )

        return data

    async def get_status(self) -> dict[str, Any]:
        """Get WDA server status."""
        return await self._request("GET", "/status")

    async def health_check(self) -> bool:
        """Check if WDA is responsive."""
        try:
            await self.get_status()
            return True
        except Exception:
            return False

    async def create_session(self, capabilities: dict[str, Any] | None = None) -> str:
        """Create a new WDA session.

        Args:
            capabilities: Optional desired capabilities

        Returns:
            Session ID
        """
        caps = capabilities or {}
        data = await self._request(
            "POST",
            "/session",
            json={"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}},
        )
        self.session_id = data.get("sessionId") or data.get("value", {}).get("sessionId")
        if not self.session_id:
            raise WDAError("Failed to create session: no session ID returned")
        return self.session_id

    async def delete_session(self) -> None:
        """Delete the current session."""
        if self.session_id:
            try:
                await self._request("DELETE", f"/session/{self.session_id}")
            except Exception as e:
                logger.warning(f"Error deleting session: {e}")
            self.session_id = None

    async def _ensure_session(self) -> str:
        """Ensure we have an active session."""
        if not self.session_id:
            await self.create_session()
        return self.session_id  # type: ignore

    # === Screenshot ===

    async def get_screenshot(self) -> bytes:
        """Capture screenshot as PNG bytes."""
        session_id = await self._ensure_session()
        data = await self._request("GET", f"/session/{session_id}/screenshot")
        b64_data = data.get("value", "")
        if not b64_data:
            raise WDAError("No screenshot data returned")
        return base64.b64decode(b64_data)

    # === UI Hierarchy ===

    async def get_source(self, format: str = "json") -> dict[str, Any] | str:
        """Get the UI hierarchy.

        Args:
            format: 'json' or 'xml'

        Returns:
            UI hierarchy as dict (json) or string (xml)
        """
        session_id = await self._ensure_session()
        if format == "json":
            data = await self._request("GET", f"/session/{session_id}/source?format=json")
            return data.get("value", {})
        else:
            data = await self._request("GET", f"/session/{session_id}/source")
            return data.get("value", "")

    # === Window/Screen Info ===

    async def get_window_size(self) -> dict[str, int]:
        """Get the window size."""
        session_id = await self._ensure_session()
        data = await self._request("GET", f"/session/{session_id}/window/size")
        return data.get("value", {"width": 0, "height": 0})

    # === Element Finding ===

    async def find_element(
        self,
        using: str,
        value: str,
    ) -> WDAElement:
        """Find a single element.

        Args:
            using: Locator strategy ('class name', 'xpath', 'predicate string',
                   'class chain', 'link text', 'name', 'accessibility id')
            value: Locator value

        Returns:
            WDAElement
        """
        session_id = await self._ensure_session()
        data = await self._request(
            "POST",
            f"/session/{session_id}/element",
            json={"using": using, "value": value},
        )
        element_data = data.get("value", {})
        element_id = element_data.get("ELEMENT") or element_data.get(
            "element-6066-11e4-a52e-4f735466cecf"
        )
        if not element_id:
            raise WDAError(f"Element not found: {using}={value}")
        return WDAElement(element_id=element_id)

    async def find_elements(
        self,
        using: str,
        value: str,
    ) -> list[WDAElement]:
        """Find multiple elements.

        Args:
            using: Locator strategy
            value: Locator value

        Returns:
            List of WDAElements
        """
        session_id = await self._ensure_session()
        data = await self._request(
            "POST",
            f"/session/{session_id}/elements",
            json={"using": using, "value": value},
        )
        elements = []
        for elem_data in data.get("value", []):
            elem_id = elem_data.get("ELEMENT") or elem_data.get(
                "element-6066-11e4-a52e-4f735466cecf"
            )
            if elem_id:
                elements.append(WDAElement(element_id=elem_id))
        return elements

    async def get_element_attribute(self, element_id: str, attribute: str) -> str | None:
        """Get element attribute."""
        session_id = await self._ensure_session()
        data = await self._request(
            "GET",
            f"/session/{session_id}/element/{element_id}/attribute/{attribute}",
        )
        return data.get("value")

    async def get_element_rect(self, element_id: str) -> dict[str, int]:
        """Get element position and size."""
        session_id = await self._ensure_session()
        data = await self._request(
            "GET",
            f"/session/{session_id}/element/{element_id}/rect",
        )
        return data.get("value", {"x": 0, "y": 0, "width": 0, "height": 0})

    # === Element Actions ===

    async def click_element(self, element_id: str) -> None:
        """Click/tap on an element."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/element/{element_id}/click",
            json={},
        )

    async def send_keys_to_element(self, element_id: str, text: str) -> None:
        """Send keys to an element."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/element/{element_id}/value",
            json={"value": list(text)},
        )

    async def clear_element(self, element_id: str) -> None:
        """Clear an element's text."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/element/{element_id}/clear",
            json={},
        )

    # === Touch Actions (WDA Extensions) ===

    async def release_actions(self) -> None:
        """Release all active pointer/touch inputs."""
        if not self.session_id:
            return
        try:
            await self._request(
                "DELETE",
                f"/session/{self.session_id}/actions",
            )
        except WDAError:
            pass  # Ignore errors, this is cleanup

    async def tap(self, x: int, y: int) -> None:
        """Tap at coordinates using W3C actions API."""
        session_id = await self._ensure_session()

        # Use W3C actions API (more reliable)
        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 50},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }

        try:
            await self._request(
                "POST",
                f"/session/{session_id}/actions",
                json=actions,
            )
            # Clean up actions
            await self.release_actions()
        except WDAError as e:
            # Fallback to WDA-specific tap endpoint
            logger.debug(f"W3C actions failed, trying WDA tap: {e}")
            await self._request(
                "POST",
                f"/session/{session_id}/wda/tap/0",
                json={"x": x, "y": y},
            )

    async def tap_wda(self, x: int, y: int) -> None:
        """Tap at coordinates using WDA-specific endpoint (legacy)."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/tap/0",
            json={"x": x, "y": y},
        )

    async def double_tap(self, x: int, y: int) -> None:
        """Double tap at coordinates using W3C actions API."""
        session_id = await self._ensure_session()

        # Two quick taps
        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 50},
                        {"type": "pointerUp", "button": 0},
                        {"type": "pause", "duration": 100},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 50},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }

        try:
            await self._request(
                "POST",
                f"/session/{session_id}/actions",
                json=actions,
            )
        except WDAError as e:
            # Fallback to WDA-specific endpoint
            logger.debug(f"W3C actions failed, trying WDA doubleTap: {e}")
            await self._request(
                "POST",
                f"/session/{session_id}/wda/doubleTap",
                json={"x": x, "y": y},
            )

    async def long_press(self, x: int, y: int, duration: float = 1.0) -> None:
        """Long press at coordinates using W3C actions API."""
        session_id = await self._ensure_session()
        duration_ms = int(duration * 1000)

        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": duration_ms},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }

        try:
            await self._request(
                "POST",
                f"/session/{session_id}/actions",
                json=actions,
            )
        except WDAError as e:
            # Fallback to WDA-specific endpoint
            logger.debug(f"W3C actions failed, trying WDA touchAndHold: {e}")
            await self._request(
                "POST",
                f"/session/{session_id}/wda/touchAndHold",
                json={"x": x, "y": y, "duration": duration},
            )

    async def swipe(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        duration: float = 0.3,
    ) -> None:
        """Perform a swipe gesture using W3C actions API."""
        session_id = await self._ensure_session()

        # Duration in milliseconds
        duration_ms = int(duration * 1000)

        # Use W3C actions API
        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": from_x, "y": from_y},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerMove", "duration": duration_ms, "x": to_x, "y": to_y},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }

        try:
            await self._request(
                "POST",
                f"/session/{session_id}/actions",
                json=actions,
            )
        except WDAError as e:
            # Fallback to WDA-specific endpoint
            logger.debug(f"W3C actions failed, trying WDA drag: {e}")
            await self._request(
                "POST",
                f"/session/{session_id}/wda/dragfromtoforduration",
                json={
                    "fromX": from_x,
                    "fromY": from_y,
                    "toX": to_x,
                    "toY": to_y,
                    "duration": duration,
                },
            )

    # === Keyboard ===

    async def send_keys(self, text: str) -> None:
        """Send keys to the focused element."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/keys",
            json={"value": list(text)},
        )

    async def press_button(self, button: str) -> None:
        """Press a hardware button.

        Args:
            button: 'home', 'volumeUp', 'volumeDown'
        """
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/pressButton",
            json={"name": button},
        )

    # === Navigation ===

    async def go_home(self) -> None:
        """Go to home screen."""
        await self.press_button("home")

    # === App Management ===

    async def launch_app(self, bundle_id: str) -> None:
        """Launch an app."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/apps/launch",
            json={"bundleId": bundle_id},
        )

    async def terminate_app(self, bundle_id: str) -> None:
        """Terminate an app."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/apps/terminate",
            json={"bundleId": bundle_id},
        )

    async def activate_app(self, bundle_id: str) -> None:
        """Activate (bring to foreground) an app."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/apps/activate",
            json={"bundleId": bundle_id},
        )

    async def get_app_state(self, bundle_id: str) -> int:
        """Get app state.

        Returns:
            0: not installed
            1: not running
            2: running in background (suspended)
            3: running in background
            4: running in foreground
        """
        session_id = await self._ensure_session()
        data = await self._request(
            "POST",
            f"/session/{session_id}/wda/apps/state",
            json={"bundleId": bundle_id},
        )
        return data.get("value", 0)

    async def list_apps(self) -> list[dict[str, Any]]:
        """List installed apps.

        Returns a list of app info dicts with bundleId, name, etc.
        """
        # WDA doesn't have a direct list apps endpoint, but we can use simctl
        # For WDA, we just return empty list and let simulator handle it
        return []

    # === Alert Handling ===

    async def get_alert_text(self) -> str | None:
        """Get alert text if present."""
        session_id = await self._ensure_session()
        try:
            data = await self._request("GET", f"/session/{session_id}/alert/text")
            return data.get("value")
        except WDAError:
            return None

    async def accept_alert(self) -> None:
        """Accept/dismiss the current alert."""
        session_id = await self._ensure_session()
        await self._request("POST", f"/session/{session_id}/alert/accept", json={})

    async def dismiss_alert(self) -> None:
        """Dismiss the current alert."""
        session_id = await self._ensure_session()
        await self._request("POST", f"/session/{session_id}/alert/dismiss", json={})

    # === Orientation ===

    async def get_orientation(self) -> str:
        """Get device orientation."""
        session_id = await self._ensure_session()
        data = await self._request("GET", f"/session/{session_id}/orientation")
        return data.get("value", "PORTRAIT")

    async def set_orientation(self, orientation: str) -> None:
        """Set device orientation.

        Args:
            orientation: 'PORTRAIT' or 'LANDSCAPE'
        """
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/orientation",
            json={"orientation": orientation},
        )

    # === Pasteboard ===

    async def set_pasteboard(self, content: str, content_type: str = "plaintext") -> None:
        """Set pasteboard content."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/setPasteboard",
            json={
                "content": base64.b64encode(content.encode()).decode(),
                "contentType": content_type,
            },
        )

    async def get_pasteboard(self, content_type: str = "plaintext") -> str:
        """Get pasteboard content."""
        session_id = await self._ensure_session()
        data = await self._request(
            "POST",
            f"/session/{session_id}/wda/getPasteboard",
            json={"contentType": content_type},
        )
        b64_content = data.get("value", "")
        if b64_content:
            return base64.b64decode(b64_content).decode()
        return ""

    # === Keyboard ===

    async def dismiss_keyboard(self) -> None:
        """Dismiss the on-screen keyboard."""
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/keyboard/dismiss",
            json={},
        )

    # === Device Appearance ===

    async def set_appearance(self, appearance: str) -> None:
        """Set device appearance (dark/light mode).

        Args:
            appearance: 'dark' or 'light'
        """
        # This endpoint is session-less (.withoutSession in WDA)
        await self._request(
            "POST",
            "/wda/device/appearance",
            json={"name": appearance},
        )

    async def get_appearance(self) -> str:
        """Get current device appearance."""
        # Get appearance from device info (session-less endpoint)
        data = await self._request(
            "GET",
            "/wda/device/info",
        )
        value = data.get("value", {})
        return value.get("userInterfaceStyle", "unknown")

    # === Biometrics (Touch ID / Face ID) ===

    async def simulate_biometrics(self, match: bool = True) -> None:
        """Simulate Touch ID / Face ID authentication.

        Args:
            match: True to simulate successful auth, False to simulate failure
        """
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/touch_id",
            json={"match": match},
        )

    # === Screen Recording (Note: Use SimulatorManager for simctl-based recording) ===

    async def start_recording_wda(self) -> dict[str, Any]:
        """Start screen recording via WDA (returns metadata only, not video data).

        Note: WDA recording doesn't return video data directly. For actual video
        files, use SimulatorManager.start_recording() which uses simctl.

        Returns:
            Recording metadata (uuid, startedAt, fps, codec)
        """
        session_id = await self._ensure_session()
        data = await self._request(
            "POST",
            f"/session/{session_id}/wda/video/start",
            json={},
        )
        return data.get("value", {})

    async def stop_recording_wda(self) -> dict[str, Any]:
        """Stop WDA screen recording (returns metadata only).

        Returns:
            Recording metadata
        """
        session_id = await self._ensure_session()
        data = await self._request(
            "POST",
            f"/session/{session_id}/wda/video/stop",
            json={},
        )
        return data.get("value", {})

    async def get_recording_status(self) -> bool:
        """Check if screen recording is active.

        Returns:
            True if recording is active, False otherwise
        """
        session_id = await self._ensure_session()
        try:
            data = await self._request(
                "GET",
                f"/session/{session_id}/wda/video",
            )
            return bool(data.get("value"))
        except WDAError:
            return False

    # === Pinch Gesture ===

    async def pinch(
        self,
        x: int,
        y: int,
        scale: float,
        velocity: float = 1.0,
    ) -> None:
        """Perform a pinch gesture at coordinates.

        Args:
            x: Center X coordinate
            y: Center Y coordinate
            scale: Scale factor (< 1.0 for pinch in/zoom out, > 1.0 for pinch out/zoom in)
            velocity: Pinch velocity in scale factor per second (default: 1.0)
        """
        session_id = await self._ensure_session()
        await self._request(
            "POST",
            f"/session/{session_id}/wda/pinch",
            json={
                "x": x,
                "y": y,
                "scale": scale,
                "velocity": velocity,
            },
        )
