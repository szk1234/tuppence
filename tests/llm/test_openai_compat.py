import json

import httpx
import pytest

from tuppence.llm.providers.openai_compat import OpenAICompatProvider
from tuppence.llm.types import (
    ChatRequest,
    LLMBadResponse,
    LLMHTTPError,
    LLMTimeout,
    Message,
    ToolCall,
    ToolSpec,
)


def make(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(client, "https://api.example.com/v1", "sk-test", **kw)


def test_chat_request_shape_and_parse():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "m1",
                "choices": [
                    {
                        "message": {
                            "content": "hi",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": '{"q": "tesco"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    p = make(handler, max_tokens_param="max_completion_tokens")
    req = ChatRequest(
        model="m1",
        messages=[
            Message(role="system", content="sys"),
            Message(role="user", content="hello"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c0", name="lookup", arguments={"q": "a"})],
            ),
            Message(role="tool", content='{"r": 1}', tool_call_id="c0", name="lookup"),
        ],
        tools=[
            ToolSpec(
                name="lookup",
                description="Look up",
                parameters={"type": "object", "properties": {}},
            )
        ],
        json_schema={
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        },
        max_tokens=100,
    )
    r = p.chat(req)
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["max_completion_tokens"] == 100 and "max_tokens" not in body
    assert body["messages"][2]["tool_calls"][0]["function"] == {
        "name": "lookup",
        "arguments": '{"q": "a"}',
    }
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c0", "content": '{"r": 1}'}
    assert body["tools"][0]["type"] == "function"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert r.text == "hi" and r.tool_calls == [
        ToolCall(id="c1", name="lookup", arguments={"q": "tesco"})
    ]
    assert (r.usage.input_tokens, r.usage.output_tokens, r.finish_reason) == (12, 3, "tool_calls")


def test_keyless_local_server_sends_no_auth_header():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    OpenAICompatProvider(client, "http://127.0.0.1:11434/v1", None).chat(
        ChatRequest(model="m", messages=[Message(role="user", content="x")])
    )
    assert seen["auth"] is None


def test_list_models_with_openrouter_extras():
    def handler(req):
        assert str(req.url) == "https://api.example.com/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "plain-model"},
                    {
                        "id": "vendor/rich",
                        "name": "Rich",
                        "context_length": 128000,
                        "pricing": {"prompt": "0.000002", "completion": "0.000008"},
                        "supported_parameters": ["tools", "response_format", "structured_outputs"],
                        "architecture": {"input_modalities": ["text", "image"]},
                    },
                ]
            },
        )

    models = make(handler).list_models()
    assert models[0].id == "plain-model" and models[0].context_window is None
    rich = models[1]
    assert (rich.context_window, rich.price_in_usd_per_mtok, rich.price_out_usd_per_mtok) == (
        128000,
        2.0,
        8.0,
    )
    assert rich.supports_tools and rich.supports_json_schema and rich.supports_vision


def test_errors_are_mapped():
    p = make(
        lambda req: httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "slow down"})
    )
    with pytest.raises(LLMHTTPError) as exc:
        p.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert exc.value.status == 429 and exc.value.retryable and exc.value.retry_after == 2.0

    def timeout(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(LLMTimeout):
        make(timeout).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))

    with pytest.raises(LLMBadResponse):
        make(lambda req: httpx.Response(200, text="<html>")).chat(
            ChatRequest(model="m", messages=[Message(role="user", content="x")])
        )

    bad_args = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{not json"},
                        }
                    ],
                }
            }
        ]
    }
    with pytest.raises(LLMBadResponse):
        make(lambda req: httpx.Response(200, json=bad_args)).chat(
            ChatRequest(model="m", messages=[Message(role="user", content="x")])
        )

    p400 = make(lambda req: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(LLMHTTPError) as exc400:
        p400.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert not exc400.value.retryable


def test_error_body_never_leaks_credentials():
    p = make(
        lambda req: httpx.Response(
            401, text="bad key. Authorization: Bearer sk-supersecretvalue123"
        )
    )
    with pytest.raises(LLMHTTPError) as exc:
        p.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert "sk-supersecretvalue123" not in exc.value.body
    assert "sk-supersecretvalue123" not in str(exc.value)


def test_local_only_block_propagates():
    from tuppence.net.client import LocalOnlyBlocked

    def blocked(req):
        raise LocalOnlyBlocked("api.example.com")

    with pytest.raises(LocalOnlyBlocked):
        make(blocked).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
