#!/bin/sh
case "${COMPOSE_PROFILES}" in
  *,*)
    >&2 echo "only one profile is supported"
    exit 1
    ;;
  *)
    ;;
esac

mkdir -p /app/browser-local-storage
mkdir -p /app/browser-downloads

python /app/generate_launch_config.py
exec npx -y playwright@${PLAYWRIGHT_VERSION} launch-server --browser=firefox --config /tmp/launch-params.json
