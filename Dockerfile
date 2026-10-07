# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TUPPENCE_DATA_DIR=/data
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
# OpenCV (headless build, used by RapidOCR) needs this one system library.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY --from=web /src/src/tuppence/web_dist ./src/tuppence/web_dist
RUN uv sync --frozen --no-dev --no-editable \
 && useradd --system --uid 10001 --home-dir /app tuppence \
 && mkdir -p /data && chown tuppence /data
USER tuppence
VOLUME ["/data"]
EXPOSE 8040
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8040/health', timeout=4).status == 200 else 1)"]
CMD ["/app/.venv/bin/tuppence", "serve", "--mode", "server", "--host", "0.0.0.0", "--port", "8040"]
