#!/usr/bin/env bash
# Build the UI, start a fresh server-mode Tuppence, run Playwright against it.
# On any failure (server start, smoke check or tests) the server log is printed before cleanup.
set -euo pipefail
cd "$(dirname "$0")/.."
npm --prefix web run build
DATA="$(mktemp -d)"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
uv run tuppence serve --mode server --host 127.0.0.1 --port "$PORT" --data-dir "$DATA" >"$DATA/server.log" 2>&1 &
PID=$!
cleanup() {
  local status=$?
  kill "$PID" 2>/dev/null || true
  if [ "$status" -ne 0 ] && [ -f "$DATA/server.log" ]; then
    echo "--- Tuppence server log ---" >&2
    cat "$DATA/server.log" >&2 || true
  fi
  rm -rf "$DATA"
  exit "$status"
}
trap cleanup EXIT
uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:$PORT" --timeout 60 --expect-mode server
cd web
TUPPENCE_URL="http://127.0.0.1:$PORT" npx playwright test "$@"
