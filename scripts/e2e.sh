#!/usr/bin/env bash
# Build the UI, start a fresh server-mode Tuppence (A), run Playwright specs 01-04 against it; then start a second,
# completely fresh server (B) and run the specs tagged @fresh (05-onboarding) against that. 05 must see no data at all,
# and 01-04 must not touch B, so the two servers never share a run.
# On any failure (server start, smoke check or tests) the server logs are printed before cleanup.
set -euo pipefail
cd "$(dirname "$0")/.."
npm --prefix web run build
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
DATA="$(mktemp -d)"
DATA_B="$(mktemp -d)"
PORT="$(free_port)"
PORT_B="$(free_port)"
LLM_PORT="$(free_port)"
PID=""; PID_B=""; LLM_PID=""
cleanup() {
  local status=$?
  kill $PID $PID_B $LLM_PID 2>/dev/null || true
  if [ "$status" -ne 0 ]; then
    for log in "$DATA/server.log" "$DATA_B/server.log" "$DATA/fake_llm.log"; do
      if [ -f "$log" ]; then
        echo "--- $log ---" >&2
        cat "$log" >&2 || true
      fi
    done
  fi
  rm -rf "$DATA" "$DATA_B"
  exit "$status"
}
trap cleanup EXIT
start_server() { # data-dir port
  uv run tuppence serve --mode server --host 127.0.0.1 --port "$2" --data-dir "$1" >"$1/server.log" 2>&1 &
}
start_server "$DATA" "$PORT"; PID=$!
uv run python tests/fakes/fake_llm.py --port "$LLM_PORT" >"$DATA/fake_llm.log" 2>&1 &
LLM_PID=$!
export FAKE_LLM_URL="http://127.0.0.1:$LLM_PORT"
uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:$PORT" --timeout 60 --expect-mode server
for _ in $(seq 1 100); do
  if curl -fsS "$FAKE_LLM_URL/health" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
curl -fsS "$FAKE_LLM_URL/health" >/dev/null
cd web
# Extra arguments (a spec file, --grep ...) go to both runs; a run with nothing to do is not an error then.
EXTRA=()
if [ "$#" -gt 0 ]; then EXTRA=(--pass-with-no-tests "$@"); fi
TUPPENCE_URL="http://127.0.0.1:$PORT" npx playwright test --project chromium "${EXTRA[@]}"
cd ..
start_server "$DATA_B" "$PORT_B"; PID_B=$!
uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:$PORT_B" --timeout 60 --expect-mode server
cd web
TUPPENCE_FRESH_URL="http://127.0.0.1:$PORT_B" npx playwright test --project fresh "${EXTRA[@]}"
