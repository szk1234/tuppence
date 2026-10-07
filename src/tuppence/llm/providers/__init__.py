from __future__ import annotations

import httpx

from tuppence.llm.providers.anthropic import AnthropicProvider
from tuppence.llm.providers.gemini import GeminiProvider
from tuppence.llm.providers.openai_compat import OpenAICompatProvider
from tuppence.llm.types import Provider


def build_provider(
    api_style: str,
    client: httpx.Client,
    base_url: str,
    api_key: str | None,
    headers: dict[str, str] | None = None,
    max_tokens_param: str = "max_tokens",
) -> Provider:
    if api_style == "anthropic":
        return AnthropicProvider(client, base_url, api_key, extra_headers=headers)
    if api_style == "gemini":
        return GeminiProvider(client, base_url, api_key, extra_headers=headers)
    return OpenAICompatProvider(
        client, base_url, api_key, extra_headers=headers, max_tokens_param=max_tokens_param
    )
