#!/bin/sh
pip install --root-user-action=ignore --disable-pip-version-check -q playwright==${PLAYWRIGHT_VERSION}
curl -s -L --output /app/gpth https://github.com/TheLastGimbus/GooglePhotosTakeoutHelper/releases/download/v3.4.3/gpth-linux
# ls /app
chmod +x /app/gpth
export PATH=$PATH:/app
python $@