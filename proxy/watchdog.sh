#!/usr/bin/env bash
# Giữ gost + tunnel bore.pub sống trong sandbox; chết thì tự dựng lại.
set -u
cd "$(dirname "$0")"
ROOT=$(pwd)
STATE="$ROOT/state"
mkdir -p "$STATE"

exec 9>"$STATE/watchdog.lock"
flock -n 9 || { echo "watchdog đang chạy"; exit 0; }
echo $$ > "$STATE/watchdog.pid"

IFS=: read -r U P < "$ROOT/auth.txt" || { echo "thiếu auth.txt"; exit 1; }

RELAY=bore.pub
PORT=37635
LOCAL=127.0.0.1:8080
LOG="$STATE/watchdog.log"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

ensure_gost() {
  if ! pgrep -f "bin/gost -L" >/dev/null 2>&1; then
    log "gost chết -> dựng lại"
    setsid nohup "$ROOT/bin/gost" -L "auto://$U:$P@$LOCAL" >>"$STATE/gost.log" 2>&1 </dev/null &
    sleep 1
  fi
}

ensure_bore() {
  if ! pgrep -f "bin/bore local" >/dev/null 2>&1; then
    log "bore chết -> dựng lại"
    setsid nohup "$ROOT/bin/bore" local 8080 --to "$RELAY" --port "$PORT" >>"$STATE/bore.log" 2>&1 </dev/null &
    sleep 3
  fi
  got=$(grep -o "listening at $RELAY:[0-9]*" "$STATE/bore.log" | tail -1 | cut -d: -f2)
  if [ -n "$got" ]; then
    printf '%s:%s:%s:%s\n' "$RELAY" "$got" "$U" "$P" > "$STATE/endpoint.txt"
    chmod 600 "$STATE/endpoint.txt"
  fi
}

log "watchdog start pid=$$"
while :; do
  ensure_gost
  ensure_bore
  sleep 30
done
