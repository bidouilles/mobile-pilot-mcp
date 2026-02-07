# ios-simulator-mcp

![Dashboard Screenshot](docs/dashboard-screenshot.png)

[![Build Status](https://github.com/bidouilles/ios-simulator-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bidouilles/ios-simulator-mcp/actions/workflows/ci.yml)

MCP server for iOS Simulator automation through WebDriverAgent.
Use it from Claude, Cursor, Windsurf, and other MCP clients to tap, type, swipe, take screenshots, launch apps, and monitor sessions live.

## Why This Project

- Fast UI iteration on iOS simulators with natural-language automation.
- Reliable visual validation with screenshots, recordings, and a live dashboard.
- Better Flutter workflows when paired with Dart MCP for runtime + tooling context.

## Prerequisites

- macOS with Xcode installed
- Xcode Command Line Tools: `xcode-select --install`
- Python 3.10+
- WebDriverAgent cloned locally from `https://github.com/appium/WebDriverAgent`

## Quick Start

1. Install and activate:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

2. Start WebDriverAgent:

```bash
# clone once (if you do not already have it)
git clone https://github.com/appium/WebDriverAgent.git ~/WebDriverAgent

# default path used by script: $HOME/WebDriverAgent
./scripts/start_wda.sh
# or
./scripts/start_wda.sh <UDID>

# optional explicit form (same as default)
WDA_PATH=~/WebDriverAgent ./scripts/start_wda.sh <UDID>

# if your clone is elsewhere, set the actual path (example: sibling directory)
WDA_PATH=../WebDriverAgent ./scripts/start_wda.sh <UDID>
```

3. Add MCP server to Claude Code:

```bash
claude mcp add ios-simulator -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp
```

If WDA is not on localhost:

```bash
claude mcp add ios-simulator -e WDA_HOST=192.168.1.30 -- /path/to/ios-simulator-mcp/venv/bin/ios-simulator-mcp
```

Optional verification:

```bash
python scripts/test_install.py
```

## Dashboard

The dashboard runs automatically with the server at `http://localhost:8200`.

Main capabilities:
- Real-time tool-call monitoring
- Live screenshot preview with click-to-tap
- Quick actions (connect, capture, home, UI tree, recording, apps)

## Flutter Development

For Flutter work, use this MCP server together with Dart MCP to combine simulator control and Flutter runtime tooling.

Reference: [Flutter announcement (July 23, 2025)](https://blog.flutter.dev/supercharge-your-dart-flutter-development-experience-with-the-dart-mcp-server-2edcc8107b49)

Minimum versions called out in that announcement:
- Dart SDK `3.9+`
- Flutter `3.35 beta+`

```bash
claude mcp add --transport stdio dart -- dart mcp-server
```

Recommended split:
- `ios-simulator-mcp`: simulator control, screenshots, gestures, app/system actions
- `dart mcp-server`: runtime errors, widget/runtime introspection, hot reload, tests, pub.dev/package workflows

Optional (older Dart setups that still need experimental flag):

```bash
claude mcp add --transport stdio dart -- dart mcp-server --experimental-mcp-server
```

## Where Everything Else Lives

Detailed documentation is in `docs/`:

- [Setup and Configuration](docs/setup-and-config.md) for full setup, WDA host/port details, env vars, and client configuration
- [Tools Reference](docs/tools-reference.md) for complete tool catalog, predicates, bundle IDs, and examples
- [Troubleshooting](docs/troubleshooting.md) for common errors and fixes
- [Docs Index](docs/README.md) for all docs pages

## Development

```bash
pip install -e "[dev]"
ruff check .
python scripts/test_install.py
```

## Contributing

1. Fork and clone the repository.
2. Set up local environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e "[dev]"
```

3. Run quality checks before opening a PR:

```bash
ruff check .
python scripts/test_install.py
```

4. Open a PR with:
- clear purpose and scope
- verification steps/commands
- screenshot/recording for dashboard or UI behavior changes

Contributor conventions and repo guidelines are documented in `AGENTS.md`.

## License

Apache 2.0
