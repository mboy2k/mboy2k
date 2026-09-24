#!/usr/bin/env bash
# Managed 24/7 gateway process for the Hoplite preview.
# Health probe on :3000 stays green only while the gateway lives.
set -u
export HERMES_HOME=/tmp/hoplite/workspace/.hermes-home
export HERMES_ACCEPT_HOOKS=1
python3 -m http.server 3000 --bind 0.0.0.0 &
http_pid=$!
cleanup() { kill "$http_pid" 2>/dev/null; }
trap cleanup EXIT
exec hermes gateway run --external-supervisor --accept-hooks
