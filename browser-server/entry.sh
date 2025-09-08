#!/bin/sh
pip install --root-user-action=ignore --disable-pip-version-check -q eciespy eth-keys coincurve websockets
python $@