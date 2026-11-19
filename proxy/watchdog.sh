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
    if [ "$got" != "$PORT" ]; then
      log "canh bao: port doi thanh $got (xem state/endpoint.txt)"
    fi
  fi
}

log "watchdog start pid=$$"
fails=0
tick=0
while :; do
  ensure_gost
  ensure_bore
  tick=$((tick + 1))
  # Mỗi phút test thật đường tunnel; tắc 2 lần liên tiếp thì dựng lại bore.
  if [ $((tick % 6)) -eq 0 ]; then
    ep_port=$PORT
    [ -s "$STATE/endpoint.txt" ] && ep_port=$(cut -d: -f2 "$STATE/endpoint.txt")
    if curl -s -m 8 -x "http://$U:$P@$RELAY:$ep_port" -o /dev/null https://api.ipify.org; then
      fails=0
    else
      fails=$((fails + 1))
      log "health check fail lan $fails"
      if [ "$fails" -ge 2 ]; then
        log "tunnel khong thong -> dung lai bore"
        pkill -x bore 2>/dev/null
        fails=0
        sleep 2
      fi
    fi
  fi
  sleep 10
done
