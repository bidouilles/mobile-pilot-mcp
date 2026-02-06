# CLAUDE.md - ios-simulator-mcp

This file provides context for AI assistants working on this project.

## Project Overview

**ios-simulator-mcp** - MCP server for iOS Simulator automation via WebDriverAgent. Control simulators from Claude, Cursor, and other AI assistants. Tap, type, swipe, screenshot, launch apps, and more.

This Python MCP server enables AI assistants to automate iOS Simulators via WebDriverAgent (WDA). It provides tools for UI automation, screenshots, app control, and system interactions.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   MCP Client    │────▶│   MCP Server    │────▶│ WebDriverAgent  │
│ (Claude, etc.)  │     │  (Python/stdio) │     │  (on Simulator) │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │  xcrun simctl   │
                        │ (device mgmt)   │
                        └─────────────────┘
```

### Key Components

- **`server.py`**: MCP server entry point, tool definitions and handlers
- **`wda_client.py`**: HTTP client for WebDriverAgent API (W3C WebDriver + WDA extensions)
- **`simulator.py`**: iOS Simulator management via `xcrun simctl`
- **`ui_tree.py`**: Parses WDA accessibility hierarchy into usable format

## WebDriverAgent

WDA is located at `../WebDriverAgent` relative to this directory. It must be running on the simulator for UI automation to work.

### Starting WDA

```bash
cd ../WebDriverAgent
xcodebuild -project WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner \
  -destination 'platform=iOS Simulator,id=<UDID>' \
  test
```

### WDA Host Configuration

WDA typically binds to the machine's network IP (e.g., `192.168.1.30:8100`), not `127.0.0.1`. Set `WDA_HOST` environment variable or pass `host` parameter to `start_bridge`.

## Common Development Tasks

### Running the Server

```bash
source venv/bin/activate
WDA_HOST=192.168.1.30 python -m ios_simulator_mcp.server
```

### Testing Changes

```bash
# Test imports and basic functionality
python scripts/test_install.py

# Manual testing via MCP client
# Configure your MCP client to connect to the server
```

### Adding a New Tool

1. Add `Tool` definition to `TOOLS` list in `server.py`
2. Add handler in `handle_tool()` function
3. If WDA API needed, add method to `wda_client.py`

## WDA API Reference

### Session Management
- `POST /session` - Create session
- `DELETE /session/{id}` - Delete session
- `GET /status` - Server status

### UI Hierarchy
- `GET /session/{id}/source?format=json` - Get UI tree (JSON)
- `GET /session/{id}/source` - Get UI tree (XML)

### Touch Actions (W3C Actions API - preferred)
```python
{
    "actions": [{
        "type": "pointer",
        "id": "finger1",
        "parameters": {"pointerType": "touch"},
        "actions": [
            {"type": "pointerMove", "x": x, "y": y},
            {"type": "pointerDown", "button": 0},
            {"type": "pause", "duration": 50},
            {"type": "pointerUp", "button": 0}
        ]
    }]
}
```

### WDA-Specific Endpoints (fallback)
- `POST /session/{id}/wda/tap/0` - Tap at coordinates
- `POST /session/{id}/wda/doubleTap` - Double tap
- `POST /session/{id}/wda/touchAndHold` - Long press
- `POST /session/{id}/wda/dragfromtoforduration` - Swipe
- `POST /session/{id}/wda/apps/launch` - Launch app
- `POST /session/{id}/wda/pressButton` - Hardware button
- `POST /session/{id}/wda/keyboard/dismiss` - Dismiss keyboard
- `POST /session/{id}/wda/device/appearance` - Set dark/light mode
- `GET /session/{id}/wda/device/appearance` - Get current appearance
- `POST /session/{id}/wda/touch_id` - Simulate biometrics
- `POST /session/{id}/wda/video/start` - Start screen recording
- `POST /session/{id}/wda/video/stop` - Stop recording
- `POST /session/{id}/wda/pinch` - Pinch gesture

## Error Handling

### Common WDA Errors

1. **Session expired**: Use `reset_session` tool or call `delete_session()` + `create_session()`
2. **Connection refused**: WDA not running, check with `health_check()`
3. **Unknown error**: Usually means WDA returned an error in unexpected format - check logs

### Error Response Formats

WDA can return errors in multiple formats:
```python
# Standard WebDriver
{"error": "no such element", "message": "..."}

# WDA-specific
{"value": {"error": "...", "message": "..."}}

# Status code
{"status": 7, "value": {"message": "..."}}
```

## Simulator Management

### simctl Commands Used

```bash
xcrun simctl list devices -j       # List devices (JSON)
xcrun simctl boot <UDID>           # Boot simulator
xcrun simctl shutdown <UDID>       # Shutdown simulator
xcrun simctl io <UDID> screenshot  # Take screenshot
xcrun simctl launch <UDID> <app>   # Launch app
xcrun simctl openurl <UDID> <url>  # Open URL
xcrun simctl status_bar <UDID> override --time "9:41"  # Override status bar
xcrun simctl status_bar <UDID> clear  # Clear overrides
```

### Status Bar Override

Use `set_status_bar` for consistent screenshots (e.g., App Store submissions):
```
set_status_bar device_id="..." time="9:41" battery_level=100 wifi_bars=3
```

Common options: `time`, `battery_level`, `battery_state`, `data_network`, `wifi_bars`, `cellular_bars`, `operator_name`

Clear with `clear_status_bar device_id="..."`

## UI Tree Format

Elements are indexed for easy reference:
```
[0] Application "MyApp"
  [1] Window
    [2] Button "Login"
    [3] TextField "Username"
    [4] SecureTextField "Password"
