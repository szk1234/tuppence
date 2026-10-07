"""Scripted model replies and a reader call for the ingestion tests."""

import datetime as dt
import json

from ingest.helpers import budget
from tuppence.ingest.identify import HeaderFacts
from tuppence.ingest.reader import read_document

TODAY = dt.date(2026, 11, 1)


def row(ref, amount_text="1.00", sign_from=None, amount=-1.0, running=None):
    return {
        "ref": ref,
        "date": "2026-10-01",
        "amount": amount,
        "amount_text": amount_text,
        "sign_from": sign_from,
        "raw_desc": "x",
        "merchant": None,
        "bank_category": None,
        "bank_type": None,
        "running_balance": running,
    }


def reply(rows, skipped=(), currency="GBP"):
    return json.dumps(
        {
            "statement": {
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
                "opening_balance": None,
                "closing_balance": None,
                "currency": currency,
            },
            "transactions": rows,
            "skipped": list(skipped),
        }
    )


def read(services, doc, window=None, **kw):
    window = window or services.router.chain_for("read")[0][1].context_window
    return read_document(
        doc,
        llm=services.llm,
        run=kw.pop("run", None) or budget(),
        perspective="household",
        level="full",
        account="current account",
        facts=HeaderFacts(),
        today=TODAY,
        context_window=window,
        **kw,
    )
