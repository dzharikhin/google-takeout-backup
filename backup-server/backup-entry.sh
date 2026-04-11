#!/bin/sh
pip install --root-user-action=ignore --disable-pip-version-check -q playwright==${PLAYWRIGHT_VERSION}
if [ ! -f "/app/utils/gpth" ]; then
    curl -L --output /app/utils/gpth https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper/releases/download/v3.4.3/gpth-linux
fi
# ls /app
chmod +x /app/gpth
export PATH=$PATH:/app
python $@