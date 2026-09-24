#!/usr/bin/env bash
# Managed 24/7 gateway process for the Hoplite preview. The gateway is
# re-launched in-unit because the preview supervisor does not respawn a unit
# that exits on its own (verified by kill test).
set -u
export HERMES_HOME=/tmp/hoplite/workspace/.hermes-home
export HERMES_ACCEPT_HOOKS=1
python3 -m http.server 3000 --bind 0.0.0.0 &
http_pid=$!
cleanup() { kill "$http_pid" 2>/dev/null; }
trap cleanup EXIT INT TERM
while true; do
  hermes gateway run --external-supervisor --accept-hooks
  echo "$(date -Is) gateway exited (code $?), restarting in 5s" >> "$HERMES_HOME/gateway-restarts.log"
  sleep 5
done
