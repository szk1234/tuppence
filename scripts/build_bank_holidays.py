"""Regenerate src/tuppence/datapacks/baseline/uk-bank-holidays.json from gov.uk.

Run occasionally; the M6 data-pack pipeline replaces this with signed packs.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://www.gov.uk/bank-holidays.json"
OUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "tuppence"
    / "datapacks"
    / "baseline"
    / "uk-bank-holidays.json"
)


def main() -> int:
    # Fixed https URL, not user input: the S310 file:/custom-scheme risk does not apply.
    with urllib.request.urlopen(URL, timeout=30) as r:  # noqa: S310
        raw = json.load(r)
    divisions = {
        name: sorted(e["date"] for e in div["events"]) for name, div in sorted(raw.items())
    }
    out = {"source": URL, "fetched": date.today().isoformat(), "divisions": divisions}
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({', '.join(f'{k}: {len(v)}' for k, v in divisions.items())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
