"""A fake LLM server speaking the OpenAI, Anthropic and Gemini wire formats over real HTTP.

Run it with `python tests/fakes/fake_llm.py --port N`. Every chat reply is
"Echo: <last user message>", except when the request carries a schema (or a system
message mentions "JSON Schema"): then the model text is `{"category": "test", "confidence": 1}`.
`GET /_last` returns the last chat request body (any style); `POST /_reset` clears it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

# Make the repository root importable when run as a script.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals import oracle  # noqa: E402

STRUCTURED = json.dumps({"category": "test", "confidence": 1})
SCRIPT: list[str] = []
CALLS = {"count": 0, "understanding": 0}
_UNDERSTANDING = (
    oracle.CATEGORISE_MARKER,
    oracle.REVIEW_MARKER,
    oracle.REFILE_MARKER,
    oracle.LABELS_MARKER,
)


def canned_reply(messages: list[dict[str, Any]]) -> str | None:
    """Scripted replies first, then the oracle for Tuppence's own prompts. None means: answer
    as before ("Echo: …"). Understanding calls are counted apart and never scripted."""
    text = json.dumps(messages)
    if any(marker in text for marker in _UNDERSTANDING):
        CALLS["understanding"] += 1
        return oracle.reply(messages)
    CALLS["count"] += 1
    if SCRIPT:
        return SCRIPT.pop(0)
    if oracle.READ_MARKER in text or oracle.MAPPING_MARKER in text:
        return oracle.reply(messages)
    return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and "text" in p
        )
    return ""


def _reply(text: str, wants_schema: bool) -> str:
    return STRUCTURED if wants_schema else f"Echo: {text}"


def create_fake_app() -> FastAPI:
    app = FastAPI()
    last: dict[str, Any] = {}

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/_last")
    def get_last() -> dict[str, Any]:
        return last

    @app.post("/_reset")
    def reset() -> dict[str, bool]:
        last.clear()
        SCRIPT.clear()
        CALLS["count"] = 0
        CALLS["understanding"] = 0
        return {"ok": True}

    @app.post("/_script")
    async def script(body: dict[str, Any]) -> dict[str, int]:
        SCRIPT.extend(str(reply) for reply in body.get("replies", []))
        return {"queued": len(SCRIPT)}

    @app.get("/_calls")
    async def calls() -> dict[str, int]:
        return {"count": CALLS["count"], "understanding": CALLS["understanding"]}

    @app.get("/v1/models")
    def models(request: Request) -> dict[str, Any]:
        if "x-api-key" in request.headers:  # Anthropic style
            return {
                "data": [
                    {
                        "id": "claude-fake",
                        "display_name": "Claude Fake",
                        "max_input_tokens": 200000,
                        "max_tokens": 8192,
                        "capabilities": {
                            "structured_outputs": {"supported": True},
                            "image_input": {"supported": False},
                        },
                    }
                ],
                "has_more": False,
            }
        return {"data": [{"id": "fake-small"}, {"id": "fake-large"}]}

    @app.post("/v1/chat/completions")
    async def openai_chat(request: Request) -> dict[str, Any]:
        body = await request.json()
        last.clear()
        last.update(body)
        msgs = body.get("messages", [])
        user = next(
            (_text(m.get("content")) for m in reversed(msgs) if m.get("role") == "user"), ""
        )
        schema = "response_format" in body or any(
            m.get("role") == "system" and "JSON Schema" in _text(m.get("content")) for m in msgs
        )
        canned = canned_reply(msgs)
        return {
            "model": body.get("model", "fake-small"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": canned if canned is not None else _reply(user, schema),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6},
        }

    @app.post("/v1/messages")
    async def anthropic_chat(request: Request) -> dict[str, Any]:
        body = await request.json()
        last.clear()
        last.update(body)
        msgs = body.get("messages", [])
        user = next(
            (_text(m.get("content")) for m in reversed(msgs) if m.get("role") == "user"), ""
        )
        schema = "output_config" in body or "JSON Schema" in _text(body.get("system"))
        return {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "claude-fake"),
            "content": [{"type": "text", "text": _reply(user, schema)}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 6},
        }

    @app.get("/v1beta/models")
    def gemini_models() -> dict[str, Any]:
        return {
            "models": [
                {
                    "name": "models/gemini-fake",
                    "displayName": "Gemini Fake",
                    "inputTokenLimit": 1000000,
                    "outputTokenLimit": 8192,
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        }

    @app.post("/v1beta/models/{model}:generateContent")
    async def gemini_chat(model: str, request: Request) -> dict[str, Any]:
        if not model:
            raise HTTPException(404)
        body = await request.json()
        last.clear()
        last.update(body)
        contents = body.get("contents", [])
        user = next(
            (_text(c.get("parts")) for c in reversed(contents) if c.get("role") == "user"), ""
        )
        config = body.get("generationConfig", {})
        schema = "responseJsonSchema" in config or "JSON Schema" in _text(
            body.get("systemInstruction", {}).get("parts")
        )
        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": _reply(user, schema)}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 6},
        }

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(create_fake_app(), host="127.0.0.1", port=args.port, log_level="warning")
