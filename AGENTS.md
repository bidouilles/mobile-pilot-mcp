# Repository Guidelines

## Project Structure & Module Organization
Core Python code lives in `src/ios_simulator_mcp/`:
- `server.py`: FastMCP server and tool registration.
- `simulator.py`, `wda_client.py`, `ui_tree.py`: simulator control, WDA API, and UI parsing.
- `dashboard.py` and `templates/dashboard.html`: local monitoring UI.

Operational scripts are in `scripts/` (`setup.sh`, `start_wda.sh`, `run_server.sh`, `test_install.py`). Docs and reference assets are in `docs/`.

## Build, Test, and Development Commands
- `python3 -m venv venv && source venv/bin/activate`: create/activate local environment.
- `pip install -e .`: install the package in editable mode.
- `pip install -e ".[dev]"`: install development tools (`pytest`, `pytest-asyncio`, `ruff`).
- `./scripts/setup.sh`: one-step prerequisite check + local setup.
- `./scripts/start_wda.sh <UDID>`: launch WebDriverAgent for a simulator.
- `./scripts/run_server.sh`: start MCP server from local source.
- `python scripts/test_install.py`: smoke test imports, simulator discovery, and WDA health.
- `ruff check .`: run lint checks.

## Coding Style & Naming Conventions
- Target runtime: Python 3.10+.
- Follow Ruff settings from `pyproject.toml` (`line-length = 100`, rules `E,F,I,W`).
- Use type hints and `from __future__ import annotations` for new modules.
- Naming: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_CASE` for constants.
- Keep modules focused; avoid mixing transport, parsing, and simulator orchestration logic in one file.

## Testing Guidelines
`pytest` and `pytest-asyncio` are configured, but the repository currently uses `scripts/test_install.py` as the primary verification path.
- Add new tests under `tests/` with names like `test_<feature>.py`.
- Prefer async tests for WDA/simulator flows and keep external dependencies mocked when possible.
- Run `python scripts/test_install.py` before opening a PR; run `pytest` when test files are present.

## Commit & Pull Request Guidelines
Recent history follows Conventional Commit types, often with an emoji prefix (example: `✨ feat: ...`, `🐛 fix: ...`, `♻️ refactor: ...`).
- Keep commit subjects imperative and scoped to one change.
- PRs should include: purpose, key changes, verification steps/commands, and related issue links.
- For dashboard or UI behavior changes, include a screenshot or short recording.

## Security & Configuration Tips
- Do not commit simulator artifacts, logs with secrets, or local IP-specific credentials.
- Use environment variables (`WDA_HOST`, `LOG_LEVEL`) instead of hardcoding local machine values.
