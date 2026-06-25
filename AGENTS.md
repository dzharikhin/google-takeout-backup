# AGENTS.md

## Architecture

Two-container Docker Compose app for automated Google Photos Takeout backup.

- **browser-server/** — Runs Chromium via `npx playwright launch-server`, exposes a WebSocket. An ECIES-encrypted reverse proxy (`webs.py`) sits in front, protecting credentials in transit. Profiles: `headless` (bot-detectable), `virtual` (xvfb, recommended), `headed` (real display, debug), `manual` (auth only).
- **backup-server/** — Connects to the browser WebSocket, drives the Takeout UI via Playwright, downloads/processes archives using `gpth`.

The proxy (`browser-server/webs.py`) intercepts Playwright CDP messages to encrypt/decrypt `storageState` and password values. Auth state is stored as `.auth_encoded` (ECIES ciphertext).

## Source files

| File | Role |
|---|---|
| `auth.py` | Shared Google login state machine (used by both `backup.py` and `manual_auth.py`) |
| `browser-server/webs.py` | Encrypted WebSocket proxy (Python, `proxy` dep group) |
| `browser-server/manual_auth.py` | Interactive/invisible-playwright auth flow (Python, `manual-auth` dep group) |
| `backup-server/backup.py` | Main backup orchestration (Python, `backup` dep group) |
| `backup-server/Dockerfile` | Build-time dependencies + gpth binary for backup-server |

## Key conventions

- **UI selectors are locale-dependent.** `keys_*.csv` maps logical names to visible button/label text (e.g. `export.ready.label` → `Завершено`). CSS classes are obfuscated and change. Set `GOOGLE_LANG` env var to `RU` or `EN` to pick the right file.
- **`auth.py` uses `transitions` state machine** with `@with_model_definitions` and `@name_enricher` decorators — the transition graph is defined declaratively via `add_transitions(transition(...))` on methods, not via the usual `Machine` constructor.
- **Dependencies are managed by `uv`** via `pyproject.toml` dependency groups (`proxy`, `manual-auth`, `backup`, `dev`), not separate packages. Install with `uv sync --group <name>`.
- **`backup-server` uses a dedicated Dockerfile** (instead of runtime entrypoint script) that bakes in dependencies at build time using `uv sync --group backup`, downloads `gpth` binary, and `COPY`s all source files.

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

## Planned: Chromium → Firefox migration

See `backup_integration.md` for the plan to switch from Chromium to Firefox via `invisible-playwright` (anti-fingerprint, human-like mouse). Current code still uses Chromium.

## Testing

No test framework is configured. `backup-server/test/` contains fixture data, not tests.
