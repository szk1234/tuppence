"""OpenAI-compatible Chat Completions — covers most providers and every local server."""

from __future__ import annotations

import json
from typing import Any

import httpx

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
    out: dict[str, Any] = {"role": m.role, "content": m.content}
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
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


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
        models: list[ProviderModel] = []
        for item in data.get("data", []):
            params = set(item.get("supported_parameters") or [])
            modalities = set((item.get("architecture") or {}).get("input_modalities") or [])
            pricing = item.get("pricing") or {}
            models.append(
                ProviderModel(
                    id=item["id"],
                    display_name=item.get("name"),
                    context_window=item.get("context_length"),
                    supports_tools=("tools" in params) if params else None,
                    supports_json_schema=bool(params & {"response_format", "structured_outputs"})
                    if params
                    else None,
                    supports_vision=("image" in modalities) if modalities else None,
                    price_in_usd_per_mtok=_per_mtok(pricing.get("prompt")),
                    price_out_usd_per_mtok=_per_mtok(pricing.get("completion")),
                )
            )
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
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponse("Response had no choices") from exc
        calls: list[ToolCall] = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            fn = raw.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise LLMBadResponse(
                    f"Tool call arguments weren't valid JSON for {fn.get('name')}"
                ) from exc
            calls.append(
                ToolCall(id=raw.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=args)
            )
        usage = data.get("usage") or {}
        return ChatResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            model=data.get("model") or req.model,
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0) or 0,
                output_tokens=usage.get("completion_tokens", 0) or 0,
            ),
            finish_reason=choice.get("finish_reason"),
        )
