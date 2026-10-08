"""R-M3-23 (d): every pass over a statement's untrusted lines takes time in proportion to what it
reads, and the passes over a document look at a deadline as they go.

One sweep times each function that reads statement text, cells, rows or a document built from
them, on 1 MB lines (a CSV cell or an OFX memo reaches Check at full length) and on 20,000-line
documents (rows, withheld lines between rows, label lines above the table, one long page).
Bounds are generous: linear code takes well under a second here, and quadratic code takes
minutes. A completeness check makes sure a new function of this kind joins the sweep.

The default suite runs each function on the most hostile shapes; the slow suite on all."""

from __future__ import annotations

import datetime as dt
import importlib
import inspect
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import pytest

from ingest.test_linear_time import SHAPES, adversarial
from tuppence.ingest import (
    balances,
    check,
    dedupe,
    identify,
    mapping,
    parse,
    reader,
    registry,
    sensitive,
    service,
    textnum,
    textprep,
)
from tuppence.ingest.clock import CheckTimeout, after
from tuppence.ingest.importers import csv_layout, ofx, qif
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.registry import CsvLayout, LayoutRegistry, load_bank_pack

MB = 1_000_000
N = 20_000
BOUND = 8.0  # seconds for one call: linear code takes a fraction of it, quadratic minutes
HOSTILE = ["thousands", "spaced digits", "figure pairs", "spaces", "bullets"]
# The slow suite: every hostile shape that reaches a different kind of pass (the per-pattern
# test in test_linear_time runs every shape against every pattern).
EVERY_KIND = [
    *HOSTILE,
    "digits",
    "digit-commas",
    "dashed digits",
    "dotted digits",
    "pounds",
    "dots",
    "balance figures",
    "dates",
    "credit markers",
    "sort-code groups",
    "card groups",
]
DOCS = ["rows", "between", "preamble", "page"]
DAY = dt.date(2026, 10, 2)


# --- inputs ---------------------------------------------------------------------------------


@lru_cache(maxsize=4)
def line(shape: str) -> str:
    return adversarial(shape, MB)


def _row(ref: str, pence: int, text: str, **kw: Any) -> ParsedRow:
    return ParsedRow(
        ref=ref, date=DAY, amount_pence=pence, amount_text=text, raw_description="Shop", **kw
    )


def _statement(rows: list[ParsedRow], **kw: Any) -> ParsedStatement:
    return ParsedStatement(
        importer="ai-read",
        period_start=dt.date(2026, 10, 1),
        period_end=dt.date(2026, 10, 31),
        opening_balance_pence=10_400,
        closing_balance_pence=9_700,
        rows=rows,
        **kw,
    )


@lru_cache(maxsize=2)
def long_doc(shape: str) -> tuple[Document, ParsedStatement]:
    """A table whose third line (and its middle cell) is 1 MB, cited by a row, as a CSV or OFX
    line reaches Check."""
    long = line(shape)
    texts = [
        "Date,Description,Paid out,Balance",
        "02/10/2026,Shop,4.00,100.00",
        long,
        "03/10/2026,Cafe,3.00,97.00",
    ]
    lines = [Line(ref=f"L{i}", text=t) for i, t in enumerate(texts, start=1)]
    doc = Document(
        kind="csv",
        sha256="x",
        lines=lines,
        table=[t.split(",") for t in texts[:2]]
        + [["02/10/2026", long, "1.00", ""]]
        + [texts[3].split(",")],
        header_refs=["L1"],
        data_refs=["L2", "L3", "L4"],
        held_amount_refs=["L3"],
    )
    parsed = _statement(
        [
            _row("L2", -400, "4.00", balance_after_pence=10_000),
            _row("L3", -100, "1.00", sign_from="Paid out"),
            _row("L4", -300, "3.00", balance_after_pence=9_700),
        ]
    )
    return doc, parsed


def _text_rows(n: int) -> list[str]:
    return [f"{1 + i % 28:02d}/10/2026 Shop {i} 1.00 {1000 + i}.00" for i in range(n)]


@lru_cache(maxsize=4)
def many_text(kind: str) -> str:
    head = "Example Bank\nStatement 01/10/2026 to 31/10/2026\nDate Description Paid out Balance\n"
    if kind == "rows":
        return head + "\n".join(_text_rows(N)) + "\n"
    if kind == "between":
        return (
            head
            + "02/10/2026 Shop 1.00 999.00\n"
            + "Sort code 12-34-56\n" * N
            + ("03/10/2026 Cafe 1.00 998.00\n")
        )
    if kind == "preamble":
        return "Example Bank\n" + "Opening balance\n" * N + head + "\n".join(_text_rows(10)) + "\n"
    return head + "\n".join(_text_rows(N)) + "\n"


