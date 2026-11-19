#!/usr/bin/env bash
# Bật proxy + watchdog (idempotent) và in ra endpoint để nhập vào BP Proxy Switcher.
set -u
cd "$(dirname "$0")"
ROOT=$(pwd)
STATE="$ROOT/state"; mkdir -p "$STATE" "$ROOT/bin"

if [ ! -x "$ROOT/bin/gost" ] || [ ! -x "$ROOT/bin/bore" ]; then
  echo "[proxy] thiếu binary, đang tải..."
  bash "$ROOT/download.sh" || exit 1
fi
[ -f "$ROOT/auth.txt" ] || { echo "[proxy] thiếu auth.txt"; exit 1; }

if [ -f "$STATE/watchdog.pid" ] && kill -0 "$(cat "$STATE/watchdog.pid")" 2>/dev/null; then
  echo "[proxy] watchdog đang chạy (pid $(cat "$STATE/watchdog.pid"))"
else
  setsid nohup bash "$ROOT/watchdog.sh" >>"$STATE/watchdog.log" 2>&1 </dev/null &
  sleep 2
fi

for i in 1 2 3 4 5 6 7 8 9 10; do
  [ -s "$STATE/endpoint.txt" ] && break
  sleep 2
done
if [ -s "$STATE/endpoint.txt" ]; then
  echo "[proxy] endpoint: $(cat "$STATE/endpoint.txt")"
else
  echo "[proxy] chưa lấy được endpoint — xem $STATE/bore.log"
fi
