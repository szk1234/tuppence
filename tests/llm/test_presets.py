import pytest

from tuppence.core.errors import InputError
from tuppence.llm.presets import PRESETS, normalise_base_url

LOCAL = {"ollama", "lmstudio", "llamacpp", "vllm", "jan", "foundry_local"}


def test_all_spec_presets_present():
    expected = LOCAL | {
        "anthropic", "openai", "gemini", "openrouter", "mistral", "groq", "together",
        "xai", "deepseek", "qwen", "kimi", "glm", "custom",
    }  # fmt: skip
    assert expected <= set(PRESETS)
    assert PRESETS["anthropic"].api_style == "anthropic"
    assert PRESETS["gemini"].api_style == "gemini"
    assert PRESETS["openai"].max_tokens_param == "max_completion_tokens"
    assert all(p.kind == "local" for k, p in PRESETS.items() if k in LOCAL)
    assert all(p.key_required for p in PRESETS.values() if p.kind == "cloud")


@pytest.mark.parametrize(
    ("raw", "style", "expected"),
    [
        ("https://api.openai.com", "openai", "https://api.openai.com/v1"),
        ("https://api.openai.com/v1/ ", "openai", "https://api.openai.com/v1"),
        ("  http://127.0.0.1:11434/v1  ", "openai", "http://127.0.0.1:11434/v1"),
        ("https://api.z.ai/api/paas/v4", "openai", "https://api.z.ai/api/paas/v4"),
        ("https://api.anthropic.com/", "anthropic", "https://api.anthropic.com"),
    ],
)
def test_normalise_base_url(raw, style, expected):
    assert normalise_base_url(raw, style) == expected


def test_rejects_bad_scheme():
    with pytest.raises(InputError):
        normalise_base_url("ftp://x", "openai")
