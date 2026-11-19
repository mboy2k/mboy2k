#!/usr/bin/env bash
# Hoplite setup (idempotent): venv + deps + Chromium + .env + proxy cho BP Proxy Switcher.
set -euo pipefail
cd "$(dirname "$0")/.."
WORKSPACE="$(pwd)"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip -q install --upgrade pip
.venv/bin/pip -q install -r requirements.txt

# apt tới mirror Ubuntu bị chặn trong sandbox -> tải browser thẳng từ CDN Playwright.
PW_CACHE="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
if [ -z "$(ls -A "$PW_CACHE" 2>/dev/null)" ]; then
  .venv/bin/python -m playwright install chromium \
    || echo "hoplite-setup: canh bao: chua tai duoc Chromium (thu lai sau)"
fi

if [ ! -f .env ]; then
  printf 'BROWSER_API_KEY=%s\nHOST=0.0.0.0\nPORT=3000\n' \
    "$(python3 -c 'import secrets; print(secrets.token_hex(24))')" > .env
fi

# Proxy: sinh mật khẩu lần đầu (giữ nguyên nếu đã có), tải binary nếu thiếu, bật watchdog.
if [ ! -f proxy/auth.txt ]; then
  printf 'boss:%s\n' "$(python3 -c 'import secrets; print(secrets.token_hex(12))')" > proxy/auth.txt
fi
chmod 600 proxy/auth.txt
if [ ! -x proxy/bin/gost ] || [ ! -x proxy/bin/bore ]; then
  bash proxy/download.sh || echo "hoplite-setup: canh bao: chua tai duoc binary proxy"
fi
bash proxy/start.sh || echo "hoplite-setup: canh bao: proxy chua bat duoc (xem proxy/state)"

echo "hoplite-setup: ${WORKSPACE} san sang (venv + deps + Chromium + .env + proxy)"
