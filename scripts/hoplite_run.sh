#!/usr/bin/env bash
# Hoplite run script: load .env, then serve the browser API plus the forward proxy.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -x .venv/bin/python ]; then
  bash scripts/hoplite_setup.sh
fi

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

exec .venv/bin/python cloud_browser/app.py
