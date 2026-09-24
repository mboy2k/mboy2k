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
