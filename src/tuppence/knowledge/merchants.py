"""Merchants: who was paid, recognised however the statement prints it (spec §7).

`merchant_key()` turns any statement text into a stable key: "SQ *JS TRADING",
"CARD PAYMENT TO JS TRADING ON 05 OCT" and "JS Trading Ltd" all become "js trading".
The merchant row remembers its usual category (merchant memory) and the raw texts seen.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.records import NotFound
from tuppence.knowledge.models import BusinessType, MemoryState

# Card processors and payment platforms that put their own name before the merchant's.
PROCESSORS = frozenset(
    {
        "sq",
        "sqr",
        "square",
        "paypal",
        "pp",
        "crv",
        "sumup",
        "iz",
        "izettle",
        "zettle",
        "ztl",
        "sp",
        "tst",
        "gc",
        "gocardless",
        "stripe",
        "dojo",
        "wpy",
        "worldpay",
        "takepayments",
        "lsp",
        "clover",
        "yoyo",
        "ccl",
    }
)
# Words banks add in front of the payee.
_LEADING = (
    "card payment to",
    "card payment",
    "contactless payment to",
    "contactless payment",
    "direct debit payment to",
    "direct debit to",
    "direct debit",
    "dd payment to",
    "dd",
    "standing order to",
    "standing order",
    "so",
    "faster payment to",
    "faster payment",
    "faster payments",
    "fpo",
    "fpi",
    "bill payment to",
    "bill payment",
    "bp",
    "transfer to",
    "transfer from",
    "tfr",
    "pos",
    "vis",
    "visa",
    "debit card",
    "dc",
    "dpc",
    "online payment to",
    "online payment",
    "payment to",
    "paid to",
    "bgc",
    "bacs",
    "cnp",
    "contactless",
    "apple pay",
    "google pay",
    "clearpay",
)
_LEADING_RE = re.compile(r"^(?:(?:" + "|".join(re.escape(w) for w in _LEADING) + r")\s+)+")
_DOMAIN = re.compile(r"\.(?:co\.uk|org\.uk|com|net|org|io|uk)\b", re.IGNORECASE)
_DATE_TAIL = re.compile(
    r"\b(?:on\s+)?\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    r"(?:\s*\d{2,4})?\b.*$|\b(?:on\s+)?\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b.*$",
    re.IGNORECASE,
)
_REF_TAIL = re.compile(r"\b(?:ref|reference|mandate|card|a/c|acct|account)\b.*$", re.IGNORECASE)
_DIGITS = re.compile(r"\d")
_CARD_ENDING = re.compile(r"^[x*#]+\d+$")
_STORE_NUMBER = re.compile(r"^(?:#?\d{3,}|[a-z]{1,3}\d{3,}|\d+[a-z]{1,2}\d+)$")
_TRAILING = frozenset(
    {
        "gb",
        "gbr",
        "uk",
        "united",
        "kingdom",
        "england",
        "scotland",
        "wales",
        "ltd",
        "limited",
        "plc",
        "llp",
        "co",
        "inc",
        "contactless",
        "online",
        "www",
        "gbp",
        "cr",
        "dr",
        "dd",
        "so",
        "bgc",
        "fpo",
        "fpi",
        "tfr",
        "bp",
        "dpc",
        "pos",
        "vis",
        "cnp",
        "bacs",
        "london",
        "manchester",
        "birmingham",
        "leeds",
        "glasgow",
        "edinburgh",
        "liverpool",
        "bristol",
        "sheffield",
        "cardiff",
        "belfast",
        "newcastle",
        "nottingham",
        "leicester",
        "coventry",
        "bradford",
        "southampton",
        "portsmouth",
        "brighton",
        "plymouth",
        "reading",
        "derby",
        "york",
        "oxford",
        "cambridge",
        "norwich",
        "exeter",
        "swansea",
        "aberdeen",
        "dundee",
        "milton",
        "keynes",
        "luton",
        "bath",
        "hull",
        "stoke",
        "wolverhampton",
        "sunderland",
        "preston",
        "blackpool",
        "bournemouth",
        "ipswich",
        "peterborough",
    }
)


def clean_text(raw: str) -> str:
    """The statement text as one comparable string: upper case, single spaces."""
    return " ".join(raw.upper().split())


def variant_text(raw_description: str, merchant_text: str | None) -> str:
    """How a merchant's name was printed, with numbers blanked so references and dates
    don't make every payment a new variant: "SQ *JS TRADING 0873" → "SQ *JS TRADING #"."""
    return re.sub(r"\d+", "#", clean_text(merchant_text or raw_description))[:120]


