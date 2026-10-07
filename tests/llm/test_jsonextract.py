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


def test_extract_json_prefers_object_and_survives_nesting():
    assert extract_json('see [1] and {"a": 1}') == {"a": 1}
    assert extract_json("[1, 2]") == [1, 2]
    assert extract_json("[" * 100000 + ' {"ok": 1}') == {"ok": 1}


def test_strict_schema_ref_siblings_recursion_and_dicts():
    s = to_strict_schema(
        {
            "$defs": {"A": {"type": "object", "properties": {"x": {"type": "string"}}}},
            "type": "object",
            "properties": {"a": {"$ref": "#/$defs/A", "description": "an A"}},
            "additionalProperties": {"type": "string"},
        }
    )
    assert s["properties"]["a"]["description"] == "an A"
    assert s["properties"]["a"]["additionalProperties"] is False
    assert s["additionalProperties"] is False
    rec = {
        "$defs": {"R": {"type": "object", "properties": {"n": {"$ref": "#/$defs/R"}}}},
        "$ref": "#/$defs/R",
    }
    with pytest.raises(ValueError, match="Recursive"):
        to_strict_schema(rec)
