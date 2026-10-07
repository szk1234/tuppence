"""Learn an unfamiliar CSV layout: the AI proposes a column mapping once, the
mapping is checked like any importer, and a mapping that passes is saved so later
files in that layout need no AI call (spec §6.2 step 3)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tuppence.core.errors import safe_error_text
from tuppence.ingest.check import check_document
from tuppence.ingest.importers.csv_layout import ImportResult, LayoutMismatch, parse_with_layout
from tuppence.ingest.models import Document
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.reader import StructuredLLM
from tuppence.ingest.registry import CsvLayout, data_records, header_cells, header_key, norm
from tuppence.llm.types import LLMBadResponse, Message

SAMPLE_ROWS = 5
ALLOWED_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d %b %y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y",
)


class MappingOut(BaseModel):
    date_column: str
    date_format: str
    description_columns: list[str]
    merchant_column: str | None
    amount_column: str | None
    money_out_column: str | None
    money_in_column: str | None
    amounts_are: Literal["money_out_negative", "purchases_positive"]
    balance_column: str | None
    category_column: str | None
    type_column: str | None


class MappingOutcome(BaseModel):
    layout: CsvLayout | None
    result: ImportResult | None
    errors: list[str] = Field(default_factory=list)
    attempts: int = 0


def _parses(fmt: str, values: list[str]) -> bool:
    try:
        for value in values:
            datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True


def mapping_to_layout(
    mapping: MappingOut, header: list[str], samples: list[list[str]]
) -> CsvLayout:
    """A CsvLayout from the model's answer, or ValueError naming what's wrong."""
    known = {norm(h): h for h in header if h.strip()}

    def column(name: str | None, what: str) -> str | None:
        if name is None:
            return None
        if norm(name) not in known:
            raise ValueError(f'{what} "{name}" is not one of the headings')
        return known[norm(name)]

    date_column = column(mapping.date_column, "date_column")
    assert date_column is not None
    index = [norm(h) for h in header].index(norm(date_column))
    dates = [row[index] for row in samples if len(row) > index and row[index].strip()]
    formats = [mapping.date_format, *ALLOWED_DATE_FORMATS]
    date_format = next(
        (f for f in formats if f in ALLOWED_DATE_FORMATS and _parses(f, dates)), None
    )
    if date_format is None:
        raise ValueError(f"no allowed date_format reads the dates in {date_column}")
    descriptions = [
        c for c in (column(d, "description_columns") for d in mapping.description_columns[:3]) if c
    ]
    if not descriptions:
        raise ValueError("description_columns is empty")
    return CsvLayout(
        id=f"learned-{header_key(header)}",
        name="Your bank's export (learned)",
        source="learned",
        signature=[h for h in header if h.strip()],
        date=date_column,
        date_formats=[date_format],
        description=descriptions,
        merchant=column(mapping.merchant_column, "merchant_column"),
        amount=column(mapping.amount_column, "amount_column"),
        money_out=None
        if mapping.amount_column
        else column(mapping.money_out_column, "money_out_column"),
        money_in=None
        if mapping.amount_column
        else column(mapping.money_in_column, "money_in_column"),
        perspective="card"
        if mapping.amounts_are == "purchases_positive" and mapping.amount_column
        else "household",
        balance=column(mapping.balance_column, "balance_column"),
        category=column(mapping.category_column, "category_column"),
        type=column(mapping.type_column, "type_column"),
    )


def propose_layout(
    doc: Document, *, llm: StructuredLLM, run: Any, max_attempts: int = 3, prompt: str | None = None
) -> MappingOutcome:
    header = header_cells(doc)
    if header is None:
        return MappingOutcome(
            layout=None,
            result=None,
            errors=[
                "This file has no row of column headings, so its layout can't be "
                "worked out automatically."
            ],
        )
    samples = [cells for _, cells in data_records(doc)[:SAMPLE_ROWS]]
    base = (
        f"ALLOWED_DATE_FORMATS: {json.dumps(list(ALLOWED_DATE_FORMATS))}\n"
        f"HEADINGS:\n{json.dumps(header)}\nROWS:\n" + "\n".join(json.dumps(row) for row in samples)
    )
    system = Message(role="system", content=prompt or load_prompt("csv_mapping"))
    errors: list[str] = []
    previous: str | None = None
    last: ImportResult | None = None
    for attempt in range(1, max_attempts + 1):
        user = (
            base
            if attempt == 1
            else (
                f"{base}\n\nYour previous answer didn't work:\n"
                + "\n".join(errors[:20])
                + (f"\n{previous}" if previous else "")
            )
        )
        try:
            mapping = llm.structured(
                "read",
                [system, Message(role="user", content=user)],
                MappingOut,
                max_tokens=800,
                run=run,
            )
        except LLMBadResponse as exc:
            errors, previous = [safe_error_text(exc)], None
            continue
        previous = mapping.model_dump_json()
        try:
            layout = mapping_to_layout(mapping, header, samples)
            last = parse_with_layout(doc, layout)
        except (ValueError, LayoutMismatch) as exc:
            errors = [safe_error_text(exc)]
            continue
        errors = last.problems + check_document(doc, last.parsed)
        if not errors:
            return MappingOutcome(layout=layout, result=last, errors=[], attempts=attempt)
    return MappingOutcome(layout=None, result=last, errors=errors, attempts=max_attempts)
