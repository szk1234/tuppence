import json

import httpx
import pytest

from tuppence.llm.providers.gemini import GeminiProvider
from tuppence.llm.types import (
    ChatRequest,
    LLMBadResponse,
    LLMHTTPError,
    LLMRefused,
    Message,
    ToolCall,
    ToolSpec,
)


def make(handler):
    return GeminiProvider(httpx.Client(transport=httpx.MockTransport(handler)), api_key="g-key")


OK = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"text": "thinking...", "thought": True}, {"text": "Hello"}],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2, "thoughtsTokenCount": 5},
}


def test_request_shape_and_parse():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=OK)

    r = make(handler).chat(
        ChatRequest(
            model="gemini-x",
            messages=[
                Message(role="system", content="sys"),
                Message(role="user", content="hi"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="c1", name="lookup", arguments={"q": 1})],
                ),
                Message(role="tool", content='{"r": 2}', tool_call_id="c1", name="lookup"),
            ],
            tools=[
                ToolSpec(
                    name="lookup", description="d", parameters={"type": "object", "properties": {}}
                )
            ],
            json_schema={"type": "object", "properties": {}},
            max_tokens=50,
        )
    )
    assert (
        seen["url"]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    )
    assert seen["key"] == "g-key"
    b = seen["body"]
    assert b["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert b["contents"][0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert b["contents"][1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "lookup", "args": {"q": 1}}}],
    }
    assert b["contents"][2] == {
        "role": "user",
        "parts": [{"functionResponse": {"name": "lookup", "response": {"result": '{"r": 2}'}}}],
    }
    assert b["generationConfig"]["responseMimeType"] == "application/json"
    assert b["generationConfig"]["responseJsonSchema"] == {"type": "object", "properties": {}}
    assert b["generationConfig"]["maxOutputTokens"] == 50
    assert b["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == {
        "type": "object",
        "properties": {},
    }
    assert r.text == "Hello" and r.usage.input_tokens == 7 and r.usage.output_tokens == 7


def test_schema_rejected_retries_without_schema():
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        if "responseJsonSchema" in body["generationConfig"]:
            return httpx.Response(
                400, json={"error": {"message": 'Unknown name "responseJsonSchema"'}}
            )
        return httpx.Response(200, json=OK)

    r = make(handler).chat(
        ChatRequest(
            model="g", messages=[Message(role="user", content="x")], json_schema={"type": "object"}
        )
    )
    assert len(calls) == 2 and r.text == "Hello"
    assert calls[1]["generationConfig"]["responseMimeType"] == "application/json"


def test_function_call_and_safety():
    fc = {
        "candidates": [
            {
                "content": {"parts": [{"functionCall": {"name": "spend", "args": {"m": "x"}}}]},
                "finishReason": "STOP",
            }
        ]
    }
    r = make(lambda req: httpx.Response(200, json=fc)).chat(
        ChatRequest(model="g", messages=[Message(role="user", content="x")])
    )
    assert r.tool_calls[0].name == "spend" and r.tool_calls[0].arguments == {"m": "x"}
    blocked = {"candidates": [{"finishReason": "SAFETY"}]}
    with pytest.raises(LLMRefused):
        make(lambda req: httpx.Response(200, json=blocked)).chat(
            ChatRequest(model="g", messages=[Message(role="user", content="x")])
        )
    pf = {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
    with pytest.raises(LLMRefused):
        make(lambda req: httpx.Response(200, json=pf)).chat(
            ChatRequest(model="g", messages=[Message(role="user", content="x")])
        )


def test_list_models():
    pages = {
        None: {
            "models": [
                {
                    "name": "models/gemini-a",
                    "displayName": "A",
                    "inputTokenLimit": 1048576,
                    "outputTokenLimit": 65536,
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                },
                {"name": "models/embed-x", "supportedGenerationMethods": ["embedContent"]},
            ],
            "nextPageToken": "p2",
        },
        "p2": {
            "models": [
                {
                    "name": "models/gemini-b",
                    "inputTokenLimit": 32768,
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        },
    }
    models = make(
        lambda req: httpx.Response(200, json=pages[req.url.params.get("pageToken")])
    ).list_models()
    assert [m.id for m in models] == ["gemini-a", "gemini-b"]
    assert models[0].context_window == 1048576 and models[0].max_output_tokens == 65536


def _chat(body):
    return make(lambda req: httpx.Response(200, json=body)).chat(
        ChatRequest(model="g", messages=[Message(role="user", content="x")])
    )


def _cand(parts, **extra):
    return {"candidates": [{"content": {"parts": parts}, **extra}]}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"candidates": None},
        {"candidates": []},
        {"candidates": ["x"]},
        {"candidates": [{"content": "x"}]},
        {"candidates": [{"content": {"parts": "x"}}]},
        _cand(["x"]),
        _cand([{"functionCall": "x"}]),
        _cand([{"functionCall": {"args": {}}}]),
        _cand([{"functionCall": {"name": "f", "args": "nope"}}]),
        _cand([{"functionCall": {"name": 7, "args": {}}}]),
    ],
)
def test_malformed_200_is_bad_response(body):
    with pytest.raises(LLMBadResponse):
        _chat(body)


