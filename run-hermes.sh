#!/usr/bin/env bash
# 24/7 Hermes gateway unit for this sandbox.
# Resilience layers:
#   1. in-unit loop — the preview supervisor does not respawn an exited unit
#      (verified by kill test), so the gateway is relaunched here instead.
#   2. flock — exactly one unit owns the gateway, so a watchdog-relaunched copy
#      can never double the dispatcher when the platform also starts a unit.
set -u
export HERMES_HOME=/tmp/hoplite/workspace/.hermes-home
export HERMES_ACCEPT_HOOKS=1
mkdir -p "$HERMES_HOME"
exec 9>"$HERMES_HOME/gateway.lock"
if ! flock -n 9; then
  echo "$(date -Is) another unit owns the gateway; exiting" >> "$HERMES_HOME/gateway-restarts.log"
  exit 0
fi
# Health probe for preview verification; set HERMES_NO_HEALTH=1 to skip it when
# another instance already serves the port.
if [ "${HERMES_NO_HEALTH:-0}" != "1" ]; then
  python3 -m http.server 3000 --bind 0.0.0.0 &
  http_pid=$!
  cleanup() { kill "$http_pid" 2>/dev/null; }
  trap cleanup EXIT INT TERM
fi
while true; do
  hermes gateway run --external-supervisor --accept-hooks
  echo "$(date -Is) gateway exited (code $?), restarting in 5s" >> "$HERMES_HOME/gateway-restarts.log"
  sleep 5
done
