# ios-simulator-mcp

MCP server for iOS Simulator automation via WebDriverAgent. Control simulators from Claude, Cursor, and other AI assistants. Tap, type, swipe, screenshot, launch apps, and more.

## Quick Start (3 Steps)

### 1. Install (one-time)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. Start WebDriverAgent on Simulator

```bash
# Start WDA (will show device list if no UDID provided)
./scripts/start_wda.sh

# Or with specific UDID
./scripts/start_wda.sh <UDID>
```

Note the WDA URL in output: `ServerURLHere->http://192.168.1.30:8100<-ServerURLHere`

### 3. Add to Claude Code

```bash
claude mcp add ios-simulator -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp
```

Or with WDA_HOST (if not localhost):
```bash
claude mcp add ios-simulator -e WDA_HOST=192.168.1.30 -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp
```

**That's it!** Now ask Claude to interact with your simulator.

---

## Features

- **Simulator Management**: List, boot, and shutdown iOS simulators via `xcrun simctl`
- **UI Automation**: Tap, type, swipe, and interact with apps via WebDriverAgent
- **Screenshot Capture**: Optimized screenshots (90% smaller with auto-compression)
- **App Control**: Launch, terminate, and list installed apps
- **Alert Handling**: Accept, dismiss, and read alert dialogs
- **System Control**: Set location, manage clipboard, press hardware buttons
- **Web Dashboard**: Real-time tool call monitoring at http://localhost:8200

## Prerequisites

- **macOS** with Xcode installed
- **Xcode Command Line Tools**: `xcode-select --install`
- **Python 3.10+**
- **WebDriverAgent**: Clone from https://github.com/appium/WebDriverAgent (default expected by scripts: `~/WebDriverAgent`)

## Detailed Setup

### Starting WebDriverAgent

WDA must be running for UI automation:

```bash
# Option A: By simulator name
cd ~/WebDriverAgent
xcodebuild -project WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro' \
  test

# Option B: By UDID
xcodebuild -project WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner \
  -destination 'platform=iOS Simulator,id=D8D53F70-4AB1-4B44-8602-82ED2AF4F2A9' \
  test

# Option C: Helper script (uses ~/WebDriverAgent by default)
./scripts/start_wda.sh <UDID>

# If WebDriverAgent is somewhere else:
WDA_PATH=../WebDriverAgent ./scripts/start_wda.sh <UDID>
```

### Finding Simulator UDID

```bash
xcrun simctl list devices | grep Booted
```

### WDA Host Configuration

WDA typically binds to your machine's IP (not localhost). Check the WDA output for the actual URL and set `WDA_HOST` accordingly.

## MCP Client Configuration

### Claude Code (CLI)

```bash
# Add MCP server
claude mcp add ios-simulator -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp

# With WDA_HOST environment variable
claude mcp add ios-simulator -e WDA_HOST=192.168.1.30 -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp

# Remove if needed
claude mcp remove ios-simulator
```

### Claude Code (Manual)

Add to `~/.claude/settings.json` or project `.claude/settings.json`:

```json
{
  "mcpServers": {
    "ios-simulator": {
      "command": "/path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp",
      "env": {
        "WDA_HOST": "192.168.1.30"
      }
    }
  }
}
```

### Cursor / Windsurf

```json
{
  "mcpServers": {
    "ios-simulator": {
      "command": "/path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp",
      "env": {
        "WDA_HOST": "192.168.1.30"
      }
    }
  }
}
```

## Available Tools

### Device Management

| Tool | Description |
|------|-------------|
| `list_devices` | List all iOS simulators (booted and available) |
| `get_device` | Get device info by UDID |
| `boot_simulator` | Boot a simulator |
| `shutdown_simulator` | Shutdown a simulator |
| `start_bridge` | Connect to WebDriverAgent and create session |
| `reset_session` | Reset WDA session (useful if errors occur) |

### UI Automation

