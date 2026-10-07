import json

import httpx
import pytest

from tuppence.llm.providers.anthropic import AnthropicProvider
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
    return AnthropicProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "https://api.anthropic.com",
        "sk-ant-test",
    )


def test_request_shape():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-x",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": '{"a": "b"}'},
                ],
                "usage": {"input_tokens": 20, "output_tokens": 5},
            },
        )

    r = make(handler).chat(
        ChatRequest(
            model="claude-x",
            messages=[
                Message(role="system", content="Be brief."),
                Message(role="user", content="q1"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="tu1", name="lookup", arguments={"q": "x"})],
                ),
                Message(role="tool", content="result-1", tool_call_id="tu1", name="lookup"),
                Message(role="system", content="Second system note."),
            ],
            tools=[
                ToolSpec(
                    name="lookup", description="d", parameters={"type": "object", "properties": {}}
                )
            ],
            json_schema={
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
                "additionalProperties": False,
            },
            max_tokens=300,
            temperature=0.0,
        )
    )
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert (
        seen["headers"]["x-api-key"] == "sk-ant-test"
        and seen["headers"]["anthropic-version"] == "2023-06-01"
    )
    body = seen["body"]
    assert body["system"] == "Be brief.\n\nSecond system note."
    assert "temperature" not in body and "tool_choice" not in body
    assert body["max_tokens"] == 300
    assert body["messages"][0] == {"role": "user", "content": "q1"}
    assert body["messages"][1]["content"] == [
        {"type": "tool_use", "id": "tu1", "name": "lookup", "input": {"q": "x"}}
    ]
    assert body["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "result-1"}],
    }
    assert body["tools"] == [
        {"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {}}}
    ]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert r.text == '{"a": "b"}' and r.usage.input_tokens == 20 and r.finish_reason == "stop"


def test_tool_use_response_and_consecutive_tool_results_merge():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-x",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "t9", "name": "spend", "input": {"m": "2026-09"}},
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    r = make(handler).chat(
        ChatRequest(
            model="claude-x",
            messages=[
                Message(role="user", content="q"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="a", name="f", arguments={}),
                        ToolCall(id="b", name="g", arguments={}),
                    ],
                ),
                Message(role="tool", content="ra", tool_call_id="a", name="f"),
                Message(role="tool", content="rb", tool_call_id="b", name="g"),
            ],
        )
    )
    assert len(seen["body"]["messages"][2]["content"]) == 2  # both results in ONE user message
    assert (
        r.tool_calls == [ToolCall(id="t9", name="spend", arguments={"m": "2026-09"})]
        and r.text == "Let me check."
    )


def test_refusal_raises():
    with pytest.raises(LLMRefused):
        make(
            lambda req: httpx.Response(
                200,
                json={
                    "model": "m",
                    "stop_reason": "refusal",
                    "content": [],
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                    "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"},
                },
            )
        ).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))


def test_trailing_assistant_message_rejected():
    with pytest.raises(ValueError):
        make(lambda req: httpx.Response(200, json={})).chat(
            ChatRequest(
                model="m",
                messages=[
                    Message(role="user", content="x"),
                    Message(role="assistant", content="prefill"),
                ],
            )
        )


def test_list_models_paginates_and_reads_capabilities():
    pages = {
        None: {
            "data": [
                {
                    "id": "claude-a",
                    "display_name": "A",
                    "max_input_tokens": 1000000,
                    "max_tokens": 128000,
                    "capabilities": {
                        "structured_outputs": {"supported": True},
                        "image_input": {"supported": True},
                    },
                }
            ],
            "has_more": True,
            "last_id": "claude-a",
        },
        "claude-a": {
            "data": [
                {
                    "id": "claude-b",
                    "display_name": "B",
                    "max_input_tokens": 200000,
                    "max_tokens": 64000,
                    "capabilities": {
                        "structured_outputs": {"supported": False},
                        "image_input": {"supported": False},
                    },
                }
            ],
            "has_more": False,
            "last_id": "claude-b",
        },
    }

    def handler(req):
        return httpx.Response(200, json=pages[req.url.params.get("after_id")])

    models = make(handler).list_models()
    assert [m.id for m in models] == ["claude-a", "claude-b"]
    assert (
        models[0].context_window == 1000000
        and models[0].supports_json_schema
        and models[0].supports_tools
    )
    assert models[1].supports_json_schema is False


def _chat(body):
    return make(lambda req: httpx.Response(200, json=body)).chat(
        ChatRequest(model="m", messages=[Message(role="user", content="x")])
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"content": None},
        {"content": "text"},
        {"content": ["x"]},
        {"content": [{"type": "tool_use", "id": "a", "name": "f", "input": "nope"}]},
        {"content": [{"type": "tool_use", "name": "f", "input": {}}]},
        {"content": [{"type": "tool_use", "id": 3, "name": "f", "input": {}}]},
        {"content": [{"type": "tool_use", "id": "a", "name": ["f"], "input": {}}]},
    ],
)
def test_malformed_200_is_bad_response(body):
    with pytest.raises(LLMBadResponse):
        _chat(body)


