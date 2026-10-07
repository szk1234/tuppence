"""Google Gemini generateContent over raw HTTP.

The API key travels in the ``x-goog-api-key`` header, never in the URL query, so it
can't leak through logged URLs.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from tuppence.llm.providers.base import get_json, join_url, post_json
from tuppence.llm.types import (
    ChatRequest,
    ChatResponse,
    LLMBadResponse,
    LLMHTTPError,
    LLMRefused,
    Message,
    ProviderModel,
    ToolCall,
    Usage,
)

BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


def _convert(messages: list[Message]) -> tuple[list[str], list[dict[str, Any]]]:
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system.append(m.content)
        elif m.role == "user":
            if not m.content:
                continue
            contents.append({"role": "user", "parts": [{"text": m.content}]})
        elif m.role == "assistant":
            parts: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
            for c in m.tool_calls:
                call_part: dict[str, Any] = {"functionCall": {"name": c.name, "args": c.arguments}}
                signature = (c.provider_meta or {}).get("thoughtSignature")
                if isinstance(signature, str):
                    call_part["thoughtSignature"] = signature
                parts.append(call_part)
            if not parts:
                continue  # the API rejects empty parts
            contents.append({"role": "model", "parts": parts})
        else:
            part = {
                "functionResponse": {"name": m.name or "tool", "response": {"result": m.content}}
            }
            if (
                contents
                and contents[-1]["role"] == "user"
                and "functionResponse" in contents[-1]["parts"][0]
            ):
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    if not contents:
        raise ValueError("There is no message content to send")
    return system, contents


_FINISH = {"STOP": "stop", "MAX_TOKENS": "length"} | dict.fromkeys(BLOCKED, "content_filter")


def _base(url: str) -> str:
    url = url.strip().rstrip("/")
    for suffix in ("/v1beta", "/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/")


def _model_path(model: str) -> str:
    return quote(model.strip().removeprefix("models/"), safe="")


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _limit(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _model(item: dict[str, Any]) -> ProviderModel:
    name = item.get("displayName")
    return ProviderModel(
        id=item["name"].removeprefix("models/"),
        display_name=name if isinstance(name, str) else None,
        context_window=_limit(item.get("inputTokenLimit")),
        max_output_tokens=_limit(item.get("outputTokenLimit")),
        supports_tools=True,
        supports_json_schema=True,
    )


def _refused(reason: str) -> LLMRefused:
    return LLMRefused(f"The model declined this request ({reason})")


def _parse_chat(data: dict[str, Any], requested_model: str) -> ChatResponse:
    feedback = data.get("promptFeedback")
    block = feedback.get("blockReason") if isinstance(feedback, dict) else None
    if block:
        raise _refused(str(block))
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise LLMBadResponse("Gemini returned no candidates")
    cand = candidates[0]
    finish = cand.get("finishReason")
    if isinstance(finish, str) and finish in BLOCKED:
        raise _refused(finish)
    content = cand.get("content")
    if content is None:
        content = {}
    if not isinstance(content, dict):
        raise LLMBadResponse("Candidate content wasn't an object")
    raw_parts = content.get("parts")
    if raw_parts is None:
        raw_parts = []
    if not isinstance(raw_parts, list):
        raise LLMBadResponse("Candidate parts weren't a list")
    texts: list[str] = []
    calls: list[ToolCall] = []
    for i, part in enumerate(raw_parts):
        if not isinstance(part, dict):
            raise LLMBadResponse("Response part wasn't an object")
        if part.get("thought") is True:
            continue
        if isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            if not isinstance(fc, dict) or not isinstance(fc.get("name"), str):
                raise LLMBadResponse("Function call had no name")
            args = fc.get("args")
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise LLMBadResponse(f"Function call args weren't an object for {fc['name']}")
            sig = part.get("thoughtSignature")
            calls.append(
                ToolCall(
                    id=f"call_{i}",
                    name=fc["name"],
                    arguments=args,
                    provider_meta={"thoughtSignature": sig} if isinstance(sig, str) else None,
                )
            )
    usage = data.get("usageMetadata")
    if not isinstance(usage, dict):
        usage = {}
    return ChatResponse(
        text="".join(texts),
        tool_calls=calls,
        model=requested_model,
        finish_reason=(
            "tool_calls"
            if calls
            else _FINISH.get(finish, finish.lower())
            if isinstance(finish, str)
            else None
        ),
        usage=Usage(
            input_tokens=_count(usage.get("promptTokenCount"))
            + _count(usage.get("toolUsePromptTokenCount")),
            output_tokens=_count(usage.get("candidatesTokenCount"))
            + _count(usage.get("thoughtsTokenCount")),
        ),
    )


class GeminiProvider:
    api_style = "gemini"

    def __init__(
        self,
        client: httpx.Client,
        base_url: str = "https://generativelanguage.googleapis.com",
        api_key: str | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.client, self.base_url = client, _base(base_url)
        self.headers = {"Accept": "application/json", **(extra_headers or {})}
        if api_key:
            self.headers["x-goog-api-key"] = api_key

    def list_models(self) -> list[ProviderModel]:
        models: list[ProviderModel] = []
        token: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            data = get_json(
                self.client,
                join_url(self.base_url, "v1beta/models"),
                headers=self.headers,
                params=params,
            )
            items = data.get("models", [])
            if not isinstance(items, list):
                raise LLMBadResponse("Model list had an unexpected shape")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                methods = item.get("supportedGenerationMethods")
                if not isinstance(methods, list) or "generateContent" not in methods:
                    continue
                try:
                    models.append(_model(item))
                except (TypeError, ValueError, ValidationError):
                    continue
            nxt = data.get("nextPageToken")
            if not isinstance(nxt, str) or not nxt or nxt == token:
                break
            token = nxt
        return models

    def _body(self, req: ChatRequest, *, with_schema: bool) -> dict[str, Any]:
        system, contents = _convert(req.messages)
        config: dict[str, Any] = {"maxOutputTokens": req.max_tokens}
        if req.temperature is not None:
            config["temperature"] = req.temperature
        # Structured output and tool calling are separate calls; some models reject both together.
        if req.json_schema is not None and not req.tools:
            config["responseMimeType"] = "application/json"
            if with_schema:
                config["responseJsonSchema"] = req.json_schema
        body: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        if req.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parametersJsonSchema": t.parameters,
                        }
                        for t in req.tools
                    ]
                }
            ]
        return body

    def chat(self, req: ChatRequest) -> ChatResponse:
        url = join_url(self.base_url, f"v1beta/models/{_model_path(req.model)}:generateContent")
        try:
            data = post_json(
                self.client, url, headers=self.headers, body=self._body(req, with_schema=True)
            )
        except LLMHTTPError as exc:
            schema_rejected = exc.status == 400 and (
                "responseJsonSchema" in exc.body or "response_json_schema" in exc.body
            )
            if req.json_schema is None or req.tools or not schema_rejected:
                raise
            # Fall back to JSON mime type only; the client's validate-and-repair path takes over.
            data = post_json(
                self.client, url, headers=self.headers, body=self._body(req, with_schema=False)
            )
        try:
            return _parse_chat(data, req.model)
        except LLMRefused:
            raise
        except (
            AttributeError,
            TypeError,
            KeyError,
            IndexError,
            ValueError,
            ValidationError,
        ) as exc:
            raise LLMBadResponse(f"Reply had an unexpected shape ({type(exc).__name__})") from exc
