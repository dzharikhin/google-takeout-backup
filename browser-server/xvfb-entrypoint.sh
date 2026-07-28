#!/bin/sh
if [ "${DISPLAY_MODE}" = "virtual" ]; then
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
  Xvfb :99 -screen 0 1920x1080x24 -ac &
  sleep 1
  export DISPLAY=:99
fi
exec "$@"
