#!/usr/bin/env bash
# Build the wheel, check the UI is inside, install it like `uvx` would, and health-check it.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist/*.whl
uv build --wheel --out-dir dist >/dev/null
WHEEL="$(ls dist/tuppence-*.whl | head -1)"
python3 - "$WHEEL" <<'PY'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
assert any(n.endswith("tuppence/web_dist/index.html") for n in names), "web_dist missing from wheel"
assert any("/web_dist/assets/" in n for n in names), "assets missing from wheel"
PY
TMP="$(mktemp -d)"
trap 'kill "${PID:-0}" 2>/dev/null || true; rm -rf "$TMP"' EXIT
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
uvx --from "$WHEEL" tuppence serve --no-browser --port "$PORT" --data-dir "$TMP/data" >"$TMP/log" 2>&1 &
PID=$!
if uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:${PORT}" --timeout 90 --expect-mode local; then
  curl -fsS "http://127.0.0.1:${PORT}/" | grep -q 'id="app"'
  echo "smoke: ok wheel"
else
  cat "$TMP/log"; exit 1
fi
