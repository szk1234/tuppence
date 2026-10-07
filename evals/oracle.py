"""A deterministic stand-in for an AI model, used by tests, the browser tests and
`python -m evals.run --model oracle`.

It answers the `read` prompt by parsing the FILE lines with plain rules, and the
CSV-mapping prompt by matching heading names. It is good enough for the synthetic
corpus and nothing else; it exists so the whole pipeline runs without a model.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

READ_MARKER = "TUPPENCE-READ-V1"
MAPPING_MARKER = "TUPPENCE-CSV-MAPPING-V1"
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DATE = re.compile(
    rf"\b(\d{{1,2}}/\d{{1,2}}/\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}"
    rf"|\d{{1,2}} (?:{_MONTHS})[a-z]*(?: \d{{4}})?)\b",
    re.IGNORECASE,
)
_MONEY = re.compile(r"(?<![\w.,])([+\-−]?£?\d{1,3}(?:,\d{3})*\.\d{2}(?: ?CR)?)(?![\d])")
_REF = re.compile(r"^((?:P\d+)?L\d+): (.*)$")
_WEEKDAY = re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\b", re.IGNORECASE)
_SKIP_WORDS = (
    "brought forward",
    "carried forward",
    "minimum payment",
    "credit limit",
    "payment due",
)
OUT_LABELS = ("Paid out", "Money out", "Debit", "Withdrawals")
IN_LABELS = ("Paid in", "Money in", "Credit", "Deposits")
DATE_FORMATS = [
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y",
]


def reply(messages: list[dict[str, Any]]) -> str:
    """The text a model would send back for these chat messages."""
    text = "\n".join(str(m.get("content", "")) for m in messages)
    user = next(
        (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), ""
    )
    if MAPPING_MARKER in text:
        return json.dumps(mapping(user))
    if READ_MARKER in text:
        return json.dumps(read(user))
    return json.dumps({"note": "oracle has no answer for this prompt"})


def _section(user: str, name: str) -> list[str]:
    match = re.search(rf"^{name}:\n(.*?)(?=^\w[\w ]*:\n|\Z)", user, re.MULTILINE | re.DOTALL)
    return [ln for ln in (match.group(1).splitlines() if match else []) if ln.strip()]


def _value(user: str, label: str) -> str:
    match = re.search(rf"^{label}: (.*)$", user, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _parse_day(token: str, year: int) -> date | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            pass
    for fmt in ("%d %b", "%d %B"):
        try:
            return datetime.strptime(token, fmt).date().replace(year=year)
        except ValueError:
            pass
    return None


def _number(token: str) -> float:
    cleaned = token.replace("£", "").replace(",", "").replace("−", "-").replace("CR", "").strip()
    return float(cleaned)


def read(user: str) -> dict[str, Any]:
    today = date.fromisoformat(_value(user, "TODAY") or "2026-11-01")
    card = "credit card" in _value(user, "ACCOUNT")
    header = _value(user, "STATEMENT HEADER")
    period = re.search(r"period (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", header)
    opening = re.search(r"opening balance (-?[\d.]+)", header)
    closing = re.search(r"closing balance (-?[\d.]+)", header)
    year = date.fromisoformat(period.group(2)).year if period else today.year
    lines = [
        m.groups()
        for ln in [*_section(user, "CONTEXT"), *_section(user, "FILE")]
        if (m := _REF.match(ln))
    ]
    file_refs = {m.group(1) for ln in _section(user, "FILE") if (m := _REF.match(ln))}
    out_label = in_label = None
    balance = float(opening.group(1)) if opening else None
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for ref, text in lines:
        if out_label is None:
            out_label = next(
                (label for label in OUT_LABELS if label.casefold() in text.casefold()), None
            )
            in_label = next(
                (label for label in IN_LABELS if label.casefold() in text.casefold()), None
            )
        when = _DATE.search(text)
        money = _MONEY.findall(text)
        if ref not in file_refs:
            if when and len(money) > 1:
                balance = _number(money[-1])  # the previous chunk's last running balance
            continue
        lowered = text.casefold()
        if "brought forward" in lowered and money:
            balance = _number(money[-1])
        if not when or not money or any(word in lowered for word in _SKIP_WORDS):
            skipped.append({"ref": ref, "reason": "not a transaction line"})
            continue
        day = _parse_day(when.group(1), year)
        if day is None:
            skipped.append({"ref": ref, "reason": "no readable date"})
            continue
        amount_text = money[0]
        size = abs(_number(amount_text))
        sign_from = None
        running = _number(money[1]) if len(money) > 1 else None
        if card:
            amount = size if ("CR" in amount_text or amount_text.startswith(("-", "−"))) else -size
        elif amount_text.startswith(("-", "−")):
            amount = -size
        elif amount_text.startswith("+"):
            amount = size
        else:
            positive = running is not None and balance is not None and running > balance
            amount = size if positive else -size
            sign_from = in_label if positive else out_label
        if running is not None:
            balance = running
        description = _WEEKDAY.sub("", _MONEY.sub("", _DATE.sub("", text, count=1)))
        description = " ".join(description.split())
        rows.append(
            {
                "ref": ref,
                "date": day.isoformat(),
                "amount": amount,
                "amount_text": amount_text,
                "sign_from": sign_from,
                "raw_desc": description,
                "merchant": description,
                "bank_category": None,
                "bank_type": None,
                "running_balance": running,
            }
        )
    dates = [r["date"] for r in rows]
    return {
        "statement": {
            "period_start": period.group(1) if period else (min(dates) if dates else None),
            "period_end": period.group(2) if period else (max(dates) if dates else None),
            "opening_balance": float(opening.group(1)) if opening else None,
            "closing_balance": float(closing.group(1)) if closing else None,
            "currency": "GBP",
        },
        "transactions": rows,
        "skipped": skipped,
    }


def _find(headings: list[str], *words: str) -> str | None:
    for word in words:
        for heading in headings:
            if word in heading.casefold():
                return heading
    return None


def mapping(user: str) -> dict[str, Any]:
    headings: list[str] = json.loads(_section(user, "HEADINGS")[0])
    rows = [json.loads(r) for r in _section(user, "ROWS")]
    date_column = _find(headings, "transaction date", "date") or headings[0]
    index = headings.index(date_column)
    samples = [r[index] for r in rows if len(r) > index and r[index]]
    date_format = next((f for f in DATE_FORMATS if all(_try(f, s) for s in samples)), "%d/%m/%Y")
    amount = _find(headings, "amount", "value")
    out = None if amount else _find(headings, "withdrawal", "paid out", "money out", "debit", "out")
    paid_in = None if amount else _find(headings, "deposit", "paid in", "money in", "credit", "in")
    return {
        "date_column": date_column,
        "date_format": date_format,
        "description_columns": [
            h
            for h in [_find(headings, "details", "description", "narrative", "memo", "payee")]
            if h
        ],
        "merchant_column": None,
        "amount_column": amount,
        "money_out_column": out,
        "money_in_column": paid_in,
        "amounts_are": "money_out_negative",
        "balance_column": _find(headings, "balance"),
        "category_column": _find(headings, "category"),
        "type_column": None,
    }


def _try(fmt: str, value: str) -> bool:
    try:
        datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True


class OracleLLM:
    """Has the same `structured()` as LLMClient, answered by the oracle. No network."""

    def structured(
        self,
        task: str,
        messages: Any,
        schema: Any,
        *,
        max_tokens: int = 4096,
        run: Any = None,
        run_id: str | None = None,
    ) -> Any:
        text = reply([{"role": m.role, "content": m.content} for m in messages])
        if run is not None:
            run.check_limits()
            run.start_call()
            run.record(len(text) // 4, 0.0)
        return schema.model_validate_json(text)
