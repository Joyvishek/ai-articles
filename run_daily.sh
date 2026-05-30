#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
python3 ./main.py --config ./digest_config.json
