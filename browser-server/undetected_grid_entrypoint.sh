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

if [ -n "${PK:-}" ] && [ -n "${SK:-}" ]; then
  cat >> /app/selenium-grid.toml <<EOF
[encryption]
public-key = "${PK}"
private-key = "${SK}"
EOF
fi

# Add file-stream system properties
export JAVA_OPTS="$JAVA_OPTS -Dfilestream.key=${FILE_STREAM_KEY:-} -Dfilestream.port=${FILE_STREAM_PORT:-4445}"

# Collect all plugin JARs
EXT_JARS=""
for jar in /app/plugins/*.jar; do
    [ -f "$jar" ] || continue
    EXT_JARS="${EXT_JARS:+$EXT_JARS:}$jar"
done

if [ -n "$EXT_JARS" ]; then
    exec java $JAVA_OPTS -jar /app/selenium-server.jar --ext "$EXT_JARS" standalone --config /app/selenium-grid.toml --log-level "${LOG_LEVEL:-INFO}"
else
    echo "Warning: no plugin JARs found, continuing without plugins"
    exec java $JAVA_OPTS -jar /app/selenium-server.jar standalone --config /app/selenium-grid.toml --log-level "${LOG_LEVEL:-INFO}"
fi
