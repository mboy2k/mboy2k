#!/usr/bin/env bash
# Dừng watchdog + gost + tunnel.
cd "$(dirname "$0")"
if [ -f state/watchdog.pid ] && kill -0 "$(cat state/watchdog.pid)" 2>/dev/null; then
  kill "$(cat state/watchdog.pid)" 2>/dev/null
fi
rm -f state/watchdog.pid state/watchdog.lock
pkill -x gost 2>/dev/null
pkill -x bore 2>/dev/null
sleep 1
echo "[proxy] đã dừng"
