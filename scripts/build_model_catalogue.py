"""Regenerate src/tuppence/datapacks/baseline/model-catalogue.json.

Sources: OpenRouter's public model list (context windows, capabilities, USD prices)
plus Anthropic first-party entries maintained below. Run occasionally; the M6 data-pack
pipeline replaces this with signed packs.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

OUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "tuppence"
    / "datapacks"
    / "baseline"
    / "model-catalogue.json"
)

# Anthropic first-party IDs and prices (USD per million tokens), checked 2026-10-07.
ANTHROPIC = [
    ("claude-opus-5-5", 1_000_000, 128_000, 4.0, 20.0),
    ("claude-sonnet-5-5", 1_000_000, 128_000, 2.0, 10.0),
    ("claude-haiku-4-5", 200_000, 64_000, 1.0, 5.0),
    ("claude-fable-5-1", 1_000_000, 128_000, 10.0, 50.0),
]


def per_mtok(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return (
        round(price * 1_000_000, 6) if price >= 0 else None
    )  # -1 marks routers with variable price


def main() -> int:
    url = "https://openrouter.ai/api/v1/models"
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        data = json.load(r)["data"]
    entries: list[dict[str, Any]] = []
    ids = {m["id"] for m in data}
    for m in data:
        mid = m["id"]
        # Routers and meta entries have no fixed model behind them.
        if mid.startswith(("openrouter/", "~")):
            continue
        # Tier variants (":free", ":beta", ...) must never stand in for the paid model.
        if ":" in mid and (mid.endswith(":free") or mid.split(":", 1)[0] in ids):
            continue
        params = set(m.get("supported_parameters") or [])
        pricing = m.get("pricing") or {}
        model_part = m["id"].split("/", 1)[-1]
        modalities = (m.get("architecture") or {}).get("input_modalities") or []
        entries.append(
            {
                "id": m["id"],
                "aliases": sorted({model_part}),
                "context_window": m.get("context_length"),
                "max_output_tokens": (m.get("top_provider") or {}).get("max_completion_tokens"),
                "supports_tools": "tools" in params,
                "supports_json_schema": bool(params & {"response_format", "structured_outputs"}),
                "supports_vision": "image" in modalities,
                "price_in_usd_per_mtok": per_mtok(pricing.get("prompt")),
                "price_out_usd_per_mtok": per_mtok(pricing.get("completion")),
            }
        )
    for mid, ctx, out, pin, pout in ANTHROPIC:
        entries.append(
            {
                "id": f"anthropic/{mid}",
                "aliases": [mid],
                "context_window": ctx,
                "max_output_tokens": out,
                "supports_tools": True,
                "supports_json_schema": True,
                "supports_vision": True,
                "price_in_usd_per_mtok": pin,
                "price_out_usd_per_mtok": pout,
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": date.today().isoformat(), "models": entries}
    OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(entries)} models to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