@lru_cache(maxsize=4)
def many_doc(kind: str) -> tuple[Document, ParsedStatement]:
    """20,000 lines of one kind, with a row for each data line the model might have read
    (signs read the wrong way round, so sign repair has work to do)."""
    if kind == "page":
        doc = textprep.pages_document(
            [["Date Description Paid out Balance", *_text_rows(N)]], sha256="x", kind="pdf"
        )
    else:
        doc = textprep.text_document(many_text(kind), sha256="x")
    by_ref = doc.by_ref()
    rows = []
    for i, ref in enumerate(
        r for r in doc.data_refs if "Shop" in by_ref[r].text or "Cafe" in by_ref[r].text
    ):
        rows.append(
            _row(ref, 100, "1.00", balance_after_pence=None, sign_from=f"Paid out {i % 50}")
        )
    parsed = _statement(rows, skipped=[SkippedLine(ref=doc.data_refs[0], reason="headings")])
    return doc, parsed


def _layout() -> CsvLayout:
    return CsvLayout(
        id="sweep",
        name="Sweep",
        providers=["other"],
        kind="current",
        signature=["Date", "Description", "Paid out", "Balance"],
        date="Date",
        date_formats=["%d/%m/%Y"],
        description=["Description"],
        amount="Paid out",
        balance="Balance",
    )


class _NoRows:
    """A read model that answers every chunk with nothing (only the local work is timed)."""

    def structured(self, task, messages, schema, *, max_tokens=4096, run=None):
        del task, messages, max_tokens, run
        if schema is mapping.MappingOut:
            return _mapping()
        return schema.model_validate(
            {
                "statement": {
                    "period_start": None,
                    "period_end": None,
                    "opening_balance": None,
                    "closing_balance": None,
                    "currency": "GBP",
                },
                "transactions": [],
                "skipped": [],
            }
        )


# --- the sweep: every function that reads untrusted text --------------------------------------

LineCall = Callable[[str], object]
DocCall = Callable[[Document, ParsedStatement], object]

