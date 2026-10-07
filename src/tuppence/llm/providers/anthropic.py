"""Anthropic Messages API over raw HTTP (spec §4.1, D8)."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from tuppence.llm.providers.base import get_json, join_url, post_json
from tuppence.llm.types import (
    ChatRequest,
    ChatResponse,
    LLMBadResponse,
    LLMRefused,
    Message,
    ProviderModel,
    ToolCall,
    Usage,
)

ANTHROPIC_VERSION = "2023-06-01"


def _convert(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
            continue
        if m.role == "tool":
            pending_results.append(
                {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            )
            continue
        flush()
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = (
                [{"type": "text", "text": m.content}] if m.content else []
            )
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    flush()
    if out and out[-1]["role"] == "assistant":
        raise ValueError("Anthropic models don't accept a trailing assistant message (prefill)")
    return "\n\n".join(p for p in system_parts if p), out


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _limit(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _supported(caps: Any, name: str) -> bool | None:
    entry = caps.get(name) if isinstance(caps, dict) else None
    flag = entry.get("supported") if isinstance(entry, dict) else None
    return flag if isinstance(flag, bool) else None


def _model(item: dict[str, Any]) -> ProviderModel:
    caps = item.get("capabilities")
    name = item.get("display_name")
    return ProviderModel(
        id=item["id"],
        display_name=name if isinstance(name, str) else None,
        context_window=_limit(item.get("max_input_tokens")),
        max_output_tokens=_limit(item.get("max_tokens")),
        supports_tools=True,
        supports_json_schema=_supported(caps, "structured_outputs"),
        supports_vision=_supported(caps, "image_input"),
    )


def _parse_chat(data: dict[str, Any], requested_model: str) -> ChatResponse:
    if data.get("stop_reason") == "refusal":
        details = data.get("stop_details")
        category = details.get("category") if isinstance(details, dict) else None
        label = category if isinstance(category, str) else "policy"
        raise LLMRefused(f"The model declined this request ({label})")
    content = data.get("content")
    if not isinstance(content, list):
        raise LLMBadResponse("Anthropic response had no content")
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in content:
        if not isinstance(block, dict):
            raise LLMBadResponse("Response content block wasn't an object")
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
        elif kind == "tool_use":
            args = block.get("input")
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise LLMBadResponse("Tool call input wasn't an object")
            if not isinstance(block.get("id"), str) or not isinstance(block.get("name"), str):
                raise LLMBadResponse("Tool call had no id or name")
            calls.append(ToolCall(id=block["id"], name=block["name"], arguments=args))
        # thinking / redacted_thinking / anything else: skipped
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    model = data.get("model")
    stop = data.get("stop_reason")
    return ChatResponse(
        text="".join(texts),
        tool_calls=calls,
        model=model if isinstance(model, str) and model else requested_model,
        usage=Usage(
            input_tokens=_count(usage.get("input_tokens")),
            output_tokens=_count(usage.get("output_tokens")),
        ),
        finish_reason=stop if isinstance(stop, str) else None,
    )


class AnthropicProvider:
    api_style = "anthropic"

    def __init__(
        self,
        client: httpx.Client,
        base_url: str = "https://api.anthropic.com",
        api_key: str | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.client, self.base_url = client, base_url
        self.headers = {
            "anthropic-version": ANTHROPIC_VERSION,
            "Accept": "application/json",
            **(extra_headers or {}),
        }
        if api_key:
            self.headers["x-api-key"] = api_key

    def list_models(self) -> list[ProviderModel]:
        models: list[ProviderModel] = []
        after: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"limit": 1000}
            if after:
                params["after_id"] = after
            data = get_json(
                self.client,
                join_url(self.base_url, "v1/models"),
                headers=self.headers,
                params=params,
            )
            items = data.get("data", [])
            if not isinstance(items, list):
                raise LLMBadResponse("Model list had an unexpected shape")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                try:
                    models.append(_model(item))
                except (TypeError, ValueError, ValidationError):
                    continue
            last = data.get("last_id")
            if not data.get("has_more") or not isinstance(last, str) or last == after:
                break
            after = last
        return models

    def chat(self, req: ChatRequest) -> ChatResponse:
        system, messages = _convert(req.messages)
        # No temperature (rejected by newer models) and no forced tool_choice.
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if req.tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in req.tools
            ]
        if req.json_schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": req.json_schema}}
        data = post_json(
            self.client, join_url(self.base_url, "v1/messages"), headers=self.headers, body=body
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
