# Setup and Configuration

## Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

For development:

```bash
pip install -e "[dev]"
```

Optional one-step setup:

```bash
./scripts/setup.sh
```

## Start WebDriverAgent

WDA must be running for UI automation.

Clone WebDriverAgent (one-time). It lives in the `appium-ios` monorepo; the old
`appium/WebDriverAgent` repo stopped at v16.15.3. Tested with WDA 17.0.11.

```bash
git clone https://github.com/appium/appium-ios.git ~/appium-ios
```

```bash
# Option A: helper script (default path: ~/appium-ios/packages/webdriveragent)
./scripts/start_wda.sh <UDID>

# If no UDID is provided, the script shows device choices
./scripts/start_wda.sh

# Optional explicit form (same as default):
WDA_PATH=~/appium-ios/packages/webdriveragent ./scripts/start_wda.sh <UDID>

# If WebDriverAgent is elsewhere, set the actual path:
WDA_PATH=../appium-ios/packages/webdriveragent ./scripts/start_wda.sh <UDID>
```

Manual `xcodebuild` example:

```bash
xcodebuild -project ~/appium-ios/packages/webdriveragent/WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro' \
  test
```

Find booted simulator UDID:

```bash
xcrun simctl list devices | grep Booted
```

## MCP Client Configuration

### Claude Code (CLI)

```bash
claude mcp add mobile-pilot -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
claude mcp add mobile-pilot -e WDA_HOST=192.168.1.30 -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
claude mcp remove mobile-pilot
```

### Codex (CLI)

```bash
codex mcp add mobile-pilot -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
codex mcp add mobile-pilot -e WDA_HOST=192.168.1.30 -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
codex mcp remove mobile-pilot
```

### Dart MCP (optional)

Needs Dart 3.9 / Flutter 3.35 or later.

```bash
claude mcp add --transport stdio dart -- dart mcp-server
codex mcp add dart -- dart mcp-server
```

Connecting the Dart MCP server to a running app needs a **DTD URI** (Dart Tooling Daemon), not a VM service URI:

| Flutter / Dart | How to connect |
|----------------|----------------|
| 3.44 / 3.12 or later | Ask the Dart MCP server: `dtd` tool, `listDtdUris` then `connect` |
| 3.35 – 3.41 / 3.9 – 3.11 | Start the app with `flutter run --print-dtd`, then pass the printed URI to `connect_dart_tooling_daemon` |

### JSON config (Claude/Cursor/Windsurf)

```json
{
  "mcpServers": {
    "mobile-pilot": {
      "command": "/path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp",
      "env": {
        "WDA_HOST": "192.168.1.30"
      }
    }
  }
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WDA_HOST` | `127.0.0.1` | WebDriverAgent host |
| `WDA_PORT` | `8100` | Port used by `scripts/start_wda.sh` for launching WDA. For MCP calls, pass `port` to `start_bridge`. |
| `DASHBOARD_PORT` | `8200` | Dashboard web port |
| `DASHBOARD_AUTO_OPEN` | `true` | Auto-open dashboard browser tab |

## Dashboard Settings

```bash
DASHBOARD_PORT=9000
DASHBOARD_AUTO_OPEN=false
```

## Run Server Directly

```bash
./scripts/run_server.sh
```

## Smoke Test

```bash
python scripts/test_install.py
```
