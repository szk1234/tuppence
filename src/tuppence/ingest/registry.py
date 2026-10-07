"""The `uk-banks` pack: CSV layouts, PDF markers, sort codes and BICs (spec §6.2, §12.2).

Three layers of CSV layouts, most specific first: layouts learned from an AI
mapping (stored in the database), the user's own YAML files in
`<data>/config/importers/`, and the pack's community-maintained `layouts.yaml`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.ingest.models import AccountKind, Document, Perspective
from tuppence.ingest.textnum import parse_date, parse_money

PACK_ID = "uk-banks"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkipRule(_Strict):
    column: str
    not_in: list[str] | None = None
    equals: list[str] | None = None
    reason: str


class CsvLayout(_Strict):
    id: str
    name: str
    providers: list[str] = Field(default_factory=list)
    kind: AccountKind | None = None
    signature: list[str] = Field(default_factory=list)  # headings that must all be present
    columns: int | None = (
        None  # files with no heading row: exact column count; refer to columns as "#0", "#1"…
    )
    date: str
    date_formats: list[str] = Field(default_factory=lambda: ["%d/%m/%Y"])
    description: list[str]
    merchant: str | None = None
    amount: str | None = None
    money_out: str | None = None
    money_in: str | None = None
    fee: str | None = None
    perspective: Perspective = "household"
    balance: str | None = None
    category: str | None = None
    type: str | None = None
    account_number: str | None = None
    skip: list[SkipRule] = Field(default_factory=list)
    confirmed: bool = False
    notes: str = ""
    source: Literal["pack", "user", "learned"] = "pack"

    @model_validator(mode="after")
    def _shape(self) -> CsvLayout:
        split = self.money_out is not None or self.money_in is not None
        if self.amount is not None and split:
            raise ValueError("use either amount, or money_out and money_in, not both")
        if self.amount is None and (self.money_out is None or self.money_in is None):
            raise ValueError("needs amount, or both money_out and money_in")
        if not self.signature and self.columns is None:
            raise ValueError("needs a signature (headings) or a column count")
        return self


class PdfMarker(_Strict):
    provider: str
    kind: AccountKind | None = None
    any: list[str]


class BankPack(_Strict):
    id: str = PACK_ID
    version: str
    layouts: list[CsvLayout]
    pdf_markers: list[PdfMarker]
    sort_codes: dict[str, str]  # "040004" or a two-digit prefix "40" → provider
    bics: dict[str, str]  # first 8 characters of a BIC → provider


def norm(cell: str) -> str:
    return " ".join(cell.casefold().split())


def header_key(cells: Sequence[str]) -> str:
    joined = "|".join(norm(c) for c in cells)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _read(node: Traversable, name: str) -> str:
    return node.joinpath(name).read_text(encoding="utf-8")


def load_pack(node: Traversable) -> BankPack:
    """Read a pack folder, checking every file against the manifest's SHA-256."""
    manifest = json.loads(_read(node, "manifest.json"))
    for name, digest in manifest["files"].items():
        actual = hashlib.sha256(node.joinpath(name).read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(f"{PACK_ID} pack file {name} doesn't match its checksum")
    layouts = yaml.safe_load(_read(node, "layouts.yaml"))["layouts"]
    markers = yaml.safe_load(_read(node, "markers.yaml"))
    return BankPack(
        version=manifest["version"],
        layouts=[CsvLayout.model_validate(x) for x in layouts],
        pdf_markers=[PdfMarker.model_validate(x) for x in markers["pdf_markers"]],
        sort_codes={str(k): v for k, v in markers["sort_codes"].items()},
        bics={str(k): v for k, v in markers["bics"].items()},
    )


def load_bank_pack() -> BankPack:
    """The baseline pack shipped inside the app."""
    return load_pack(resources.files("tuppence.datapacks.baseline").joinpath(PACK_ID))


class LearnedLayouts:
    """Layouts learned from an AI mapping, in the `csv_layout` table (or memory, for tests)."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db
        self._memory: dict[str, CsvLayout] = {}

    def all(self) -> list[tuple[str, CsvLayout]]:
        if self.db is None:
            return list(self._memory.items())
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT header_key, layout FROM csv_layout ORDER BY created_at"
            ).fetchall()
        return [(r["header_key"], CsvLayout.model_validate_json(r["layout"])) for r in rows]

    def put(self, key: str, layout: CsvLayout) -> None:
        if self.db is None:
            self._memory[key] = layout
            return
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO csv_layout (id, header_key, layout, created_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(header_key) DO UPDATE SET layout = excluded.layout",
                [layout.id, key, layout.model_dump_json(), to_iso(utcnow())],
            )


class LayoutRegistry:
    def __init__(
        self, pack: BankPack, *, user_dir: Path | None = None, learned: LearnedLayouts | None = None
    ) -> None:
        self.pack = pack
        self.user_dir = user_dir
        self.learned = learned or LearnedLayouts()
        self._user_cache: tuple[tuple[tuple[str, int], ...], list[CsvLayout], list[str]] = (
            (),
            [],
            [],
        )

    def _load_user(self) -> tuple[list[CsvLayout], list[str]]:
        if self.user_dir is None or not self.user_dir.is_dir():
            return [], []
        paths = sorted(self.user_dir.glob("*.yaml"))
        signature = tuple((p.name, p.stat().st_mtime_ns) for p in paths)
        if signature == self._user_cache[0]:
            return self._user_cache[1], self._user_cache[2]
        found: list[CsvLayout] = []
        errors: list[str] = []
        for path in paths:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                for item in data.get("layouts", []):
                    found.append(CsvLayout.model_validate({**item, "source": "user"}))
            except (
                yaml.YAMLError,
                ValidationError,
                UnicodeDecodeError,
                AttributeError,
                TypeError,
            ) as exc:
                errors.append(f"{path.name}: {str(exc).splitlines()[0][:200]}")
        self._user_cache = (signature, found, errors)
        return found, errors

    def user_layouts(self) -> list[CsvLayout]:
        return self._load_user()[0]

    def user_errors(self) -> list[str]:
        return self._load_user()[1]

    def is_known_header(self, cells: Sequence[str]) -> bool:
        present = {norm(c) for c in cells if c.strip()}
        if any(key == header_key(cells) for key, _ in self.learned.all()):
            return True
        return any(
            layout.signature and {norm(s) for s in layout.signature} <= present
            for layout in [*self.user_layouts(), *self.pack.layouts]
        )

    def match(self, doc: Document) -> CsvLayout | None:
        header = header_cells(doc)
        if header is not None:
            key = header_key(header)
            for stored_key, layout in self.learned.all():
                if stored_key == key:
                    return layout
            present = {norm(c) for c in header if c.strip()}
            best: CsvLayout | None = None
            for layout in [*self.user_layouts(), *self.pack.layouts]:
                wanted = {norm(s) for s in layout.signature}
                if (
                    wanted
                    and wanted <= present
                    and (best is None or len(wanted) > len(best.signature))
                ):
                    best = layout
            return best
        first = next(iter(data_records(doc)), None)
        if first is None:
            return None
        cells = first[1]
        for layout in [*self.user_layouts(), *self.pack.layouts]:
            if (
                layout.columns is not None
                and layout.columns == len(cells)
                and _headerless_fits(layout, cells)
            ):
                return layout
        return None

    def save_learned(self, doc: Document, layout: CsvLayout) -> CsvLayout:
        header = header_cells(doc)
        if header is None:
            raise ValueError("only files with a heading row can be learned")
        learned = layout.model_copy(
            update={"source": "learned", "signature": [c for c in header if c.strip()]}
        )
        self.learned.put(header_key(header), learned)
        return learned


def header_cells(doc: Document) -> list[str] | None:
    if not doc.header_refs or doc.table is None:
        return None
    refs = [line.ref for line in doc.lines]
    return doc.table[refs.index(doc.header_refs[0])]


def data_records(doc: Document) -> list[tuple[str, list[str]]]:
    if doc.table is None:
        return []
    wanted = set(doc.data_refs)
    return [
        (line.ref, cells)
        for line, cells in zip(doc.lines, doc.table, strict=True)
        if line.ref in wanted
    ]


def column_getter(
    layout: CsvLayout, header: Sequence[str] | None
) -> Callable[[Sequence[str], str | None], str]:
    """A function returning a cell's text by heading name or `#n`, or "" when absent."""
    positions = {norm(c): i for i, c in enumerate(header or [])}

    def position(ref: str) -> int:
        if ref.startswith("#"):
            return int(ref[1:])
        if norm(ref) not in positions:
            raise KeyError(ref)
        return positions[norm(ref)]

    def get(cells: Sequence[str], ref: str | None) -> str:
        if ref is None:
            return ""
        i = position(ref)
        return cells[i].strip() if i < len(cells) else ""

    return get


def _headerless_fits(layout: CsvLayout, cells: Sequence[str]) -> bool:
    get = column_getter(layout, None)
    if parse_date(get(cells, layout.date), layout.date_formats) is None:
        return False
    amount_ref = layout.amount or layout.money_out
    return parse_money(get(cells, amount_ref)) is not None or bool(
        layout.money_in and get(cells, layout.money_in)
    )
