#!/bin/sh
if [ "${HEADLESS_MODE}" = "virtual" ]; then
  Xvfb :99 -screen 0 1920x1080x24 -ac &
  sleep 1
  export DISPLAY=:99
fi
exec "$@"
