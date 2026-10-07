#!/usr/bin/env bash
# Run the local live test against a real llama.cpp server running Qwen2.5 0.5B (needs Docker).
# Never touches cloud endpoints: only `-k local` tests run. Removes the container it starts.
set -euo pipefail
cd "$(dirname "$0")/.."
MODELS_DIR="${TUPPENCE_MODELS_DIR:-$HOME/.cache/tuppence-models}"
MODEL_FILE="qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/$MODEL_FILE"
IMAGE="ghcr.io/ggml-org/llama.cpp:server"
NAME="tuppence-live-llamacpp-$$"
PORT=18081
mkdir -p "$MODELS_DIR"
if [ ! -s "$MODELS_DIR/$MODEL_FILE" ]; then
  curl -fL --retry 3 -o "$MODELS_DIR/$MODEL_FILE.part" "$MODEL_URL"
  mv "$MODELS_DIR/$MODEL_FILE.part" "$MODELS_DIR/$MODEL_FILE"
fi
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then docker logs --tail 40 "$NAME" >&2 2>/dev/null || true; fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8080" -v "$MODELS_DIR:/models:ro" "$IMAGE" \
  -m "/models/$MODEL_FILE" -c 4096 --host 0.0.0.0 --port 8080 >/dev/null
for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then break; fi
  sleep 1
done
MODEL_ID="$(curl -fsS "http://127.0.0.1:$PORT/v1/models" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')"
TUPPENCE_LIVE_LOCAL_BASE="http://127.0.0.1:$PORT/v1" TUPPENCE_LIVE_LOCAL_MODEL="$MODEL_ID" \
  uv run pytest -m live tests/live -k local -q
