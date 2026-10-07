"""OpenAI-compatible Chat Completions — covers most providers and every local server."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
from pydantic import ValidationError

from tuppence.llm.providers.base import get_json, join_url, post_json
from tuppence.llm.types import (
    ChatRequest,
    ChatResponse,
    LLMBadResponse,
    Message,
    ProviderModel,
    ToolCall,
    Usage,
)


def _message(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    content: Any = m.content
    if m.images:
        content = [
            {"type": "text", "text": m.content},
            *(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{i.media_type};base64,{i.data_b64}"},
                }
                for i in m.images
            ),
        ]
    out: dict[str, Any] = {"role": m.role, "content": content}
    if m.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in m.tool_calls
        ]
    return out


def _per_mtok(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price < 0:
        return None
    return round(price * 1_000_000, 6)


def _model(item: dict[str, Any]) -> ProviderModel:
    params = item.get("supported_parameters")
    params = {p for p in params if isinstance(p, str)} if isinstance(params, list) else set()
    arch = item.get("architecture")
    modalities = arch.get("input_modalities") if isinstance(arch, dict) else None
    modalities = (
        {m for m in modalities if isinstance(m, str)} if isinstance(modalities, list) else set()
    )
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    ctx = item.get("context_length")
    return ProviderModel(
        id=item["id"],
        display_name=item.get("name") if isinstance(item.get("name"), str) else None,
        context_window=ctx if isinstance(ctx, int) and not isinstance(ctx, bool) else None,
        supports_tools=("tools" in params) if params else None,
        supports_json_schema=bool(params & {"response_format", "structured_outputs"})
        if params
        else None,
        supports_vision=("image" in modalities) if modalities else None,
        price_in_usd_per_mtok=_per_mtok(pricing.get("prompt")),
        price_out_usd_per_mtok=_per_mtok(pricing.get("completion")),
    )


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    raise TypeError("content")


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _parse_chat(data: dict[str, Any], requested_model: str) -> ChatResponse:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMBadResponse("Response had no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise LLMBadResponse("Response message wasn't an object")
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise LLMBadResponse("Response tool calls weren't a list")
    calls: list[ToolCall] = []
    for i, raw in enumerate(raw_calls):
        fn = raw.get("function") if isinstance(raw, dict) else None
        if not isinstance(fn, dict):
            raise LLMBadResponse("Tool call had no function")
        args: Any = fn.get("arguments")
        if args is None or args == "":
            args = {}
        elif isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError as exc:
                raise LLMBadResponse(
                    f"Tool call arguments weren't valid JSON for {fn.get('name')}"
                ) from exc
        if not isinstance(args, dict):
            raise LLMBadResponse(f"Tool call arguments weren't an object for {fn.get('name')}")
        calls.append(
            ToolCall(
                id=raw.get("id") or f"call_{i}", name=str(fn.get("name") or ""), arguments=args
            )
        )
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    finish = choice.get("finish_reason")
    model = data.get("model")
    return ChatResponse(
        text=_text(message.get("content")),
        tool_calls=calls,
        model=model if isinstance(model, str) and model else requested_model,
        usage=Usage(
            input_tokens=_count(usage.get("prompt_tokens")),
            output_tokens=_count(usage.get("completion_tokens")),
        ),
        finish_reason=finish if isinstance(finish, str) else None,
    )


class OpenAICompatProvider:
    api_style = "openai"

    def __init__(
        self,
        client: httpx.Client,
        base_url: str,
        api_key: str | None,
        *,
        extra_headers: dict[str, str] | None = None,
        max_tokens_param: str = "max_tokens",
    ) -> None:
        self.client, self.base_url = client, base_url
        self.headers = {"Accept": "application/json", **(extra_headers or {})}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self.max_tokens_param = max_tokens_param

    def list_models(self) -> list[ProviderModel]:
        data = get_json(self.client, join_url(self.base_url, "models"), headers=self.headers)
        items = data.get("data", [])
        if not isinstance(items, list):
            raise LLMBadResponse("Model list had an unexpected shape")
        models: list[ProviderModel] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            try:
                models.append(_model(item))
            except (TypeError, ValueError, ValidationError):
                continue
        return models

    def chat(self, req: ChatRequest) -> ChatResponse:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [_message(m) for m in req.messages],
            self.max_tokens_param: req.max_tokens,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in req.tools
            ]
        if req.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": req.schema_name, "schema": req.json_schema, "strict": True},
            }
        data = post_json(
            self.client,
            join_url(self.base_url, "chat/completions"),
            headers=self.headers,
            body=body,
        )
        try:
            return _parse_chat(data, req.model)
        except (
            AttributeError,
            TypeError,
            KeyError,
            IndexError,
            ValueError,
            ValidationError,
        ) as exc:
            raise LLMBadResponse(f"Reply had an unexpected shape ({type(exc).__name__})") from exc
