#!/bin/sh

# Set Java tmpdir for Grid managed downloads
export JAVA_OPTS="-Djava.io.tmpdir=/app/grid-downloads"
mkdir -p /app/grid-downloads

# Generate Grid config with static paths (Docker paths are deterministic)
cat > /app/selenium-grid.toml << 'EOF'
[server]
  port = 4444

[node]
  detect-drivers = false
  enable-managed-downloads = true
  max-sessions = 1

  [[node.driver-configuration]]
    display-name = "Firefox (patched)"
    stereotype = '{"browserName": "firefox", "moz:firefoxOptions": {"binary": "/opt/undetected_firefox/firefox", "args": [], "prefs": {"browser.download.folderList": 2, "browser.download.dir": "/app/grid-downloads", "browser.download.useDownloadDir": true, "browser.helperApps.neverAsk.saveToDisk": "application/zip,application/octet-stream", "pdfjs.disabled": true}}}'
    webdriver-executable = "/usr/local/bin/geckodriver"
EOF

# Start Grid with plugin
JAVA_JAR=/app/plugins/grid-secure-channel-plugin.jar
if [ ! -f "$JAVA_JAR" ]; then
  echo "Warning: grid-secure-channel-plugin JAR not found, continuing without plugin"
  exec java $JAVA_OPTS -jar /app/selenium-server.jar standalone --config /app/selenium-grid.toml --log-level "${LOG_LEVEL:-INFO}"
else
  exec java $JAVA_OPTS -jar /app/selenium-server.jar --ext "$JAVA_JAR" standalone --config /app/selenium-grid.toml --log-level "${LOG_LEVEL:-INFO}"
fi
