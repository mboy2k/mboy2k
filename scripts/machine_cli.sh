#!/usr/bin/env bash
# Command-line client for the machine API — for agents that speak shell, not MCP.
#
#   scripts/machine_cli.sh info
#   scripts/machine_cli.sh run "<command>" [timeout_seconds]
#   scripts/machine_cli.sh read <path>
#   scripts/machine_cli.sh write <path>        # content on stdin
#   scripts/machine_cli.sh ls [path]
#   scripts/machine_cli.sh base
#
# Override the target with MACHINE_URL (e.g. http://hoplite-pc:3000 over Tailscale).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

BASE="${MACHINE_URL:-http://127.0.0.1:${PORT:-3000}}"
KEY="${BROWSER_API_KEY:-}"

api() { curl -sS -H "X-API-Key: $KEY" -H 'Content-Type: application/json' "$@"; }

cmd="${1:-}"
[ $# -gt 0 ] && shift

case "$cmd" in
  base) echo "$BASE" ;;
  info) api "$BASE/machine/info" | jq . ;;
  run)
    api -X POST "$BASE/machine/exec" \
      -d "$(jq -n --arg c "${1:?thieu lenh}" --argjson t "${2:-60}" '{cmd:$c, timeout:$t}')" \
      | jq -r '.stdout + "[exit \(.code)] cwd=\(.cwd)"' ;;
  read)
    api -X POST "$BASE/machine/read" -d "$(jq -n --arg p "${1:?thieu duong dan}" '{path:$p}')" \
      | jq -r '.content' ;;
  write)
    api -X POST "$BASE/machine/write" \
      -d "$(jq -n --arg p "${1:?thieu duong dan}" --rawfile c /dev/stdin '{path:$p, content:$c}')" | jq . ;;
  ls)
    api -X POST "$BASE/machine/list" -d "$(jq -n --arg p "${1:-.}" '{path:$p}')" \
      | jq -r '.entries[] | "\(.type)\t\(.size)\t\(.name)"' ;;
  *)
    sed -n '2,11p' "$0"
    exit 1 ;;
esac
