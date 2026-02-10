# Mobile Pilot MCP

![Dashboard Screenshot](docs/dashboard-screenshot.png)

[![Build Status](https://github.com/bidouilles/mobile-pilot-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bidouilles/mobile-pilot-mcp/actions/workflows/ci.yml)

**Your AI co-pilot for mobile simulators.**

Other tools let you tap. We give you a cockpit.

Mobile Pilot MCP gives AI assistants a control plane for simulator automation, visual validation, and repeatable mobile workflows.

## Why Mobile Pilot MCP

- Cockpit dashboard with real-time tool calls, device controls, and visual feedback
- Fast automation loops for taps, swipes, typing, screenshots, recording, and app actions
- Flutter-first workflow when paired with Dart MCP server
- Cross-platform direction: iOS today, Android coming soon

## Quick Start (4 Commands)

```bash
python3 -m venv venv && source venv/bin/activate && pip install -e .
./scripts/start_wda.sh <UDID>
claude mcp add mobile-pilot -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
python scripts/test_install.py
```

Codex CLI equivalent:

```bash
codex mcp add mobile-pilot -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
```

If WDA is not on localhost:

```bash
claude mcp add mobile-pilot -e WDA_HOST=192.168.1.30 -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
```

```bash
codex mcp add mobile-pilot -e WDA_HOST=192.168.1.30 -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp
```

## Try This First

Once connected in your MCP client, try prompts like:

- `List my booted simulators and connect to the first one.`
- `Take a screenshot, then show me the UI tree and tap the Settings button.`
- `Launch Safari, open https://flutter.dev, and capture another screenshot.`
- `Start a recording, perform a swipe up, stop recording, and give me the output path.`

## Flutter + Dart MCP

Use both servers together for a stronger Flutter dev loop.

```bash
claude mcp add --transport stdio dart -- dart mcp-server
```

```bash
codex mcp add dart -- dart mcp-server
```

Recommended split:
- `mobile-pilot-mcp`: simulator control, screenshots, gestures, app/system actions
- `dart mcp-server`: runtime errors, widget/runtime introspection, hot reload, tests, pub.dev/package workflows

Reference: [Supercharge Your Dart & Flutter Development Experience with the Dart and Flutter MCP Server](https://blog.flutter.dev/supercharge-your-dart-flutter-development-experience-with-the-dart-mcp-server-2edcc8107b49)

## Comparison

| Capability | Mobile Pilot MCP | Typical simulator-only MCP |
|---|---|---|
| Dashboard cockpit | Yes | Usually no |
| Real-time tool-call timeline | Yes | Usually no |
| Visual interaction loop (live screenshot + actions) | Yes | Partial |
| Flutter pairing story (Dart MCP) | First-class | Rare |
| Cross-platform roadmap | iOS now, Android planned | Often iOS-only |

## Dashboard

Available at `http://localhost:8200` when the server starts.

## Documentation

- [Setup and Configuration](docs/setup-and-config.md)
- [Tools Reference](docs/tools-reference.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Documentation Index](docs/README.md)

## Contributing

1. Fork and clone the repository.
2. Set up local environment and install dev deps:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e "[dev]"
```

3. Run checks before opening a PR:

```bash
ruff check .
python scripts/test_install.py
```

4. For dashboard/UI changes, include a screenshot or short recording in the PR.

## License

Apache 2.0