| Tool | Description |
|------|-------------|
| `get_screenshot` | Capture screenshot with optimization (scale, format, quality) |
| `get_ui_tree` | Get accessibility tree with element indices |
| `tap` | Tap element by index, predicate, or coordinates |
| `type_text` | Type text (optionally tap element first via predicate) |
| `swipe` | Swipe gesture by direction or coordinates |
| `double_tap` | Double tap at coordinates |
| `long_press` | Long press at coordinates |

### Navigation & Apps

| Tool | Description |
|------|-------------|
| `go_home` | Navigate to home screen |
| `launch_app` | Launch app by bundle ID |
| `terminate_app` | Terminate app |
| `list_apps` | List installed apps |
| `open_url` | Open URL in Safari |
| `press_button` | Press hardware button (home, volumeUp, volumeDown) |

### System

| Tool | Description |
|------|-------------|
| `set_location` | Set GPS location |
| `get_clipboard` | Get clipboard content |
| `set_clipboard` | Set clipboard content |
| `get_window_size` | Get screen dimensions |
| `set_status_bar` | Override status bar (time, battery, network) for consistent screenshots |
| `clear_status_bar` | Clear status bar overrides |
| `dismiss_keyboard` | Dismiss the on-screen keyboard |
| `set_appearance` | Set dark mode or light mode |
| `get_appearance` | Get current appearance (dark/light) |
| `simulate_biometrics` | Simulate Touch ID / Face ID authentication |
| `start_recording` | Start screen recording |
| `stop_recording` | Stop recording and save video file |
| `pinch` | Pinch gesture for zoom in/out |

### Alerts

| Tool | Description |
|------|-------------|
| `accept_alert` | Accept alert dialog |
| `dismiss_alert` | Dismiss alert dialog |
| `get_alert_text` | Get alert text |

### Dart MCP Integration

| Tool | Description |
|------|-------------|
| `discover_dtd_uris` | Discover running Dart Tooling Daemon (DTD) URIs for Flutter debugging |

## Flutter Development

For Flutter app development, you can use this iOS Simulator MCP server alongside the **Dart MCP server** for a complete development experience. The Dart MCP server provides Flutter-specific features like hot reload, widget inspection, and runtime error reporting.

### Setup Dart MCP Server

```bash
# Add Dart MCP server to Claude Code
claude mcp add --transport stdio dart -- dart mcp-server
```

### Using Both Servers Together

1. **iOS Simulator MCP**: UI automation, screenshots, taps, swipes
2. **Dart MCP**: Hot reload, widget tree, runtime errors, code analysis

### Discover DTD URI

The `discover_dtd_uris` tool finds running Dart VM service URIs on your machine. These URIs are needed by the Dart MCP server to connect to your Flutter app:

```
# In iOS Simulator MCP
discover_dtd_uris

# Output:
# Found running Dart VM services:
# - ws://127.0.0.1:49778/BACzVeYQggg=/ws
#   Process: dart
#   VM: Dart VM
```

Then use this URI with the Dart MCP server:
```
# In Dart MCP
connect_dart_tooling_daemon uri="ws://127.0.0.1:49778/BACzVeYQggg=/ws"
```

### Flutter Workflow Example

```
1. Start your Flutter app: flutter run
2. discover_dtd_uris                        → Find DTD URI
3. connect_dart_tooling_daemon uri="..."    → Connect Dart MCP (in Dart MCP server)
4. start_bridge device_id="..."             → Connect iOS Simulator MCP
5. get_screenshot device_id="..."           → Capture current state
6. get_widget_tree                          → Inspect Flutter widgets (Dart MCP)
7. Make code changes
8. hot_reload                               → Apply changes instantly (Dart MCP)
9. get_screenshot device_id="..."           → Verify changes
```

## Usage Examples

### Basic Workflow

```
1. list_devices (only_booted: true)     → Get booted simulator UDID
2. start_bridge (device_id: "...")       → Connect to WDA
3. get_ui_tree (device_id: "...")        → See UI elements
4. tap (device_id: "...", index: 5)      → Tap element [5]
```

### Tap by Different Methods

