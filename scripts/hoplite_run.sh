#!/usr/bin/env bash
# Hoplite Preview runner: browser API trên port 3000 + giữ proxy watchdog sống.
set -u
cd "$(dirname "$0")/.."
WORKSPACE="$(pwd)"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

# Preview panel luôn trỏ port 3000, bất kể .env ghi gì.
export PORT="${HOPLITE_PREVIEW_PORT:-3000}"
export HOST="${HOST:-0.0.0.0}"

if [ -n "${HOPLITE_PREVIEW_MANIFEST_PATH:-}" ]; then
  printf '{"version":1,"generation":"cloud-browser","profile":"default","runId":"cloud-browser","ports":{"preview":%s}}' "$PORT" \
    > "$HOPLITE_PREVIEW_MANIFEST_PATH"
fi

# Proxy cho BP Proxy Switcher: bật + canh mỗi 30 giây (idempotent, flock chống trùng).
bash "$WORKSPACE/proxy/start.sh" >/dev/null 2>&1 || true
( while true; do sleep 30; bash "$WORKSPACE/proxy/start.sh" >/dev/null 2>&1 || true; done ) &

while true; do
  "$WORKSPACE/.venv/bin/python" cloud_browser/app.py
  code=$?
  echo "[supervisor] app thoat (ma $code), khoi dong lai sau 2s" >&2
  sleep 2
done
