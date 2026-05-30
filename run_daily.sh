#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
python3 ./ai_article_digest.py --config ./digest_config.json
