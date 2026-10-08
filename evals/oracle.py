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
CATEGORISE_MARKER = "TUPPENCE-CATEGORISE-V1"
REVIEW_MARKER = "TUPPENCE-REVIEW-V1"
REFILE_MARKER = "TUPPENCE-REFILE-V1"
LABELS_MARKER = "TUPPENCE-COMMITMENT-LABELS-V1"
MARKERS = (
    READ_MARKER,
    MAPPING_MARKER,
    CATEGORISE_MARKER,
    REVIEW_MARKER,
    REFILE_MARKER,
    LABELS_MARKER,
)
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DATE = re.compile(
    rf"\b(\d{{1,2}}/\d{{1,2}}/\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}"
    rf"|\d{{1,2}} (?:{_MONTHS})[a-z]*(?: \d{{4}})?)\b",
    re.IGNORECASE,
)
_MONEY = re.compile(
    r"(?<![\w.,])([+\-−]?£?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?: ?CR)?)(?![\d])"
)  # grouped or not ("1,250.00", "1250.00"), as a model reads it
_REF = re.compile(r"^((?:P\d+)?L\d+|D\d+): (.*)$")
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


def reply(messages: list[dict[str, Any]]) -> str:
    """The text a model would send back for these chat messages."""
    text = "\n".join(str(m.get("content", "")) for m in messages)
    user = next(
        (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), ""
    )
    if MAPPING_MARKER in text:  # a JSON-repair turn has no sketch of its own: use the last one
        sketches = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
        return json.dumps(mapping(next((c for c in reversed(sketches) if "HEADINGS:" in c), user)))
    if READ_MARKER in text:
        return json.dumps(read(user))
    if CATEGORISE_MARKER in text:
        return json.dumps(categorise(_last_with(messages, "TRANSACTIONS:", user)))
    if REVIEW_MARKER in text:
        return json.dumps(categorise(_last_with(messages, "TRANSACTIONS:", user), review=True))
    if REFILE_MARKER in text:
        return json.dumps({"subcategories": []})  # the oracle never splits a category
    if LABELS_MARKER in text:
        return json.dumps(labels(_last_with(messages, "PAYMENTS:", user)))
    return json.dumps({"note": "oracle has no answer for this prompt"})


def _last_with(messages: list[dict[str, Any]], heading: str, default: str) -> str:
    """The last user message holding the section (a JSON-repair turn adds a bare nudge after it)."""
    found = [str(m.get("content", "")) for m in messages if m.get("role") == "user"]
    return next((c for c in reversed(found) if heading in c), default)


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
    """Column roles from the heading names (the request carries no cell values)."""
    headings: list[str] = json.loads(_section(user, "HEADINGS")[0])
    date_column = _find(headings, "transaction date", "date") or headings[0]
    amount = _find(headings, "amount", "value")
    out = None if amount else _find(headings, "withdrawal", "paid out", "money out", "debit", "out")
    paid_in = None if amount else _find(headings, "deposit", "paid in", "money in", "credit", "in")
    return {
        "date_column": date_column,
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


# --- understanding (M4) -----------------------------------------------------------------
# Words in the synthetic corpus's merchant names, and the category each one means. The
# oracle exists to run the pipeline without a model; its accuracy says nothing about a
# real model's.
KEYWORDS: list[tuple[str, str]] = [
    ("payroll", "income.salary"),
    ("child benefit", "income.benefits"),
    ("lettings", "housing.rent"),
    ("council tax", "housing.council-tax"),
    ("water", "housing.water"),
    ("energy", "housing.energy"),
    ("broadband", "housing.broadband"),
    ("tv licensing", "housing.tv-licence"),
    ("home cover", "housing.insurance"),
    ("window cleaning", "housing.repairs"),
    ("fuel", "transport.car.fuel"),
    ("dvla", "transport.car.road-tax"),
    ("car insurance", "transport.car.insurance"),
    ("roadstar finance", "transport.car.finance"),
    ("roadside", "transport.car.breakdown"),
    ("rail", "transport.public"),
    ("greenbasket", "food.groceries"),
    ("valuemart", "food.groceries"),
    ("cafe", "food.eating-out"),
    ("pizza", "food.takeaway"),
    ("nursery", "children.childcare"),
    ("swim club", "children.activities"),
    ("pharmacy", "health.pharmacy"),
    ("gym", "health.fitness"),
    ("streamly", "subscriptions.tv-streaming"),
    ("tunewave", "subscriptions.music"),
    ("melodia", "subscriptions.music"),
    ("cloudbox", "subscriptions.software"),
    ("news digital", "subscriptions.news"),
    ("mobile", "subscriptions.mobile"),
    ("pet insurance", "pets.insurance"),
    ("lifeshield", "financial.protection"),
    ("books", "entertainment.hobbies"),
    ("cinema", "entertainment.going-out"),
    ("flights", "holidays.travel"),
    ("payment received", "transfers.card-repayment"),
    ("example card", "transfers.card-repayment"),
    ("savings", "transfers.between-accounts"),
    ("cash machine", "transfers.cash"),
]
LABELS: list[tuple[str, str]] = [
    ("subscriptions.", "subscription"),
    ("health.fitness", "subscription"),
    ("children.activities", "subscription"),
    ("transport.car.finance", "instalment"),
    ("financial.loan-repayments", "instalment"),
    ("housing.", "bill"),
    ("transport.car.", "bill"),
    ("children.childcare", "bill"),
    ("pets.insurance", "bill"),
    ("financial.protection", "bill"),
]


def _known(category: str, allowed: set[str]) -> str | None:
    """The category, or its deepest ancestor the prompt's (possibly shallow) tree lists."""
    parts = category.split(".")
    for n in range(len(parts), 0, -1):
        candidate = ".".join(parts[:n])
        if candidate in allowed:
            return candidate
    return None


def categorise(user: str, *, review: bool = False) -> dict[str, Any]:
    allowed = {ln.strip().split(" — ")[0] for ln in _section(user, "CATEGORIES")}
    memory: dict[str, str] = {}
    for ln in _section(user, "MEMORY"):
        match = re.match(r"^- (.+): ([a-z0-9.-]+) \(seen", ln)
        if match:
            memory[match.group(1).casefold()] = match.group(2)
    answers = []
    for ln in _section(user, "TRANSACTIONS"):
        row = json.loads(ln)
        text = f"{row.get('merchant') or ''} {row.get('description') or ''}".casefold()
        found = memory.get((row.get("merchant") or "").casefold())
        if found is None:
            found = next((cat for word, cat in KEYWORDS if word in text), None)
        category = _known(found, allowed) if found else None
        confidence = 0.95 if category else 0.4
        if category is None:
            money_in = not str(row.get("amount", "")).startswith("-")
            fallback = "income.other" if money_in else "other"
            category = row.get("given_category_id") or _known(fallback, allowed) or fallback
            confidence = 0.5 if review else 0.4
        answers.append(
            {
                "ref": row["ref"],
                "category_id": category,
                "who": "household",
                "confidence": confidence,
                "reason": "oracle keyword table",
            }
        )
    return {"transactions": answers}


def labels(user: str) -> dict[str, Any]:
    out = []
    for ln in _section(user, "PAYMENTS"):
        row = json.loads(ln)
        category = str(row.get("category_id") or "")
        kind = next((k for prefix, k in LABELS if category.startswith(prefix)), "none")
        out.append({"ref": row["ref"], "kind": kind})
    return {"payments": out}
