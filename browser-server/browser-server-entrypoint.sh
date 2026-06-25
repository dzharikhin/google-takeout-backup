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

if [ "${COMPOSE_PROFILES}" = "virtual" ]; then
  echo 'running on virtual display'
  exec xvfb-run -a --error-file=/app/browser-downloads/xvfb-${COMPOSE_PROFILES}.log \
    python /app/generate_launch_config.py && \
    exec npx -y playwright@${PLAYWRIGHT_VERSION} launch-server --browser=firefox --config /tmp/launch-params.json
else
  echo 'running on real display'
  exec python /app/generate_launch_config.py && \
    exec npx -y playwright@${PLAYWRIGHT_VERSION} launch-server --browser=firefox --config /tmp/launch-params.json
fi