# Functions of one line, cell or string: called with each hostile 1 MB string.
LINE: dict[str, LineCall] = {
    "textprep.sniff_delimiter": textprep.sniff_delimiter,
    "textprep.csv_records": lambda s: textprep.csv_records(f"a,b\n1,{s}\n"),
    "textprep.csv_document": lambda s: textprep.csv_document(
        f"Date,Note\n01/10/2026,{s}\n".encode(), sha256="x"
    ),
    "textprep.is_money_cell": textprep.is_money_cell,
    "textprep.is_date_cell": textprep.is_date_cell,
    "textprep.is_sensitive": textprep.is_sensitive,
    "textprep.is_summary": textprep.is_summary,
    "textprep.only_summary_vocab": textprep.only_summary_vocab,
    "textprep.is_heading": textprep.is_heading,
    "textprep.is_row": textprep.is_row,
    "textprep.is_anchor": textprep.is_anchor,
    "textprep.has_amount": textprep.has_amount,
    "textprep.header_names": lambda s: textprep.header_names([s[: textprep.MAX_LINE_CHARS]], [0]),
    "textprep.text_document": lambda s: textprep.text_document(f"Statement\n{s}\n", sha256="x"),
    "textprep.pages_document": lambda s: textprep.pages_document(
        [["Statement", s]], sha256="x", kind="pdf"
    ),
    "textprep.render": lambda s: textprep.render([Line(ref="L1", text=s)]),
    "sensitive.normalise": sensitive.normalise,
    "sensitive.view": sensitive.view,
    "sensitive.name_key": sensitive.name_key,
    "sensitive.classify": lambda s: sensitive.classify(s, names=["Alex Example"]),
    "sensitive.is_balance_line": sensitive.is_balance_line,
    "sensitive.is_figure_line": sensitive.is_figure_line,
    "sensitive.is_balance_label": sensitive.is_balance_label,
    "sensitive.is_sensitive": sensitive.is_sensitive,
    "sensitive.holds_details": sensitive.holds_details,
    "sensitive.mask": sensitive.mask,
    "sensitive.prepare_outbound": lambda s: sensitive.prepare_outbound(s, names=["Alex Example"]),
    "sensitive.mask_line": sensitive.mask_line,
    "sensitive.is_masked_balance": sensitive.is_masked_balance,
    "sensitive.unmasked_label": sensitive.unmasked_label,
    "identify.header_facts": identify.header_facts,
    "check.figures": check.figures,
    "check.balance_printed": lambda s: check.balance_printed(
        _row("L1", -100, "1.00", balance_after_pence=100), s, "household"
    ),
    "parse.card_payment": parse.card_payment,
    "dedupe.normalise_description": dedupe.normalise_description,
    "dedupe.description_tokens": dedupe.description_tokens,
    "dedupe.similar_descriptions": lambda s: dedupe.similar_descriptions(s, s),
    "mapping.cell_token": mapping.cell_token,
    "mapping.shown_heading": mapping.shown_heading,
    "mapping.header_problem": lambda s: mapping.header_problem([s, "Date", "Amount"]),
    "mapping.sketch": lambda s: mapping.sketch(
        ["Date", "Note", "Amount"], [["01/10/2026", s, "1.00"]] * 5
    ),
    "reader.tidy": reader.tidy,
    "registry.norm": registry.norm,
    "registry.header_key": lambda s: registry.header_key([s, "Date"]),
    "registry.learned_key": lambda s: registry.learned_key([s, "Date"], "current"),
    "registry.learned_id": lambda s: registry.learned_id([s, "Date"], "current"),
    "textnum.parse_money": textnum.parse_money,
    "textnum.direction_of": textnum.direction_of,
    "textnum.has_printed_sign": textnum.has_printed_sign,
    "textnum.has_credit_marker": textnum.has_credit_marker,
    "textnum.parse_date": textnum.parse_date,
    "textnum.decode_text": lambda s: textnum.decode_text(s.encode()),
    "ofx.ofx_document": lambda s: ofx.ofx_document(_ofx(s), sha256="x"),
    "ofx.parse_ofx": lambda s: ofx.parse_ofx(_ofx(s)),
    "qif.qif_document": lambda s: qif.qif_document(_qif(s), sha256="x"),
    "qif.parse_qif": lambda s: qif.parse_qif(_qif(s)),
    "service.clean_filename": service.clean_filename,
}


def _ofx(memo: str) -> str:
    return (
        "<OFX><BANKACCTFROM><BANKID>1<ACCTID>2</BANKACCTFROM><BANKTRANLIST><STMTTRN>"
        f"<DTPOSTED>20261002<TRNAMT>-1.00<NAME>Shop<MEMO>{memo}</STMTTRN></BANKTRANLIST></OFX>"
    )


def _qif(memo: str) -> str:
    return f"!Type:Bank\nD02/10/2026\nT-1.00\nPShop\nM{memo}\n^\n"


def _pack() -> LayoutRegistry:
    return LayoutRegistry(load_bank_pack())


