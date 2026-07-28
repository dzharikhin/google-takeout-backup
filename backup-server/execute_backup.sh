#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ -z "${FILE_STREAM_KEY:-}" ]; then
  echo "FILE_STREAM_KEY is not set. Generate it with 'openssl rand -hex 32'," 1>&2
  echo "then set it in the scheduler environment (e.g. crontab) and re-run." 1>&2
  exit 1
fi
if ! VERSION=$(uv version --short 2>/dev/null); then
  echo "Could not derive image version via 'uv version --short' (is uv installed and pyproject.toml reachable?)." 1>&2
  exit 1
fi
VERSION="$VERSION" docker compose --env-file ../.env --env-file .env run --rm --remove-orphans backup > /tmp/gtb.out 2>&1
if [ $? -ne 0 ]; then
  echo "Backup run has failed"
  cat /tmp/gtb.out
  SUBJECT="Google Takeout Backup run failed"
else
  echo "Backup run is successful"
  SUBJECT="Google Takeout Backup run is successful"
fi
printf "Subject: $SUBJECT\n\n%s" "$(cat /tmp/gtb.out)"
VERSION="$VERSION" docker compose --env-file ../.env --env-file .env down --volumes
