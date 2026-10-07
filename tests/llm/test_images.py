import base64

import pytest

from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.vision import VisionOCR, vision_factory
from tuppence.llm.budget import RunBudget, estimate_tokens
from tuppence.llm.providers import anthropic, gemini, openai_compat
from tuppence.llm.types import AllModelsFailed, ImageData, Message

PNG = b"\x89PNG\r\n\x1a\nfake"
IMAGE = ImageData(media_type="image/png", data_b64=base64.b64encode(PNG).decode())
WITH_IMAGE = Message(role="user", content="Transcribe this page.", images=[IMAGE])


def test_openai_style_sends_a_data_url():
    body = openai_compat._message(WITH_IMAGE)
    assert body["content"][0] == {"type": "text", "text": "Transcribe this page."}
    assert body["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert openai_compat._message(Message(role="user", content="hi"))["content"] == "hi"


def test_anthropic_sends_image_blocks_then_text():
    _, out = anthropic._convert([WITH_IMAGE])
    blocks = out[0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[-1] == {"type": "text", "text": "Transcribe this page."}


def test_gemini_sends_inline_data():
    _, contents = gemini._convert([WITH_IMAGE])
    parts = contents[0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png" and parts[-1] == {
        "text": "Transcribe this page."
    }


def test_images_count_towards_the_context_estimate():
    assert estimate_tokens([WITH_IMAGE]) >= 1600


def vision_ready(services, *, cloud=False):
    if cloud:
        conn = services.connections.create(
            "openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1"
        )
    else:
        conn = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
    services.connections.test(conn.id)
    if cloud:
        services.connections.acknowledge_notice(
            conn.id, expected_version=services.connections.get(conn.id).version
        )
    with services.db.transaction() as c:
        c.execute("UPDATE llm_model SET supports_vision = 1")
    services.settings.set(
        "llm.simple_model", {"connection_id": conn.id, "model_id": "m-small"}, expected_version=0
    )


def test_vision_ocr_sends_the_page_and_returns_lines(env):
    services, scripted = env
    vision_ready(services)
    scripted.replies = [{"content": '{"lines": ["Mon 5 Oct   Little Cafe   -£3.40", "  "]}'}]
    run = RunBudget(max_calls=5, max_tokens=100_000, max_gbp=1, max_seconds=60)
    reader = VisionOCR(services.llm, run, prompt=load_prompt("vision_ocr"))
    assert reader.transcribe(PNG, "image/png") == ["Mon 5 Oct   Little Cafe   -£3.40"]
    sent = scripted.requests[-1]["messages"][-1]["content"]
    assert sent[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert run.calls == 1


def test_no_vision_model_means_on_device_ocr(env):
    services, _ = env
    assert vision_factory(services.llm, services.router, None)(None) is None


def test_images_never_go_to_a_cloud_model_while_pseudonymising(env):
    services, scripted = env
    vision_ready(services, cloud=True)
    services.settings.set("privacy.pseudonymise", True, expected_version=0)
    with pytest.raises(AllModelsFailed, match="images can't be pseudonymised"):
        services.llm.chat("vision", [WITH_IMAGE])
    assert scripted.requests == []
