import pytest

from tuppence.llm.jsonextract import extract_json, to_strict_schema


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('Sure! Here it is:\n```json\n{"a": [1, 2]}\n```\nAnything else?', {"a": [1, 2]}),
        ('noise [1, {"b": "x}"}] trailing', [1, {"b": "x}"}]),
        ('<think>hmm {not json}</think>{"ok": true}', {"ok": True}),
    ],
)
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        extract_json("no json at all")


def test_to_strict_schema_inlines_refs_and_requires_all():
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        qty: int = 1

    class Order(BaseModel):
        items: list[Item]
        note: str | None = None

    s = to_strict_schema(Order.model_json_schema())
    assert "$defs" not in s and "$ref" not in str(s)
    assert s["additionalProperties"] is False and set(s["required"]) == {"items", "note"}
    item = s["properties"]["items"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == {"name", "qty"}
    assert "title" not in s and "default" not in str(item)


def test_to_strict_schema_keeps_properties_named_title_and_default():
    from pydantic import BaseModel

    class Odd(BaseModel):
        title: str
        default: bool = False

    s = to_strict_schema(Odd.model_json_schema())
    assert set(s["properties"]) == {"title", "default"}
    assert set(s["required"]) == {"title", "default"}
    assert "title" not in s["properties"]["title"]
    assert "default" not in s["properties"]["default"]
