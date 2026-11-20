# Troubleshooting

## Cannot connect to WebDriverAgent

1. Call `start_bridge` again: it restarts WDA on a local host
2. If it could not start WDA, read the log it names (`/tmp/mobile-pilot-mcp/wda/wda-<UDID>.log`); signing and Xcode version errors show up there
3. Check that `WDA_PATH` points at `packages/webdriveragent` in your `appium-ios` clone
4. Set `WDA_HOST` when WDA is not on `127.0.0.1` (a remote WDA is never started for you)

## Codex MCP startup fails (`initialize response` / handshake closed)

If Codex shows:
- `MCP startup failed: handshaking with MCP server failed: connection closed: initialize response`

Try:
1. Re-add the server entry:
   - `codex mcp remove mobile-pilot`
   - `codex mcp add mobile-pilot -- /path/to/mobile-pilot-mcp/venv/bin/mobile-pilot-mcp`
2. Confirm the configured command:
   - `codex mcp get mobile-pilot`
3. Ensure your local code uses stdio and no startup banner in `src/mobile_pilot_mcp/server.py`:
   - `mcp.run(transport="stdio", show_banner=False)`
4. Restart Codex after updating the MCP entry or code.

## WDA unknown error or taps not working

1. Reset session: `reset_session device_id="..."`
2. Restart WDA: stop it, then call `start_bridge`
3. Verify tap coordinates are in bounds

## Screenshots missing

Screenshots are saved under `/tmp/mobile-pilot-mcp/screenshots/`.
Use the returned file path from `get_screenshot`.

## UI tree is empty

1. Ensure WDA session exists with `start_bridge`
2. Wait for target app to fully load
3. System dialogs can expose limited accessibility content

## Session expires

Call `reset_session` to create a fresh WDA session.
