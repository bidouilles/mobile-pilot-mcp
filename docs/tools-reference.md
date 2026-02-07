# Tools Reference

This list is aligned with the tools registered in `src/ios_simulator_mcp/server.py`.

## Device Management

| Tool | Description |
|------|-------------|
| `list_devices` | List all iOS simulators (booted and available) |
| `get_device` | Get device info by UDID |
| `boot_simulator` | Boot a simulator |
| `shutdown_simulator` | Shutdown a simulator |
| `start_bridge` | Connect to WebDriverAgent and create session |
| `reset_session` | Reset WDA session |

## UI Automation

| Tool | Description |
|------|-------------|
| `get_screenshot` | Capture screenshot with optimization options |
| `get_ui_tree` | Get accessibility tree with indices |
| `tap` | Tap by index, predicate, or coordinates |
| `type_text` | Type text (optional predicate target first) |
| `swipe` | Swipe by direction or coordinates |
| `double_tap` | Double tap at coordinates |
| `long_press` | Long press at coordinates |
| `pinch` | Zoom in/out gesture |

## Navigation and Apps

| Tool | Description |
|------|-------------|
| `go_home` | Navigate to home screen |
| `launch_app` | Launch app by bundle ID |
| `terminate_app` | Terminate app |
| `list_apps` | List installed apps |
| `open_url` | Open URL in Safari |
| `press_button` | Press hardware button |

## System

| Tool | Description |
|------|-------------|
| `set_location` | Set GPS location |
| `get_clipboard` | Get clipboard content |
| `set_clipboard` | Set clipboard content |
| `get_window_size` | Get screen dimensions |
| `set_status_bar` | Override status bar for stable screenshots |
| `clear_status_bar` | Clear status bar overrides |
| `dismiss_keyboard` | Dismiss the on-screen keyboard |
| `set_appearance` | Set dark/light mode |
| `get_appearance` | Get current appearance |
| `simulate_biometrics` | Simulate Touch ID / Face ID |
| `start_recording` | Start screen recording |
| `stop_recording` | Stop screen recording and save file |

## Alerts

| Tool | Description |
|------|-------------|
| `accept_alert` | Accept alert dialog |
| `dismiss_alert` | Dismiss alert dialog |
| `get_alert_text` | Get alert text |

## Flutter Integration

| Tool | Description |
|------|-------------|
| `discover_dtd_uris` | Discover Dart Tooling Daemon URIs |

## Predicates

Supported fields for element matching:
- `text`
- `text_contains`
- `text_starts_with`
- `type`
- `label`
- `identifier`
- `index`

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

## Usage Examples

Basic flow:

```text
1. list_devices (only_booted: true)
2. start_bridge (device_id: "...")
3. get_ui_tree (device_id: "...")
4. tap (device_id: "...", index: 5)
```

Tap variants:

```text
tap device_id="..." index=5
tap device_id="..." predicate={"text_contains": "Settings"}
tap device_id="..." x=200 y=400
```

Type text:

```text
type_text device_id="..." text="Hello World"
type_text device_id="..." text="username" predicate={"type": "TextField"}
```

Swipe:

```text
swipe device_id="..." direction="up"
swipe device_id="..." from_x=200 from_y=600 to_x=200 to_y=200
```

Status bar:

```text
set_status_bar device_id="..." time="9:41" battery_level=100 wifi_bars=3
clear_status_bar device_id="..."
```

Appearance:

```text
set_appearance device_id="..." appearance="dark"
get_appearance device_id="..."
```

Recording:

```text
start_recording device_id="..."
stop_recording device_id="..."
```

Screenshot options:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `scale` | `0.5` | Scale factor from `0.1` to `1.0` |
| `format` | `jpeg` | `jpeg` or `png` |
| `quality` | `85` | JPEG quality (ignored for PNG) |
