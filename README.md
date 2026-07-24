# Google photo takeout backup
Docker compose based app able to backup google photo via takeout by link

Google makes it very hard to automate takeout management - but it is possible

# Prerequisites
1. mainstream arch like `x86_64` - to be able to run [undetected-grid](https://github.com/ultrafunkamsterdam/undetected-firefox) and [gpth](https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper)
2. `docker`,`docker-compose`
3. `crontab` or another way to schedule automation and notify if something goes wrong
4. `ssmtp` or another channel to notify you about backup launch status
5. Any storage to mount to store backup in

# Arch
The app consists of two main parts:
1. Backup server - runs backup script, stores results to FS, provides auth info
2. Browser server - runs browser, ensures security of auth info

# How to use

# Browser server
Browser server runs [undetected-grid](https://github.com/ultrafunkamsterdam/undetected-firefox) — patched Firefox + Selenium Grid — and exposes the WebDriver API on port `4444`. You can run it on a dedicated node connected to the backup server, or on the same host.

All commands below run from the project root.

## File stream encryption key (`FILE_STREAM_KEY`)

Archive downloads stream from the Grid on port `4445`, encrypted with AES-256-GCM under a pre-shared key. Both servers must use the **same** key, or downloads fail with a GCM tag error.

Generate it once and keep the value secret (do **not** put it in any `.env`):

```sh
openssl rand -hex 32
```

- **Browser server:** provide it inline when starting the Grid (see step 2).
- **Backup server:** set it in the scheduler environment that runs `execute_backup.sh` (see step 6).

1. Put your Google account email in `browser-server/.env`:
    ```env
    USER_E=you@gmail.com
    ```
    > `USER_E` can be overridden inline on the command when running manual-auth — e.g. to back up a different account — and the inline value takes precedence over `.env`.

2. Start `undetected-grid` as a persistent service with `FILE_STREAM_KEY` set (see generation above):
    ```sh
    FILE_STREAM_KEY=<your-key> VERSION=$(uv version --short) \
    docker compose --env-file .env --env-file browser-server/.env \
      -f browser-server/docker-compose.yaml up -d undetected-grid
    ```
    > The key is read from the shell environment; it is **not** loaded from `.env`. After `docker compose down && up`, set it again.
    > Images are tagged with the project version from `pyproject.toml` via `uv version --short`.
    > `DISPLAY_MODE` selects where the browser renders:
    > - `virtual` (default) — renders on an Xvfb virtual display; no display required.
    > - `headed` — renders on your real `$DISPLAY`; run `xhost +local:` on the host first (XWayland on Wayland sessions) so the container can reach your X server.
    > To override, set `DISPLAY_MODE=headed` in `browser-server/.env` or inline on the command.
    > The login is automatic; `manual-auth` is just a one-shot sidecar.

3. Run `manual-auth` as a sidecar against the running grid to obtain auth state:
    ```sh
    USER_P=$(read -rsp "Google password: " p && echo "$p") VERSION=$(uv version --short) \
    docker compose --env-file .env --env-file browser-server/.env \
      -f browser-server/docker-compose.yaml --profile manual up manual-auth
    ```
    > `USER_P` is captured by a hidden `read` prompt and passed as an env var to compose (not written to disk or shell history).
    > The browser runs with a visible window to avoid bot detection; `DISPLAY_MODE` only selects where it renders (as noted in step 2).

4. **Store the auth state.** When `manual-auth` finishes it writes `browser-server/browser-downloads/.auth_encoded` (the values are already Grid-encrypted). Copy this file to `backup-server/.auth_encoded` — it is the cookie jar the backup server later loads.
    ```sh
    cp browser-server/browser-downloads/.auth_encoded backup-server/.auth_encoded
    ```

5. **Store the encoded password.** Get the Grid public-key web-tool link from the `undetected-grid` logs:
    ```sh
    docker compose --env-file .env --env-file browser-server/.env \
      -f browser-server/docker-compose.yaml logs undetected-grid
    ```
    Look for "Encode with: https://dzharikhin.github.io/ecies/?pk=" in the logs, open that link, encode your password, and save the result as `ENCODED_PASS` in `backup-server/.env`.
    > Encryption keys are generated on start by default, so after a restart you must re-run manual-auth (step 3) to regenerate `.auth_encoded` and re-encode `ENCODED_PASS` (step 5). To keep keys stable across restarts, set fixed `SK`/`PK` in `browser-server/.env`.

## Backup server
1. go to [backup-server](./backup-server)
   - `keys_RU.csv` - locale-dependent button names to interact with browser UI controls. If you need another locale, see how to use `GOOGLE_LANG` env param
   > there's no way to use locale-agnostic selectors there - css-classes are obfuscated and are changing ;(
2. create `downloads` dir - it's for backup intermediate processing: downloading, unpacking, sorting, etc - can be local FS
3. create `photos` dir - it's where final backups are stored to. If you have dedicated storage - here's convenient mount point
4. copy the `.auth_encoded` file produced by manual-auth (Browser server, step 4) here
    > `.auth_encoded` is the raw `driver.get_cookies()` output — values are already Grid-encrypted, so no external encoding is needed.
5. create `.env` file with `ENCODED_PASS` set to your Grid-encoded password (Browser server, step 5)
    > After `browser-server` key rotation, regenerate `.auth_encoded` by re-running manual-auth and re-encode `ENCODED_PASS` via the web tool. For stable keys, set fixed `SK`/`PK` in `browser-server/.env`.
 6. set `FILE_STREAM_KEY` in the scheduler environment (e.g. the crontab line or a systemd unit) — it must match the value the browser server started with. Then schedule `execute_backup.sh` to run in the [backup-server](./backup-server) working directory frequently enough for your backup purposes
    > [execute_backup.sh](./backup-server/execute_backup.sh) fails fast if `FILE_STREAM_KEY` is unset and derives the image tag version from `pyproject.toml` via `uv version --short`. It requires local customization (e.g. notification transport) before use.
  7. schedule command to reset browser from time to time(once a month is good enough)
      from `./browser-server` location