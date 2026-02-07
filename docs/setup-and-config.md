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

Clone WebDriverAgent (one-time):

```bash
git clone https://github.com/appium/WebDriverAgent.git ~/WebDriverAgent
```

```bash
# Option A: helper script (default path: ~/WebDriverAgent)
./scripts/start_wda.sh <UDID>

# If no UDID is provided, the script shows device choices
./scripts/start_wda.sh

# Optional explicit form (same as default):
WDA_PATH=~/WebDriverAgent ./scripts/start_wda.sh <UDID>

# If WebDriverAgent is elsewhere, set the actual path:
WDA_PATH=../WebDriverAgent ./scripts/start_wda.sh <UDID>
```

Manual `xcodebuild` example:

```bash
xcodebuild -project ~/WebDriverAgent/WebDriverAgent.xcodeproj \
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
claude mcp add ios-simulator -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp
claude mcp add ios-simulator -e WDA_HOST=192.168.1.30 -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp
claude mcp remove ios-simulator
```

### JSON config (Claude/Cursor/Windsurf)

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
