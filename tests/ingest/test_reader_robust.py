"""A model reply can be wrong in any way; the read ends as a plain statement error."""

import json

import pytest

from ingest.helpers import budget, parse_pages, use_local_model
from ingest.replies import read, reply, row
from ingest.statements import HEAD, TABLE
from tuppence.ingest.textprep import pages_document

PAGE = [
    *HEAD,
    TABLE,
    "01/10/2026 GREENBASKET STORES 42.18 957.82",
    "03/10/2026 LITTLE CAFE 3.40 954.42",
]

BAD_REPLIES = {
    "amount as text": reply(
        [dict(row("D2", "42.18"), amount="£42.18"), row("D3", "3.40")],
        [{"ref": "D1", "reason": "headings"}],
    ),
    "missing fields": json.dumps({"statement": {}, "transactions": [], "skipped": []}),
    "invented and duplicated refs": reply(
        [row("D2", "42.18"), row("D2", "42.18"), row("D77", "1.00")], [{"ref": "D1", "reason": "h"}]
    ),
    "a bool amount": reply([dict(row("D2", "42.18"), amount=True)]),
    "nested junk": '{"statement": {"period_start": [1]}, "transactions": [{"ref": {"a": 1}}]}',
    "a huge number": reply([row("D2", "42.18")]).replace("-1.0", "-1e400", 1),
    "not json": "I am sorry, I cannot do that",
}


@pytest.mark.parametrize("name", list(BAD_REPLIES))
def test_bad_replies_are_retried_then_reported(ingest_env, name):
    services, scripted = ingest_env
    use_local_model(services)
    scripted.replies = [{"content": BAD_REPLIES[name]}] * 12
    doc = pages_document([PAGE], sha256="x", kind="pdf")
    out = read(services, doc, parallel=1)
    assert not out.ok and out.errors
    assert len(scripted.requests) <= 6  # 3 attempts, each with at most one repair call
    assert all(len(e) <= 200 for e in out.errors)


def test_an_all_skipped_read_needs_review(ingest_env):
    services, scripted = ingest_env
    skipped = [{"ref": f"D{i}", "reason": "not a transaction"} for i in (1, 2, 3)]
    scripted.replies = [{"content": reply([], skipped)}]
    out, _ = parse_pages(services, [PAGE])
    assert out.parsed.rows == []
    assert "No transactions were read from this file, so it needs a look." in out.errors


def test_an_empty_screenshot_read_needs_review(ingest_env, fixtures):
    import datetime as dt

    from tuppence.ingest.extract import ExtractLimits, extract_document
    from tuppence.ingest.identify import identify
    from tuppence.ingest.parse import ReaderLimits, parse_document
    from tuppence.ingest.registry import LayoutRegistry, load_bank_pack

    services, scripted = ingest_env
    window = use_local_model(services)
    pack = load_bank_pack()
    registry = LayoutRegistry(pack)
    shot = extract_document(
        fixtures / "image" / "app-screenshot.png", "image", sha256="x", limits=ExtractLimits()
    )
    evidence = identify(shot, pack=pack, registry=registry, key=b"k")
    scripted.replies = [{"content": reply([], [])}]
    out = parse_document(
        shot,
        fixtures / "image" / "app-screenshot.png",
        evidence,
        "current",
        registry=registry,
        llm=services.llm,
        run=budget(),
        context_window=window,
        today=dt.date(2026, 11, 1),
        limits=ReaderLimits(),
    )
    assert out.parsed.rows == [] and any("No transactions were read" in e for e in out.errors)


def test_model_strings_are_cleaned_and_capped_wherever_they_are_kept(ingest_env):
    # M8: the whole-file check after the read quoted the model's text in full.
    from ingest.helpers import parse_pages
    from ingest.replies import reply, row

    services, scripted = ingest_env
    nasty = "Paid\x1b[31m out " + "x" * 5000
    rows = [
        {**row("D2", "1.00", sign_from=nasty), "raw_desc": "\x07" + "y" * 3000},
        {**row("D3", "\x07" + "9" * 3000), "merchant": "\x1b" * 400, "bank_type": "z" * 999},
    ]
    skipped = [{"ref": "D1", "reason": "\x00" + "r" * 900}]
    scripted.replies = [{"content": reply(rows, skipped)}] * 3
    page = [
        "Statement period 01/10/2026 to 31/10/2026",
        "Date Description Paid out Paid in Balance",
        "01/10/2026 SHOP 1.00 99.00",
        "02/10/2026 CAFE 2.00 97.00",
    ]
    out, _ = parse_pages(services, [page])
    assert out.errors

    def clean(text):
        return text is None or (len(text) <= 200 and not any(ord(c) < 32 for c in text))

    assert all(clean(e) for e in out.errors)
    for r in out.parsed.rows:
        fields = (r.amount_text, r.sign_from, r.raw_description, r.merchant, r.bank_type)
        assert all(clean(f) for f in fields)
    assert all(clean(s.reason) for s in out.parsed.skipped)