def _holds_digits(token: str) -> bool:
    """A token that could be (part of) an account number, sort code or card ending."""
    return (
        len(_DIGITS.findall(token)) >= 4
        or bool(_CARD_ENDING.match(token))
        or bool(re.search(r"\d{2,}[-.]\d{2,}", token))
    )


def _without_details(tokens: list[str]) -> list[str]:
    """Drop what could keep an account number, sort code or card digits: tokens with long
    digit runs, card endings, hyphenated number groups, and runs of short number tokens
    ("20 00 00") that together make a long number."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) < 2 or sum(len(t) for t in run) < 5:
            out.extend(run)
        run.clear()

    for token in tokens:
        if _holds_digits(token):
            flush()
        elif token.isdigit():
            run.append(token)
        else:
            flush()
            out.append(token)
    flush()
    return out


def _strip(text: str) -> list[str]:
    text = _DOMAIN.sub(" ", text.casefold())
    if "*" in text:
        left, _, right = text.partition("*")
        left_word = re.sub(r"[^a-z0-9]", "", left)
        text = right if left_word in PROCESSORS and right.strip() else left
    text = _LEADING_RE.sub("", " ".join(re.sub(r"[^a-z0-9&'./ -]", " ", text).split()))
    text = _REF_TAIL.sub("", _DATE_TAIL.sub("", text))
    tokens = [t.strip("./-'") for t in re.split(r"[\s/]+", text)]
    tokens = _without_details([t for t in tokens if t])
    for i, token in enumerate(tokens[1:], start=1):
        if _STORE_NUMBER.match(token):
            tokens = tokens[:i]
            break
    while len(tokens) > 1 and (tokens[-1] in _TRAILING or tokens[-1].isdigit()):
        tokens.pop()
    return tokens


def merchant_key(text: str) -> str:
    """ "SQ *JS TRADING 0873 LONDON GB" → "js trading". Empty when no name is left."""
    key = " ".join(re.sub(r"[^a-z0-9& ]", "", t) for t in _strip(text)).strip()
    return key if re.search(r"[a-z]", key) else ""


def display_name(text: str) -> str:
    """A readable name: "GREENBASKET STORES 0873 LONDON" → "Greenbasket Stores"."""
    tokens = _strip(text)
    if not tokens:
        return re.sub(r"\d{4,}", "", " ".join(text.split()))[:60].strip()
    words = " ".join(tokens)
    return (words.title() if words == words.lower() else words)[:60]


class Merchant(BaseModel):
    id: str
    key: str
    name: str
    business_type: BusinessType | None = None
    business_type_source: str | None = None
    default_category_id: str | None = None
    default_who: str | None = None
    memory: MemoryState = "none"
    confidence: float = 0.0
    seen_count: int = 0
    evidence: dict[str, Any] = Field(default_factory=dict)
    version: int = 1


class MemoryGuess(BaseModel):
    category_id: str
    who: str | None
    confidence: float
    rows: int
    share: float


def infer_memory(
    decisions: Sequence[tuple[str, str | None, float, int]],
    *,
    min_rows: int = 3,
    min_share: float = 0.75,
) -> MemoryGuess | None:
    """A merchant's usual category from earlier decisions, or None when it isn't clear.

    `decisions` holds (category id, who, confidence, |amount| in pence) for rows the model
    or the person decided. The most frequent category wins (ties: more money); it needs at
    least `min_rows` rows and `min_share` of them."""
    if len(decisions) < min_rows:
        return None
    counts: dict[str, list[float]] = {}
    for category, _, confidence, pence in decisions:
        bucket = counts.setdefault(category, [0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += pence
        bucket[2] += confidence
    best = min(counts, key=lambda c: (-counts[c][0], -counts[c][1], c))
    n, _, total_conf = counts[best]
    share = n / len(decisions)
    if share < min_share:
        return None
    whos = [w for c, w, _, _ in decisions if c == best and w]
    who = max(sorted(set(whos)), key=whos.count) if whos else None
    return MemoryGuess(
        category_id=best,
        who=who,
        confidence=round(share * total_conf / n, 3),
        rows=int(n),
        share=round(share, 3),
    )


def _merchant(row: sqlite3.Row) -> Merchant:
    data = dict(row)
    data["evidence"] = json.loads(data["evidence"] or "{}")
    return Merchant.model_validate(data)


class MerchantStore:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db, self.clock = db, clock

    def get(self, merchant_id: str) -> Merchant:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM merchant WHERE id = ?", [merchant_id]).fetchone()
        if row is None:
            raise NotFound("merchant", merchant_id)
        return _merchant(row)

    @staticmethod
    def many_in(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, Merchant]:
        rows = conn.execute(
            "SELECT * FROM merchant WHERE id IN (SELECT value FROM json_each(?))",
            [json.dumps(sorted(set(ids)))],
        )
        return {row["id"]: _merchant(row) for row in rows}

    def resolve(
        self, conn: sqlite3.Connection, raw_description: str, merchant_text: str | None
    ) -> Merchant | None:
        """The merchant for this statement text, created on first sight. None when the text
        has no usable name (for example only a reference number)."""
        text = variant_text(raw_description, merchant_text)
        row = conn.execute(
            "SELECT m.* FROM merchant_variant v JOIN merchant m ON m.id = v.merchant_id"
            " WHERE v.text = ?",
            [text],
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE merchant_variant SET seen_count = seen_count + 1 WHERE text = ?", [text]
            )
            return _merchant(row)
        source = merchant_text or raw_description
        key = merchant_key(source)
        if not key:
            source = raw_description
            key = merchant_key(source)
        if not key:
            return None
        now = to_iso(self.clock())
        found = conn.execute("SELECT * FROM merchant WHERE key = ?", [key]).fetchone()
        if found is None:
            merchant_id = "m_" + secrets.token_hex(6)
            conn.execute(
                "INSERT INTO merchant (id, key, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [merchant_id, key, display_name(source), now, now],
            )
            found = conn.execute("SELECT * FROM merchant WHERE id = ?", [merchant_id]).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO merchant_variant (text, merchant_id) VALUES (?, ?)",
            [text, found["id"]],
        )
        return _merchant(found)

    def remember(
        self, conn: sqlite3.Connection, merchant_id: str, guess: MemoryGuess, *, seen_count: int
    ) -> bool:
        """Store inferred memory. Returns True when the usual category or person changed.
        Confirmed memory is the person's (or research's) and is never touched here."""
        row = conn.execute("SELECT * FROM merchant WHERE id = ?", [merchant_id]).fetchone()
        if row is None or row["memory"] == "confirmed":
            return False
        changed = (row["default_category_id"], row["default_who"]) != (guess.category_id, guess.who)
        conn.execute(
            "UPDATE merchant SET memory = 'inferred', default_category_id = ?, default_who = ?,"
            " confidence = ?, seen_count = ?, evidence = ?, updated_at = ?,"
            " version = version + ? WHERE id = ?",
            [
                guess.category_id,
                guess.who,
                guess.confidence,
                seen_count,
                json.dumps({"rows": guess.rows, "share": guess.share}),
                to_iso(self.clock()),
                int(changed),
                merchant_id,
            ],
        )
        return changed

    def confirm_memory(
        self,
        conn: sqlite3.Connection,
        merchant_id: str,
        *,
        category_id: str,
        who: str | None = None,
        source: str = "person",
    ) -> None:
        """Confirmed memory (spec §10.2: below rules, above inferred memory). M5's Researcher
        confirms merchants this way; nothing in M4's UI does."""
        conn.execute(
            "UPDATE merchant SET memory = 'confirmed', default_category_id = ?, default_who = ?,"
            " confidence = 1.0, evidence = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            [
                category_id,
                who,
                json.dumps({"confirmed_by": source}),
                to_iso(self.clock()),
                merchant_id,
            ],
        )

    def set_business_type(
        self, conn: sqlite3.Connection, merchant_id: str, kind: BusinessType, source: str
    ) -> None:
        conn.execute(
            "UPDATE merchant SET business_type = ?, business_type_source = ?, updated_at = ?"
            " WHERE id = ? AND (business_type_source IS NULL OR business_type_source != 'user'"
            " OR ? = 'user')",
            [kind, source, to_iso(self.clock()), merchant_id, source],
        )
