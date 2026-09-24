#!/usr/bin/env bash
# Detached watchdog: relaunches the gateway unit when it disappears, so the bot
# survives the preview unit being killed without anyone calling preview_start.
# It cannot survive sandbox reclamation — nothing inside the sandbox can.
set -u
export HERMES_HOME=/tmp/hoplite/workspace/.hermes-home
export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin
log="$HERMES_HOME/watchdog.log"
mkdir -p "$HERMES_HOME"
echo "$(date -Is) watchdog started (pid $$)" >> "$log"
# Grace period so a freshly started preview unit can take ownership first.
sleep "${HERMES_WATCHDOG_START_DELAY:-60}"
while true; do
  sleep 30
  [ -e "$HERMES_HOME/watchdog.pause" ] && continue
  pgrep -f "bash /tmp/hoplite/workspace/run-hermes.sh" >/dev/null 2>&1 && continue
  echo "$(date -Is) gateway unit missing; relaunching" >> "$log"
  setsid nohup bash /tmp/hoplite/workspace/run-hermes.sh >>"$HERMES_HOME/unit.log" 2>&1 &
done
