"""Build the README's "minimum viable local model" table from saved eval results.

uv run python -m evals.table                 # print it
uv run python -m evals.table --write README.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parent / "results"
START, END = "<!-- model-table:start -->", "<!-- model-table:end -->"


def render(summaries: list[dict[str, Any]]) -> str:
    columns = [
        "Model",
        "Statements read correctly",
        "Needing AI",
        "Row accuracy",
        "£ per AI statement",
        "Seconds per AI statement",
    ]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for s in sorted(summaries, key=lambda s: (-s["ai_passed"], s["seconds_per_ai_case"])):
        lines.append(
            f"| {s['model']} | {s['passed']}/{s['cases']} | {s['ai_passed']}/{s['ai_cases']} | "
            f"{s['row_accuracy']:.1%} | £{s['cost_gbp_per_ai_case']:.4f} | "
            f"{s['seconds_per_ai_case']:.1f} |"
        )
    return "\n".join(lines)


def load(folder: Path = RESULTS) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))["summary"] for p in sorted(folder.glob("*.json"))
    ]


def write_readme(readme: Path, table: str) -> None:
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit(f"{readme} has no {START} … {END} markers.")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(f"{head}{START}\n{table}\n{END}{tail}", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.table")
    parser.add_argument("--write", help="README file to update between the model-table markers")
    args = parser.parse_args(argv)
    table = render(load())
    if args.write:
        write_readme(Path(args.write), table)
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
