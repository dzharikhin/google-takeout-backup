#!/bin/sh
case "${COMPOSE_PROFILES}" in
  *,*)
    >&2 echo "only one profile is supported"
    exit 1
    ;;
  *)
    ;;
esac
cp /app/browser-launch-params.json /tmp/launch-params.json
IS_HEADLESS_MODE=$([ "${COMPOSE_PROFILES}" = "headless" ] && echo "true" || echo "false")
sed -i -e "s|\${BROWSER_PORT}|${BROWSER_PORT}|g" -e "s|\${IS_HEADLESS_MODE}|${IS_HEADLESS_MODE}|g" /tmp/launch-params.json
ADDITIONAL_ARGS=$([ "${COMPOSE_PROFILES}" = "headed" ] && echo ", \"--ozone-platform=wayland\"" || echo "")
sed -i -e "s|\${ADDITIONAL_ARGS}|${ADDITIONAL_ARGS}|g" /tmp/launch-params.json

cat /tmp/launch-params.json
if [ "${COMPOSE_PROFILES}" = "virtual" ]; then
  echo 'running on virtual display'
  exec xvfb-run -a --error-file=/app/browser-downloads/xvfb-${COMPOSE_PROFILES}.log npx -y playwright@${PLAYWRIGHT_VERSION} launch-server --browser=chromium --config /tmp/launch-params.json
else
  echo 'running on real display'
  exec npx -y playwright@${PLAYWRIGHT_VERSION} launch-server --browser=chromium --config /tmp/launch-params.json
fi