# Functions of a document, its rows or both: called with the 1 MB-line table and with each
# 20,000-line document.
DOC: dict[str, DocCall] = {
    "textprep.find_header": lambda d, p: textprep.find_header(
        d.table or [[ln.text] for ln in d.lines]
    ),
    "textprep.table_document": lambda d, p: textprep.table_document(
        [(i, ln.text, ln.text.split(",")) for i, ln in enumerate(d.lines, start=1)],
        sha256="x",
        kind="csv",
    ),
    "textprep.split_preamble": lambda d, p: textprep.split_preamble(d.lines),
    "textprep.split_screenshot": lambda d, p: textprep.split_screenshot(d.lines),
    "textprep.sent_lines": lambda d, p: textprep.sent_lines(d),
    "textprep.plan_chunks": lambda d, p: textprep.plan_chunks(d, rows_per_chunk=40),
    "identify.identify": lambda d, p: identify.identify(
        d, pack=load_bank_pack(), registry=_pack(), key=b"k"
    ),
    "balances.local_balances": lambda d, p: balances.local_balances(d, perspective="household"),
    "balances.repair_signs": lambda d, p: balances.repair_signs(
        d, p.model_copy(deep=True), opening=10_000, closing=None, level="full"
    ),
    "check.drop_unprinted_balances": lambda d, p: check.drop_unprinted_balances(
        p.model_copy(deep=True), d.lines
    ),
    "check.check_rows": lambda d, p: check.check_rows(
        d.lines, all_lines=d.lines, context_refs=d.header_refs, data_refs=d.data_refs, parsed=p
    ),
    "check.check_statement": lambda d, p: check.check_statement(p, dates=True),
    "check.check_document": lambda d, p: check.check_document(d, p),
    "check.balance_verified": lambda d, p: check.balance_verified(p, [], "full"),
    "parse.sign_doubt": lambda d, p: parse.sign_doubt(p, "credit_card", _layout(), new=True),
    "parse.held_back_message": lambda d, p: parse.held_back_message(d),
    "parse.too_long_message": lambda d, p: parse.too_long_message(d),
    "parse.restore_masked": lambda d, p: parse.restore_masked(p.model_copy(deep=True), d),
    "parse.level_for": lambda d, p: parse.level_for(d),
    "dedupe.assign_fingerprints": lambda d, p: dedupe.assign_fingerprints(p.rows, "a"),
    "dedupe.plan_dedupe": lambda d, p: dedupe.plan_dedupe(
        p.rows,
        [fp for fp, _ in dedupe.assign_fingerprints(p.rows, "a")],
        [
            dedupe.Existing(f"t{i}", r.date, r.amount_pence, "Other", f"x{i}")
            for i, r in enumerate(p.rows)
        ],
        window=(dt.date(2026, 10, 1), dt.date(2026, 10, 31)),
    ),
    "mapping.marker_contradiction": lambda d, p: mapping.marker_contradiction(d, _layout(), p),
    "mapping.missing_sign_source": lambda d, p: mapping.missing_sign_source(
        p, _layout(), "current"
    ),
    "mapping.mapping_to_layout": lambda d, p: _try_layout(d),
    "mapping.propose_layout": lambda d, p: _try(
        lambda: mapping.propose_layout(
            d.model_copy(update={"kind": "csv"}), llm=_NoRows(), run=None, max_attempts=1
        )
    ),
    "reader.ref_aliases": lambda d, p: reader.ref_aliases(d),
    "reader.user_message": lambda d, p: _messages(d),
    "reader.read_document": lambda d, p: _read(d),
    "registry.header_cells": lambda d, p: registry.header_cells(d),
    "registry.data_records": lambda d, p: registry.data_records(d),
    "registry.column_getter": lambda d, p: registry.column_getter(
        _layout(), registry.header_cells(d)
    ),
    "csv_layout.parse_with_layout": lambda d, p: _try(csv_layout.parse_with_layout, d, _layout()),
    "service.recheck": lambda d, p: service.recheck(d, p.model_copy(deep=True), "full"),
    "service.confirmed_layout": lambda d, p: service.confirmed_layout(
        {"pending_layout": _layout().model_dump(), "proposed_signs": {}}, p
    ),
    "parse.parse_document": lambda d, p: _parse_table(d),
}


def _try(fn: Callable[..., object], *args: object) -> object:
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - a refusal is a fine answer; only time is measured
        return exc


def _mapping() -> mapping.MappingOut:
    return mapping.MappingOut(
        date_column="Date",
        description_columns=["Description"],
        merchant_column=None,
        amount_column=None,
        money_out_column="Paid out",
        money_in_column=None,
        amounts_are="money_out_negative",
        balance_column="Balance",
        category_column=None,
        type_column=None,
    )


def _try_layout(doc: Document) -> object:
    header = registry.header_cells(doc) or ["Date", "Description", "Paid out", "Balance"]
    rows = [cells for _, cells in registry.data_records(doc)] if doc.table else []
    return _try(lambda: mapping.mapping_to_layout(_mapping(), header, rows, account_kind="current"))


def _messages(doc: Document) -> list[str]:
    aliases = reader.ref_aliases(doc)
    chunks = textprep.plan_chunks(doc, rows_per_chunk=40)
    return [reader.user_message("x", chunk, doc, aliases) for chunk in chunks]


def _read(doc: Document) -> object:
    return reader.read_document(
        doc,
        llm=_NoRows(),
        run=None,
        perspective="household",
        level="full",
        account="current account",
        facts=identify.HeaderFacts(),
        today=DAY,
        context_window=128_000,
        parallel=1,
        max_attempts=1,
        prompt="read",
    )


