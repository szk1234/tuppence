"""Find JSON in model output; make schemas acceptable to strict structured-output modes."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any:
    text = _THINK.sub("", text)
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for chunk in candidates:
        for i, ch in enumerate(chunk):
            if ch in "{[":
                try:
                    value, _ = decoder.raw_decode(chunk[i:])
                except json.JSONDecodeError:
                    continue
                return value
    raise ValueError("No JSON found in the model's reply")


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].split("/")[-1]
            return _resolve(copy.deepcopy(defs[name]), defs)
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("$defs", "title", "default"):
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {name: _resolve(sub, defs) for name, sub in v.items()}
            else:
                out[k] = _resolve(v, defs)
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out
    if isinstance(node, list):
        return [_resolve(v, defs) for v in node]
    return node


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return _resolve(schema, schema.get("$defs", {}))