def test_non_object_json_is_bad_response():
    with pytest.raises(LLMBadResponse):
        _chat(["x"])


def test_odd_but_acceptable_replies():
    r = _chat(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": 5}, {"text": "ok"}, {"inlineData": {}}]},
                    "finishReason": 3,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": "1",
                "candidatesTokenCount": -2,
                "thoughtsTokenCount": 4,
            },
        }
    )
    assert r.text == "ok" and r.finish_reason is None and r.model == "g"
    assert (r.usage.input_tokens, r.usage.output_tokens) == (0, 4)
    empty = _chat({"candidates": [{"finishReason": "MAX_TOKENS"}]})
    assert empty.text == "" and empty.finish_reason == "MAX_TOKENS"
    nocalls = _chat(_cand([{"functionCall": {"name": "f"}}]))
    assert nocalls.tool_calls == [ToolCall(id="call_0", name="f", arguments={})]


@pytest.mark.parametrize(
    "reason", sorted(["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"])
)
def test_every_blocked_finish_reason_refuses(reason):
    with pytest.raises(LLMRefused):
        _chat({"candidates": [{"finishReason": reason}]})


def test_consecutive_tool_results_merge():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=OK)

    make(handler).chat(
        ChatRequest(
            model="g",
            messages=[
                Message(role="user", content="q"),
                Message(role="tool", content="a", name="f"),
                Message(role="tool", content="b", name="g"),
            ],
        )
    )
    assert len(seen["body"]["contents"][1]["parts"]) == 2


def test_key_is_in_header_never_in_url():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"models": []})

    make(handler).list_models()
    assert "g-key" not in seen["url"] and "key=" not in seen["url"]


def test_other_400_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "bad contents"}})

    with pytest.raises(LLMHTTPError) as exc:
        make(handler).chat(
            ChatRequest(
                model="g",
                messages=[Message(role="user", content="x")],
                json_schema={"type": "object"},
            )
        )
    assert exc.value.status == 400 and len(calls) == 1


def test_list_models_tolerates_junk():
    body = {
        "models": [
            {"displayName": "no name", "supportedGenerationMethods": ["generateContent"]},
            "x",
            {"name": "models/a", "supportedGenerationMethods": "generateContent"},
            {
                "name": "models/ok",
                "displayName": 3,
                "inputTokenLimit": "big",
                "outputTokenLimit": -1,
                "supportedGenerationMethods": ["generateContent"],
            },
        ],
        "nextPageToken": "same",
    }
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=body)

    models = make(handler).list_models()
    assert [m.id for m in models] == ["ok", "ok"] and len(calls) == 2
    assert models[0].context_window is None and models[0].max_output_tokens is None


def test_list_models_bad_shape():
    with pytest.raises(LLMBadResponse):
        make(lambda req: httpx.Response(200, json={"models": "x"})).list_models()


@pytest.mark.parametrize(
    "echo",
    [
        '{"headers": {"x-goog-api-key": "AIzaSyAbcdefghijklmnopqrstuvwxyz0123456"}}',
        "bad request to /v1beta/models?key=AIzaSyAbcdefghijklmnopqrstuvwxyz0123456&pageSize=1",
        "API key not valid. x-goog-api-key: qqqqqqqqrrrrrrrr",
        "url: https://x/v1beta/models/g:generateContent?key=wwwwwwwwxxxxxxxx",
    ],
)
def test_error_body_never_leaks_key_echoes(echo):
    with pytest.raises(LLMHTTPError) as exc:
        make(lambda req: httpx.Response(403, text=echo)).list_models()
    for secret in ("AIzaSyAbcdefghijklmnop", "qqqqqqqqrrrrrrrr", "wwwwwwwwxxxxxxxx"):
        assert secret not in exc.value.body and secret not in str(exc.value)
    assert "[redacted]" in exc.value.body
