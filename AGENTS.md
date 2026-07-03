# AGENTS.md

## Architecture

Two-container Docker Compose app for automated Google Photos Takeout backup, designed to run browser and backup on separate hosts.

- **browser-server/** — Runs **Firefox** via [`invisible-playwright`](https://github.com/feder-cr/invisible_playwright) (stealth, anti-fingerprint), exposes a WebSocket. An ECIES-encrypted reverse proxy (`webs.py`) sits in front, protecting credentials in transit. Runs profiles: `virtual` (xvfb, recommended), `headed` (real display, debug), `manual` (auth only).
- **backup-server/** — Connects to the browser WebSocket, drives the Takeout UI via Playwright, downloads/processes archives. Designed to run on a separate host, connecting to `BROWSER_SERVER_URL`.

The proxy (`browser-server/webs.py`) intercepts WebSocket messages to encrypt/decrypt `storageState` and password values. Auth state is stored as `.auth_encoded` (ECIES ciphertext).

Both parts use **state machines** (`transitions` library) to drive the authentication and backup flows:
- `GoogleLoginMachine` in `auth.py` — Google login flow
- `TakeoutMachine` in `backup-server/backup.py` — Takeout UI navigation and archive download

## Source files

| File | Role |
|---|---|
| `auth.py` | Shared Google login state machine (used by both `backup.py` and `manual_auth.py`) |
| `browser-server/webs.py` | Encrypted WebSocket proxy (Python, `proxy` dep group) |
| `browser-server/manual_auth.py` | Interactive/invisible-playwright auth flow (Python, `manual-auth` dep group) |
| `backup-server/backup.py` | Main backup orchestration (Python, `backup` dep group) |
| `backup-server/Dockerfile` | Build-time dependencies + gpth binary for backup-server |

## Key conventions

- **4-space indentation.** Python code uses 4 spaces per indentation level.
- **Format code with `ruff format` before committing.** Run `ruff format .` to automatically format all Python files according to project style.
- **UI selectors are locale-dependent.** `keys_*.csv` maps logical names to visible button/label text (e.g. `export.ready.label` → `Завершено`). CSS classes are obfuscated and change. Set `GOOGLE_LANG` env var to `RU` or `EN` to pick the right file.
- **`auth.py` uses `transitions` state machine** with `@with_model_definitions` and `@name_enricher` decorators — the transition graph is defined declaratively via `add_transitions(transition(...))` on methods, not via the usual `Machine` constructor.
- **Dependencies are managed by `uv`** via `pyproject.toml` dependency groups (`proxy`, `manual-auth`, `backup`, `dev`), not separate packages. Install with `uv sync --group <name>`.
- **`backup-server` uses a dedicated Dockerfile** (instead of runtime entrypoint script) that bakes in dependencies at build time using `uv sync --group backup`, downloads `gpth` binary, and `COPY`s all source files.
- **Do not use `is_{state_name}` in method names for state machines.** The `transitions` library auto-generates `is_<state_name>()` methods on the model for each state. If a condition method has the same name as a state (e.g., states `account_chooser`, `address_entry` and methods `is_account_chooser()`, `is_address_entry()`), the auto-generated method shadows the condition method, causing it to never be called. Use alternative naming like `is_choosing_account()`, `has_account_chooser()`, or `on_account_chooser_page()` instead.

## Running

All commands use Docker Compose from the project root directory.

```sh
# First: create the shared encrypted network
docker network create --opt encrypted --attachable secure_net

# Browser server (virtual display, recommended)
docker compose --env-file .env --env-file browser-server/.env \
  -f browser-server/docker-compose.yaml --profile virtual up -d

# Backup server (builds from Dockerfile)
docker compose --env-file .env --env-file backup-server/.env \
  -f backup-server/docker-compose.yaml up -d
```

### Playwright version

The Playwright version is declared in root `.env` (`PLAYWRIGHT_VERSION`) and pinned in `pyproject.toml` (`playwright==X.Y.Z`). Keep both in sync to avoid protocol version mismatches between the Python client and browser binary.

## Encryption key rotation

Browser-server proxy keys are generated on start by default. After restart, `.auth_encoded` and `ENCODED_PASS` must be re-encoded with the new public key (URL printed in proxy logs). For stable keys, set `SK`/`PK` env vars.

## Encryption key rotation

Browser-server proxy keys are generated on start by default. After restart, `.auth_encoded` and `ENCODED_PASS` must be re-encoded with the new public key (URL printed in proxy logs). For stable keys, set `SK`/`PK` env vars.

## Testing

No test framework is configured. `backup-server/test/` contains fixture data, not tests.
