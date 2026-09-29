#!/usr/bin/env bash
# Durable Hoplite setup: venv, Python deps, Chromium, and a .env with a fresh API key.
set -euo pipefail
cd "$(dirname "$0")/.."
WORKSPACE="$(pwd)"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip -q install --upgrade pip
.venv/bin/pip -q install -r requirements.txt

if [ -z "$(ls -A "${HOME}/.cache/ms-playwright" 2>/dev/null)" ]; then
  .venv/bin/python -m playwright install --with-deps chromium
fi

if [ ! -f .env ]; then
  printf 'BROWSER_API_KEY=%s\nHOST=0.0.0.0\nPORT=3000\n' \
    "$(python3 -c 'import secrets; print(secrets.token_hex(24))')" > .env
  echo "hoplite-setup: da tao .env voi BROWSER_API_KEY moi"
fi

echo "hoplite-setup: ${WORKSPACE} san sang (venv + deps + Chromium + .env)"
