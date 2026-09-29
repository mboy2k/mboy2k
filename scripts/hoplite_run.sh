#!/usr/bin/env bash
# Hoplite Preview runner: Hoplite PC + browser API on the preview port, restart on crash.
set -u
cd "$(dirname "$0")/.."
WORKSPACE="$(pwd)"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

# The preview panel always points at port 3000, whatever .env says.
export PORT="${HOPLITE_PREVIEW_PORT:-3000}"
export HOST="${HOST:-0.0.0.0}"

if [ -n "${HOPLITE_PREVIEW_MANIFEST_PATH:-}" ]; then
  printf '{"version":1,"generation":"cloud-browser","profile":"default","runId":"cloud-browser","ports":{"preview":%s}}' "$PORT" \
    > "$HOPLITE_PREVIEW_MANIFEST_PATH"
fi

# Keep the remote door (sshd + tailscale) alive next to the app.
bash "$WORKSPACE/scripts/machine_access.sh" --quiet || true
( while true; do sleep 20; bash "$WORKSPACE/scripts/machine_access.sh" --quiet >/dev/null 2>&1 || true; done ) &

while true; do
  "$WORKSPACE/.venv/bin/python" cloud_browser/app.py
  code=$?
  echo "[supervisor] app thoat (ma $code), khoi dong lai sau 2s" >&2
  sleep 2
done
