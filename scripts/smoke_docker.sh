#!/usr/bin/env bash
# Build the image, run it on a random port, check /health and the UI, clean up.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-tuppence:smoke}"
NAME="tuppence-smoke-$$"
docker build -t "$IMAGE" .
docker run -d --name "$NAME" -p 127.0.0.1::8040 "$IMAGE" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
PORT="$(docker port "$NAME" 8040/tcp | head -1 | sed 's/.*://')"
if uv run --quiet python scripts/smoke_http.py "http://127.0.0.1:${PORT}" --timeout 60 --expect-mode server; then
  # the container must run as a non-root user
  USER_ID="$(docker exec "$NAME" id -u)"
  [ "$USER_ID" != "0" ] || { echo "smoke: FAILED container runs as root"; exit 1; }
  # on-device OCR must load (OpenCV's system library, the bundled models) and run in the
  # sandbox, whose child process has networking switched off
  docker exec "$NAME" /app/.venv/bin/python -c "
from PIL import Image, ImageDraw
from tuppence.ingest.ocr import image_rows
from tuppence.ingest.results import parse_image_rows
from tuppence.ingest.sandbox import run_isolated
if __name__ == '__main__':
    image = Image.new('RGB', (480, 90), 'white')
    ImageDraw.Draw(image).text((10, 20), 'Greenbasket Stores 42.18', fill='black', font_size=32)
    image.save('/tmp/ocr-smoke.png')
    assert run_isolated(image_rows, '/tmp/ocr-smoke.png', timeout_s=90, parse=parse_image_rows).rows
" || { echo "smoke: FAILED on-device OCR does not run in the image"; exit 1; }
  # the real UI (not the "UI not built" page) must be served at /
  curl -fsS "http://127.0.0.1:${PORT}/" | grep -q 'id="app"' \
    || { echo "smoke: FAILED / does not serve the built UI"; exit 1; }
  echo "smoke: ok docker"
else
  docker logs "$NAME" || true
  exit 1
fi