```
# By index (from get_ui_tree)
tap device_id="..." index=5

# By predicate
tap device_id="..." predicate={"text_contains": "Settings"}
tap device_id="..." predicate={"type": "Button", "text": "OK"}

# By coordinates
tap device_id="..." x=200 y=400
```

### Type Text

```
# Type into focused field
type_text device_id="..." text="Hello World"

# Tap field first, then type
type_text device_id="..." text="username" predicate={"type": "TextField"}
```

### Swipe/Scroll

```
# By direction
swipe device_id="..." direction="up"      # Scroll down
swipe device_id="..." direction="down"    # Scroll up

# By coordinates
swipe device_id="..." from_x=200 from_y=600 to_x=200 to_y=200
```

### Launch App

```
launch_app device_id="..." bundle_id="com.apple.Preferences"
```

### Screenshot (Optimized)

Screenshots are automatically optimized to reduce file size and context usage:

```
# Default (recommended) - JPEG at 50% scale, ~85-90% smaller
get_screenshot device_id="..."

# Full size JPEG (when you need full detail)
get_screenshot device_id="..." scale=1.0

# PNG format (lossless, for text recognition)
get_screenshot device_id="..." format="png" scale=0.5

# Tiny preview (quick checks)
get_screenshot device_id="..." scale=0.25 quality=70
```

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `scale` | 0.5 | Scale factor 0.1-1.0 (0.5 = half size) |
| `format` | jpeg | Image format: `jpeg` or `png` |
| `quality` | 85 | JPEG quality 1-100 (ignored for PNG) |

**Example output:**
```
Screenshot saved: /tmp/ios-simulator-mcp/screenshots/screenshot-20260206-100447.jpg
Original: 1170x2532 (618.7KB)
Optimized: 585x1266 (52.3KB)
Reduction: 91.5%
```

### Status Bar Override (for consistent screenshots)

```
# Classic "9:41" Apple marketing look
set_status_bar device_id="..." time="9:41" battery_level=100 wifi_bars=3

# Full control
set_status_bar device_id="..." time="9:41" battery_level=100 battery_state="charged" data_network="5g" cellular_bars=4 operator_name="Carrier"

# Reset to normal
clear_status_bar device_id="..."
```

**Available options:**
| Parameter | Values | Description |
|-----------|--------|-------------|
| `time` | string | Time to display (e.g., "9:41") |
| `battery_level` | 0-100 | Battery percentage |
| `battery_state` | charging, charged, discharging | Battery icon state |
| `data_network` | hide, wifi, 3g, 4g, lte, lte-a, lte+, 5g, 5g+, 5g-uwb, 5g-uc | Network type |
| `wifi_mode` | searching, failed, active | WiFi connection state |
| `wifi_bars` | 0-3 | WiFi signal strength |
| `cellular_mode` | notSupported, searching, failed, active | Cellular state |
| `cellular_bars` | 0-4 | Cellular signal strength |
| `operator_name` | string | Carrier name (empty to hide) |

### Dark Mode / Light Mode

```
# Switch to dark mode
set_appearance device_id="..." appearance="dark"

# Switch to light mode
set_appearance device_id="..." appearance="light"

# Check current mode
get_appearance device_id="..."
```

### Screen Recording

```
# Start recording
start_recording device_id="..."

# With specific codec
start_recording device_id="..." codec="h264"

# Stop and save (returns file path)
stop_recording device_id="..."
# Output: Screen recording saved: /tmp/ios-simulator-mcp/recordings/recording-20260206-143022.mov
```

**Recording options:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `codec` | hevc | Video codec: `hevc` (smaller files) or `h264` (more compatible) |

### Biometric Authentication

Test Touch ID / Face ID flows:

```
# Simulate successful authentication
simulate_biometrics device_id="..."

# Simulate failed authentication
simulate_biometrics device_id="..." match=false
```

### Pinch/Zoom Gesture

```
# Zoom in (pinch out) - scale > 1.0
pinch device_id="..." x=200 y=400 scale=2.0

# Zoom out (pinch in) - scale < 1.0
pinch device_id="..." x=200 y=400 scale=0.5

# With velocity control
pinch device_id="..." x=200 y=400 scale=2.0 velocity=0.5
```

