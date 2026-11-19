#!/usr/bin/env bash
# Idempotent Hoplite setup: virtualenv, dependencies, Chromium, .env seed.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

VENV=".venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements.txt

# Cached after the first run; --with-deps needs apt, so keep a plain fallback.
"$VENV/bin/python" -m playwright install --with-deps chromium \
  || "$VENV/bin/python" -m playwright install chromium

if [ ! -f .env ]; then
  gen() { "$VENV/bin/python" -c 'import secrets; print(secrets.token_hex(32))'; }
  cat > .env <<EOF
BROWSER_API_KEY=$(gen)
PROXY_USER=hoplite
PROXY_PASSWORD=$(gen)
HOST=0.0.0.0
PORT=3000
PROXY_HOST=0.0.0.0
PROXY_PORT=8888
EOF
  chmod 600 .env
fi

echo "setup ok"
