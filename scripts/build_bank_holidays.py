"""Regenerate src/tuppence/datapacks/baseline/uk-bank-holidays.json from gov.uk.

Run occasionally; the M6 data-pack pipeline replaces this with signed packs.

The dates are published by GOV.UK under the Open Government Licence v3.0, so the bundled file
carries the attribution (see also NOTICE). The output is reproducible: the same gov.uk data gives
the same bytes (no fetch date is stamped). The data is validated before anything is written, and
the file is replaced atomically, so a failed or partial download never leaves a broken file.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

URL = "https://www.gov.uk/bank-holidays.json"
LICENCE = "Open Government Licence v3.0"
LICENCE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
ATTRIBUTION = (
    "Contains public sector information licensed under the Open Government Licence v3.0 "
    "(UK bank holidays from GOV.UK)."
)
DIVISIONS = ("england-and-wales", "northern-ireland", "scotland")
OUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "tuppence"
    / "datapacks"
    / "baseline"
    / "uk-bank-holidays.json"
)


def build(raw: dict[str, Any]) -> dict[str, Any]:
    """The bundled form of gov.uk's data. ValueError when it doesn't look like gov.uk's data."""
    if not isinstance(raw, dict) or set(raw) != set(DIVISIONS):
        raise ValueError(f"expected exactly the divisions {', '.join(DIVISIONS)}")
    divisions: dict[str, list[str]] = {}
    for name in DIVISIONS:
        events = raw[name].get("events") if isinstance(raw[name], dict) else None
        if not isinstance(events, list) or not events:
            raise ValueError(f"{name}: no events")
        try:
            days = {date.fromisoformat(e["date"]).isoformat() for e in events}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{name}: an event without a YYYY-MM-DD date") from exc
        divisions[name] = sorted(days)
    return {
        "source": URL,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "divisions": divisions,
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1) + "\n"


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    # Fixed https URL, not user input: the S310 file:/custom-scheme risk does not apply.
    with urllib.request.urlopen(URL, timeout=30) as r:  # noqa: S310
        raw = json.load(r)
    data = build(raw)
    write_atomic(OUT, render(data))
    counts = ", ".join(f"{k}: {len(v)}" for k, v in data["divisions"].items())
    print(f"wrote {OUT} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
