# AGENTS.md

## Architecture

All commits are manual. Do not commit anything via automated tooling.

Single-container Docker Compose app for automated Google Photos Takeout backup, designed to run browser and backup on separate hosts.

- **browser-server/** — Runs **undetected-grid** (patched Firefox + Selenium Grid), exposes the WebDriver HTTP API on port 4444. The Grid plugin intercepts WebDriver commands and encrypts/decrypts sensitive data via ECIES. `DISPLAY_MODE` selects the display strategy: `virtual` (Xvfb, automatic login — recommended) or `headed` (real display, manual login). The `manual` compose profile runs the auth step with manual login on a real display.
- **backup-server/** — Connects to the Grid HTTP API, drives the Takeout UI via Selenium WebDriver, downloads/processes archives. Designed to run on a separate host, connecting to `BROWSER_SERVER_URL`.

The Grid plugin (`grid-secure-channel-plugin/`) intercepts WebDriver HTTP commands to encrypt/decrypt cookie values and element text values using ECIES on the grid side. Auth state is stored as `.auth_encoded` (JSON with Grid-encrypted cookie values).

Both parts use **state machines** (`transitions` library) to drive the authentication and backup flows:
- `GoogleLoginMachine` in `auth.py` — Google login flow (Selenium sync API)
- `TakeoutMachine` in `backup-server/backup.py` — Takeout UI navigation and archive download (Grid managed downloads)

## Source files

| File | Role |
|---|---|
| `auth.py` | Shared Google login state machine (Selenium sync API, used by both `backup.py` and `manual_auth.py`) |
| `browser-server/manual_auth.py` | Interactive/Selenium auth flow (Python, `manual-auth` dep group) |
| `backup-server/backup.py` | Main backup orchestration (Python, `backup` dep group) |
| `backup-server/Dockerfile` | Build-time dependencies + gpth binary for backup-server |
| `browser-server/Dockerfile.undetected_grid` | Multi-stage build for undetected-grid container |
| `browser-server/undetected_grid_entrypoint.sh` | Entry point script for undetected-grid (starts Selenium Grid) |
| `browser-server/docker-compose.yaml` | Docker Compose config for undetected-grid and manual-auth |
| `grid-secure-channel-plugin/pom.xml` | Maven project for Java Grid plugin |
| `grid-secure-channel-plugin/src/main/java/.../EncryptionInterceptor.java` | Java Grid plugin (intercepts WebDriver HTTP) |
| `selenium-grid.toml` | Selenium Grid configuration (Docker paths) |

## Key conventions

- **4-space indentation.** Python code uses 4 spaces per indentation level.
- **Format code with `ruff format` before committing.** Run `ruff format .` to automatically format all Python files according to project style.
- **UI selectors are locale-dependent.** `keys_*.csv` maps logical names to visible button/label text (e.g. `export.ready.label` → `Завершено`). CSS classes are obfuscated and change. Set `GOOGLE_LANG` env var to `RU` or `EN` to pick the right file.
- **`auth.py` uses `transitions` state machine** with `Machine` (sync), `State`, `Event` classes — the transition graph is defined declaratively via `add_transitions(transition(...))` on methods.
- **Dependencies are managed by `uv`** via `pyproject.toml` dependency groups (`manual-auth`, `backup`, `browser`, `dev`), not separate packages. Install with `uv sync --group <name>`.
- **`backup-server` uses a dedicated Dockerfile** that bakes in dependencies at build time using `uv sync --group backup`, downloads `gpth` binary, and `COPY`s all source files.
- **`undetected-grid` uses multi-stage Docker build** to:
  1. Build the Java plugin (Maven)
  2. Patch Firefox at build time (copy to `/opt/undetected_firefox`, replace `libxul.so`)
  3. Copy patched Firefox, geckodriver, Selenium Server JAR, and plugin JAR into final runtime image
- **Do not use `is_{state_name}` in method names for state machines.** The `transitions` library auto-generates `is_<state_name>()` methods on the model for each state. If a condition method has the same name as a state, the auto-generated method shadows the condition method. Use alternative naming like `is_choosing_account()`, `has_account_chooser()`, or `on_account_chooser_page()` instead.

## Running

All commands use Docker Compose from the project root directory.

```sh
# Undetected-grid browser server (virtual display, recommended)
docker compose --env-file .env --env-file browser-server/.env \
  -f browser-server/docker-compose.yaml up -d

# Manual auth (extends undetected-grid)
docker compose --env-file .env --env-file browser-server/.env \
  -f browser-server/docker-compose.yaml --profile manual up manual-auth

# Backup server (builds from Dockerfile)
docker compose --env-file .env --env-file backup-server/.env \
  -f backup-server/docker-compose.yaml up -d
```

### Selenium Grid version

The Selenium Grid version is pinned in `pyproject.toml` (`selenium>=4.10.0`), the JAR file (downloaded from GitHub in `browser-server/Dockerfile.undetected_grid`), `pom.xml` (`selenium-grid` `<version>`), and must be kept in sync across all three.

## Encryption key rotation

Grid plugin keys are generated on start by default. After restart, `.auth_encoded` must be regenerated by re-authenticating (since the Grid encrypts with new keys), and `ENCODED_PASS` must be re-encoded via the web tool link printed in Grid plugin logs. For stable keys, set `SK`/`PK` env vars in the browser-server/.env file.

## Testing

No test framework is configured. `backup-server/test/` contains fixture data, not tests.

## Linting

Before committing, run `ruff format .` and `ruff check .` to verify code quality. The `PLC0415` rule enforces no imports inside functions (imports must be at the top level).
