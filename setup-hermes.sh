#!/usr/bin/env bash
# Durable Hermes install for this workspace (idempotent, safe to re-run).
set -u
export HERMES_HOME=/tmp/hoplite/workspace/.hermes-home
mkdir -p "$HERMES_HOME"
if ! command -v hermes >/dev/null 2>&1; then
  echo "Installing Hermes Agent (skip browser tooling for speed) ..."
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh \
    | bash -s -- --skip-setup --skip-browser --skip-computer-use
fi
echo "Hermes: $(hermes --version | head -1)"
# Keep the bot self-starting after a sandbox restore, preview or not.
if ! pgrep -f "watchdog-hermes.sh" >/dev/null 2>&1; then
  setsid nohup bash /tmp/hoplite/workspace/watchdog-hermes.sh >> "$HERMES_HOME/watchdog.log" 2>&1 &
fi