def _parse_table(doc: Document) -> object:
    """The whole parse step for a table with a remembered layout (no AI): the importer and
    every check."""
    from pathlib import Path

    layouts = _pack()
    layouts.match = lambda d, kind=None: _layout()  # type: ignore[method-assign]
    return _try(
        lambda: parse.parse_document(
            doc.model_copy(update={"kind": "csv"}),
            Path("unused.csv"),
            identify.Evidence(layout_fingerprint="x", label="sweep"),
            "current",
            registry=layouts,
            llm=_NoRows(),
            run=None,
            context_window=None,
            today=DAY,
            limits=parse.ReaderLimits(),
        )
    )


def _time(fn: Callable[[], object]) -> float:
    started = time.perf_counter()
    fn()
    return time.perf_counter() - started


# --- the timing test ---------------------------------------------------------------------------


def _cases(shapes: list[str]) -> list[tuple[str, str]]:
    return [(name, shape) for name in LINE for shape in shapes] + [
        (name, shape) for name in DOC for shape in [*shapes[:1], *DOCS]
    ]


def _run(name: str, shape: str) -> None:
    if name in LINE:
        value = line(shape)
        took = _time(lambda: LINE[name](value))
    else:
        doc, parsed = long_doc(shape) if shape in SHAPES else many_doc(shape)
        took = _time(lambda: DOC[name](doc, parsed))
    assert took < BOUND, f"{name} took {took:.1f}s on {shape}"


@pytest.mark.parametrize(("name", "shape"), _cases(HOSTILE[:2]))
def test_every_pass_over_untrusted_text_is_fast(name, shape):
    _run(name, shape)


@pytest.mark.slow
@pytest.mark.parametrize(("name", "shape"), _cases(EVERY_KIND))
def test_every_pass_over_untrusted_text_is_fast_on_every_shape(name, shape):
    _run(name, shape)


# --- every such function is in the sweep ---------------------------------------------------------

_UNTRUSTED = {
    "text",
    "line",
    "lines",
    "doc",
    "document",
    "parsed",
    "rows",
    "cell",
    "cells",
    "records",
    "data",
    "pages",
    "header",
    "raw",
    "texts",
    "chunk",
    "a",
    "b",
}
_MODULES = [
    "textprep",
    "sensitive",
    "identify",
    "balances",
    "check",
    "parse",
    "dedupe",
    "mapping",
    "reader",
    "registry",
    "textnum",
    "importers.ofx",
    "importers.qif",
    "importers.csv_layout",
    "service",
]
# Functions with such a parameter that don't read statement text, and why.
_ELSEWHERE = {
    "textnum.to_pence": "takes a Decimal already read",
}


def _short(module: str, name: str) -> str:
    return f"{module.rsplit('.', 1)[-1]}.{name}"


def test_every_function_that_reads_untrusted_text_is_swept():
    missing = []
    for module in _MODULES:
        mod = importlib.import_module(f"tuppence.ingest.{module}")
        for name, fn in vars(mod).items():
            if name.startswith("_") or not inspect.isfunction(fn):
                continue
            if fn.__module__ != mod.__name__:
                continue
            if not set(inspect.signature(fn).parameters) & _UNTRUSTED:
                continue
            key = _short(module, name)
            if key not in LINE and key not in DOC and key not in _ELSEWHERE:
                missing.append(key)
    assert missing == []


# --- the passes over a document stop at the deadline ------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda d, p, t: balances.local_balances(d, perspective="household", deadline=t),
        lambda d, p, t: balances.repair_signs(d, p, opening=1, level="full", deadline=t),
        lambda d, p, t: check.check_rows(
            d.lines,
            all_lines=d.lines,
            context_refs=[],
            data_refs=d.data_refs,
            parsed=p,
            deadline=t,
        ),
        lambda d, p, t: check.check_statement(p, dates=True, deadline=t),
        lambda d, p, t: check.check_document(d, p, deadline=t),
        lambda d, p, t: textprep.plan_chunks(d, rows_per_chunk=40, deadline=t),
        lambda d, p, t: identify.identify(
            d, pack=load_bank_pack(), registry=_pack(), key=b"k", deadline=t
        ),
        lambda d, p, t: dedupe.plan_dedupe(
            p.rows, [str(i) for i in range(len(p.rows))], [], window=(DAY, DAY), deadline=t
        ),
        lambda d, p, t: service.recheck(d, p, "full", deadline=t),
    ],
)
def test_the_passes_over_a_document_stop_at_the_deadline(call):
    doc, parsed = many_doc("rows")
    with pytest.raises(CheckTimeout):
        call(doc, parsed.model_copy(deep=True), after(-1))