### Dismiss Keyboard

```
# After typing, dismiss the keyboard
dismiss_keyboard device_id="..."
```

## Predicate Fields

When using predicates to find elements:

| Field | Description |
|-------|-------------|
| `text` | Exact text match |
| `text_contains` | Contains substring (case-insensitive) |
| `text_starts_with` | Starts with prefix |
| `type` | Element type (Button, TextField, Switch, etc.) |
| `label` | Accessibility label |
| `identifier` | Accessibility identifier |
| `index` | Select Nth match (0-based) |

## Common Bundle IDs

| App | Bundle ID |
|-----|-----------|
| Settings | `com.apple.Preferences` |
| Safari | `com.apple.mobilesafari` |
| Maps | `com.apple.Maps` |
| Photos | `com.apple.Photos` |
| Calendar | `com.apple.mobilecal` |
| Notes | `com.apple.mobilenotes` |
| Mail | `com.apple.mobilemail` |
| Messages | `com.apple.MobileSMS` |
| App Store | `com.apple.AppStore` |
| Calculator | `com.apple.calculator` |
| Camera | `com.apple.camera` |
| Clock | `com.apple.clock` |

## Troubleshooting

### "Cannot connect to WebDriverAgent"

1. Make sure WDA is running (`./scripts/start_wda.sh <UDID>`)
2. Check the WDA output for the actual host/port
3. Set `WDA_HOST` if not `127.0.0.1`

### "WDA error: Unknown error" or tap not working

1. Reset the session: `reset_session device_id="..."`
2. Restart WDA if needed
3. Check coordinates are within screen bounds

### Screenshots not appearing

Screenshots are saved to `/tmp/ios-simulator-mcp/screenshots/`. Use the file path returned by `get_screenshot`.

### UI tree is empty

1. Ensure WDA is connected (`start_bridge`)
2. Wait for app to fully load
3. Some system dialogs may not expose accessibility info

### Session expires

Use `reset_session` to create a fresh WDA session.

## Web Dashboard

The MCP server includes a real-time web dashboard for monitoring and interacting with simulators.

![Dashboard Screenshot](docs/dashboard-screenshot.png)

**Features:**
- Real-time tool call monitoring via WebSocket
- Live screenshot preview with click-to-tap support
- Quick actions: Connect, Capture, Home, UI Tree, Record, Apps
- Swipe controls and text input
- Sequence recording and playback
- Keyboard shortcuts: `S` (screenshot), `H` (home), `R` (record), `U` (UI tree), `T` (text)

**Access:** Opens automatically at `http://localhost:8200` when the server starts.

**Configuration:**
```bash
# Change dashboard port
DASHBOARD_PORT=9000

# Disable auto-open browser
DASHBOARD_AUTO_OPEN=false
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WDA_HOST` | `127.0.0.1` | WebDriverAgent host |
| `WDA_PORT` | `8100` | Port used by `scripts/start_wda.sh` for launching WDA. For MCP calls, pass `port` to `start_bridge`. |
| `DASHBOARD_PORT` | `8200` | Web dashboard port |
| `DASHBOARD_AUTO_OPEN` | `true` | Auto-open dashboard in browser |

## Project Structure

```
ios-simulator-mcp/
├── pyproject.toml                    # Package configuration
├── README.md                         # This file
├── CLAUDE.md                         # AI assistant context
├── scripts/
│   ├── setup.sh                      # Setup script
│   ├── run_server.sh                 # Run MCP server
│   ├── start_wda.sh                  # Start WebDriverAgent
│   └── test_install.py               # Test installation
└── src/ios_simulator_mcp/
    ├── __init__.py
    ├── server.py                     # MCP server & tools
    ├── dashboard.py                  # Web dashboard server
    ├── simulator.py                  # simctl integration
    ├── wda_client.py                 # WebDriverAgent client
    ├── ui_tree.py                    # UI hierarchy parsing
    └── templates/
        └── dashboard.html            # Dashboard UI (HTML/CSS/JS)
```

## License

Apache 2.0