```

Use index in `tap` command: `tap device_id="..." index=2`

## Screenshot Optimization

Screenshots are automatically optimized to reduce context usage:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `scale` | 0.5 | Scale factor (0.5 = half size) |
| `format` | jpeg | `jpeg` (smaller) or `png` (lossless) |
| `quality` | 85 | JPEG quality 1-100 |

**Typical results:**
- Original: 1170x2532 PNG = ~620KB
- Optimized: 585x1266 JPEG = ~50KB (91% reduction)

**When to use different settings:**
- Default (`scale=0.5, format=jpeg`): General automation, UI verification
- `scale=1.0`: Need to read small text or see fine details
- `format=png`: Need pixel-perfect accuracy, OCR on text
- `scale=0.25`: Quick state checks, thumbnails

## Advanced Features

### Dark Mode Testing

Switch between dark and light mode for UI testing:
```
set_appearance device_id="..." appearance="dark"
set_appearance device_id="..." appearance="light"
get_appearance device_id="..."
```

### Screen Recording

Capture video of UI interactions:
```
start_recording device_id="..." fps=24 quality="medium"
# ... perform actions ...
stop_recording device_id="..."
# Returns: /tmp/ios-simulator-mcp/recordings/recording-YYYYMMDD-HHMMSS.mp4
```

### Biometric Simulation

Test Touch ID / Face ID authentication flows:
```
# Trigger successful auth
simulate_biometrics device_id="..." match=true

# Trigger failed auth
simulate_biometrics device_id="..." match=false
```

### Pinch/Zoom Gestures

```
# Zoom in (scale > 1.0)
pinch device_id="..." x=200 y=400 scale=2.0

# Zoom out (scale < 1.0)
pinch device_id="..." x=200 y=400 scale=0.5
```

### Keyboard Management

```
# Dismiss keyboard after typing
type_text device_id="..." text="hello"
dismiss_keyboard device_id="..."
```

## Dart MCP Integration

The `discover_dtd_uris` tool helps discover running Dart Tooling Daemon (DTD) URIs on the local machine. These URIs are needed by the Dart MCP server for Flutter debugging features like hot reload, widget inspection, and runtime error reporting.

### Usage

```
discover_dtd_uris timeout=3.0
```

**Output example:**
```
Found running Dart VM services:

- ws://127.0.0.1:49778/BACzVeYQggg=/ws
  Process: /path/to/dart
  VM: Dart VM
```

### How it works

The tool discovers DTD URIs by:
1. Scanning running `dart`/`flutter` processes for VM service URIs in command line arguments
2. Checking Flutter tool state files
3. Probing dart processes with listening TCP ports
4. Searching macOS system logs for recent VM service URIs

### Integration with Dart MCP Server

Pass the discovered URI to the Dart MCP server's `connect_dart_tooling_daemon` tool:
```
connect_dart_tooling_daemon uri="ws://127.0.0.1:49778/BACzVeYQggg=/ws"
```

This enables:
- `hot_reload` - Apply code changes without losing app state
- `hot_restart` - Full restart with code changes
- `get_widget_tree` - Inspect Flutter widget hierarchy
- `get_runtime_errors` - See errors from running app

## Tips for AI Assistants

1. **Always call `start_bridge` first** before any UI automation
2. **Get UI tree** before tapping to see available elements
3. **Use predicates** for robust element selection (vs hardcoded indices)
4. **Reset session** if getting repeated errors
5. **Check WDA host** - often not localhost
6. **Screenshots are optimized by default** - 50% scale JPEG saves ~90% context
7. **Use `scale=1.0`** only when you need to read small text
8. **Screenshots saved to** `/tmp/ios-simulator-mcp/screenshots/`
9. **Use `set_status_bar`** for consistent screenshots (time="9:41", battery_level=100)
10. **Use `discover_dtd_uris`** to find DTD URIs for Dart MCP integration
11. **Use `dismiss_keyboard`** after typing to clear the keyboard
12. **Use `set_appearance`** to test dark/light mode UI
13. **Use `start_recording`/`stop_recording`** to capture interaction videos
14. **Use `simulate_biometrics`** to test Touch ID/Face ID flows
15. **Use `pinch`** for map/image zoom testing (scale > 1 = zoom in, < 1 = zoom out)

## Dependencies

- `mcp>=1.0.0` - MCP SDK
- `httpx>=0.27.0` - Async HTTP client
- `Pillow>=10.0.0` - Image processing (for screenshots)

## File Locations

- Screenshots: `/tmp/ios-simulator-mcp/screenshots/`
- Recordings: `/tmp/ios-simulator-mcp/recordings/`
- WDA Project: `../WebDriverAgent/`
- Simulator data: `~/Library/Developer/CoreSimulator/Devices/<UDID>/`
