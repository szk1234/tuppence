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


@pytest.mark.parametrize(
    "raw",
    [
        "http://xn--/",  # an A-label with nothing after the prefix
        "http://xn--zz-/v1",  # an A-label ending in a hyphen
        "http://api.xn--/v1",  # malformed, but not the leading label
        "http://xn--ls8h.la/",  # decodes to a code point IDNA 2008 disallows
        "http://xn--bcher-kva.my_box/",  # httpx can't decode the host as a whole
        "http://a..b/",  # an empty label: the resolver can't encode it
        "http://" + "a" * 64 + ".example/",  # a label over 63 characters
    ],
)
def test_malformed_host_labels_are_input_errors(raw):
    with pytest.raises(InputError, match="That address isn't valid."):
        normalise_base_url(raw, "openai")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://bücher.example:8080", "http://bücher.example:8080/v1"),
        ("http://xn--bcher-kva.example", "http://xn--bcher-kva.example/v1"),
        ("http://my_server:11434/v1", "http://my_server:11434/v1"),
        ("http://localhost.:11434/v1", "http://localhost.:11434/v1"),
        ("http://[::1]:8080/v1", "http://[::1]:8080/v1"),
    ],
)
def test_well_formed_hosts_are_kept(raw, expected):
    assert normalise_base_url(raw, "openai") == expected
