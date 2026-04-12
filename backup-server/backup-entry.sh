#!/bin/sh
pip install --root-user-action=ignore --disable-pip-version-check -q playwright==${PLAYWRIGHT_VERSION}
if [ ! -f "/app/utils/gpth" ]; then
    curl -L --output /app/utils/gpth https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper/releases/download/v3.4.3/gpth-linux
    chmod +x /app/utils/gpth
fi
# ls /app
export PATH=$PATH:/app:/app/utils
python $@