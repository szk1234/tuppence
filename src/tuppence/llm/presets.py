"""Connection presets (spec §4.1). Base URLs only — never model IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from tuppence.core.errors import InputError

ApiStyle = Literal["openai", "anthropic", "gemini"]
Kind = Literal["local", "cloud", "custom"]


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    api_style: ApiStyle
    base_url: str
    kind: Kind
    key_required: bool
    max_tokens_param: str = "max_tokens"
    docs_url: str | None = None


def _local(id_: str, label: str, url: str, docs_url: str | None = None) -> Preset:
    return Preset(id_, label, "openai", url, "local", False, docs_url=docs_url)


def _cloud(
    id_: str,
    label: str,
    url: str,
    api_style: ApiStyle = "openai",
    max_tokens_param: str = "max_tokens",
) -> Preset:
    return Preset(id_, label, api_style, url, "cloud", True, max_tokens_param)


PRESETS: dict[str, Preset] = {
    p.id: p
    for p in [
        _local("ollama", "Ollama", "http://127.0.0.1:11434/v1", "https://ollama.com"),
        _local("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1", "https://lmstudio.ai"),
        _local("llamacpp", "llama.cpp server", "http://127.0.0.1:8080/v1"),
        _local("vllm", "vLLM", "http://127.0.0.1:8000/v1"),
        _local("jan", "Jan", "http://127.0.0.1:1337/v1", "https://jan.ai"),
        _local("foundry_local", "Foundry Local", "http://127.0.0.1:5273/v1"),
        _cloud("anthropic", "Anthropic (Claude)", "https://api.anthropic.com", "anthropic"),
        _cloud(
            "openai",
            "OpenAI",
            "https://api.openai.com/v1",
            max_tokens_param="max_completion_tokens",
        ),
        _cloud("gemini", "Google Gemini", "https://generativelanguage.googleapis.com", "gemini"),
        _cloud("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
        _cloud("mistral", "Mistral", "https://api.mistral.ai/v1"),
        _cloud("groq", "Groq", "https://api.groq.com/openai/v1"),
        _cloud("together", "Together AI", "https://api.together.xyz/v1"),
        _cloud("xai", "xAI (Grok)", "https://api.x.ai/v1"),
        _cloud("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
        _cloud(
            "qwen",
            "Qwen (Alibaba Model Studio)",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ),
        _cloud("kimi", "Kimi (Moonshot)", "https://api.moonshot.ai/v1"),
        _cloud("glm", "GLM (Z.ai)", "https://api.z.ai/api/paas/v4"),
        Preset("custom", "Custom (OpenAI-compatible)", "openai", "", "custom", False),
    ]
}


def normalise_base_url(url: str, api_style: str) -> str:
    raw = url.strip()
    if len(raw) > 2048:
        raise InputError("That address is too long.")
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - raises ValueError for a bad port
        if parts.netloc and not parts.hostname:
            raise ValueError("no host")
        httpx.URL(raw)
    except (ValueError, httpx.InvalidURL):
        raise InputError(
            "That isn't a valid address. Use something like http://localhost:11434."
        ) from None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise InputError("The base URL must start with http:// or https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InputError("Put credentials in the API key or header fields, not the address.")
    if parts.query:
        raise InputError("Addresses with ?query parts aren't supported yet.")
    path = parts.path.rstrip("/")
    if api_style == "openai" and path == "":
        path = "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