def test_non_object_json_is_bad_response():
    with pytest.raises(LLMBadResponse):
        make(lambda req: httpx.Response(200, json=["x"])).chat(
            ChatRequest(model="m", messages=[Message(role="user", content="x")])
        )


def test_odd_but_acceptable_replies():
    r = _chat(
        {
            "model": 5,
            "stop_reason": 7,
            "content": [
                {"type": "redacted_thinking", "data": "x"},
                {"type": "text", "text": 5},
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "a", "name": "f", "input": None},
            ],
            "usage": {"input_tokens": "9", "output_tokens": -3},
        }
    )
    assert r.text == "ok" and r.model == "m" and r.finish_reason is None
    assert r.tool_calls == [ToolCall(id="a", name="f", arguments={})]
    assert (r.usage.input_tokens, r.usage.output_tokens) == (0, 0)


def test_refusal_with_odd_details_still_refuses():
    with pytest.raises(LLMRefused):
        _chat({"stop_reason": "refusal", "stop_details": "weird"})


def test_list_models_tolerates_junk():
    body = {
        "data": [
            {"display_name": "no id"},
            "x",
            {"id": "ok", "capabilities": "none", "max_input_tokens": "big", "max_tokens": -5},
            {
                "id": "good",
                "display_name": 3,
                "capabilities": {"image_input": {"supported": "yes"}},
            },
        ],
        "has_more": True,
        "last_id": None,
    }
    models = make(lambda req: httpx.Response(200, json=body)).list_models()
    assert [m.id for m in models] == ["ok", "good"]
    assert models[0].context_window is None and models[0].max_output_tokens is None
    assert models[0].supports_json_schema is None and models[1].supports_vision is None


def test_list_models_bad_shape():
    with pytest.raises(LLMBadResponse):
        make(lambda req: httpx.Response(200, json={"data": {"id": "x"}})).list_models()


def test_list_models_stops_on_repeated_cursor():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"data": [], "has_more": True, "last_id": "same"})

    make(handler).list_models()
    assert len(calls) == 2


def test_error_body_never_leaks_key_echoes():
    def handler(req):
        return httpx.Response(
            401,
            text="invalid x-api-key: sk-ant-api03-abcdefghijklmnop1234 "
            '{"headers": {"x-api-key": "sk-ant-zzzzzzzzzzzzzzzz"}}',
        )

    with pytest.raises(LLMHTTPError) as exc:
        make(handler).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert exc.value.status == 401
    assert "abcdefghijklmnop1234" not in str(exc.value) and "zzzzzzzzzzzzzzzz" not in str(exc.value)
    assert "[redacted]" in exc.value.body


def test_retry_after_and_status_mapping():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "7"}, text="slow down")

    with pytest.raises(LLMHTTPError) as exc:
        make(handler).chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
    assert exc.value.retry_after == 7 and exc.value.retryable


def _url(base):
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": []})

    AnthropicProvider(httpx.Client(transport=httpx.MockTransport(handler)), base, "k").list_models()
    return seen["url"].split("?")[0]


@pytest.mark.parametrize(
    "base",
    [
        "https://api.anthropic.com",
        "https://api.anthropic.com/",
        "https://api.anthropic.com/v1",
        "https://api.anthropic.com/v1/",
        "  https://api.anthropic.com/v1/  ",
    ],
)
def test_base_url_variants(base):
    assert _url(base) == "https://api.anthropic.com/v1/models"


def test_proxy_path_prefix_kept():
    assert _url("http://localhost:8080/anthropic/v1") == "http://localhost:8080/anthropic/v1/models"


def test_cached_tokens_count_as_input():
    r = _chat(
        {
            "content": [],
            "usage": {
                "input_tokens": 5,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 20,
                "output_tokens": 3,
            },
        }
    )
    assert (r.usage.input_tokens, r.usage.output_tokens) == (125, 3)


@pytest.mark.parametrize(
    ("stop", "expected"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("pause_turn", "stop"),
        ("max_tokens", "length"),
        ("tool_use", "tool_calls"),
        ("something_new", "something_new"),
    ],
)
def test_finish_reason_normalised(stop, expected):
    assert _chat({"content": [], "stop_reason": stop}).finish_reason == expected


def test_529_is_retryable():
    with pytest.raises(LLMHTTPError) as exc:
        make(lambda req: httpx.Response(529, text="overloaded")).chat(
            ChatRequest(model="m", messages=[Message(role="user", content="x")])
        )
    assert exc.value.status == 529 and exc.value.retryable


def test_empty_messages_dropped_but_tool_messages_kept():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": []})

    make(handler).chat(
        ChatRequest(
            model="m",
            messages=[
                Message(role="user", content="q"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id="a", name="f", arguments={})],
                ),
                Message(role="tool", content="", tool_call_id="a", name="f"),
                Message(role="user", content=""),
            ],
        )
    )
    msgs = seen["body"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == [{"type": "tool_use", "id": "a", "name": "f", "input": {}}]
    assert msgs[2]["content"][0]["content"] == ""
    with pytest.raises(ValueError):
        make(handler).chat(ChatRequest(model="m", messages=[Message(role="user", content="")]))
