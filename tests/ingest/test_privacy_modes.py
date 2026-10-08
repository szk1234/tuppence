"""End-to-end privacy over an adversarial synthetic corpus, every format, in every mode: a local
or a cloud model, Local only, Pseudonymise on or off, and scans read by a vision model. Every
outbound request is captured; none may carry an identity detail, an address line, a balance or
limit, and the privacy log may hold none of them either."""

import json
import unicodedata

import httpx
import pytest

from ingest import adversarial as corpus
from ingest.conftest import FIXTURES, oracle_handler
from ingest.helpers import add_account, cloud_model, drain, use_local_model
from tuppence.core.household import PersonIn

VISION_MARKER = "TUPPENCE-VISION-OCR-V1"


def files() -> dict[str, bytes]:
    current = corpus.current_pdf()
    return {
        "current.pdf": current,
        "card.pdf": corpus.card_pdf(),
        "scan.pdf": corpus.scanned(current),
        "shot.png": corpus.screenshot_png(),
        "layout.csv": corpus.CSV.encode(),
        "layout.xlsx": corpus.statement_xlsx(),
        "current.ofx": (FIXTURES / "ofx" / "current.ofx").read_bytes(),
        "bank.qif": (FIXTURES / "qif" / "bank.qif").read_bytes(),
        "statement.xml": (FIXTURES / "camt" / "statement.xml").read_bytes(),
    }


def user_texts(requests) -> list[str]:
    """Everything a request carries but the fixed system prompt (images aren't text)."""
    out: list[str] = []
    for body in requests:
        for message in body.get("messages", []):
            if message.get("role") == "system":
                continue
            content = message.get("content")
            if isinstance(content, list):
                out += [part.get("text", "") for part in content if part.get("type") == "text"]
            else:
                out.append(str(content))
    return out


def folded(text: str) -> str:
    """Captured text as a reader sees it: compatibility forms folded, invisible characters
    gone, every run of spaces one space. A detail hidden by such characters is still found."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.split())


def vision_handler(scripted):
    """Answers vision calls with what a vision model would read off each page (the header
    included), and everything else with the oracle."""
    base = oracle_handler(scripted)
    pages = {"pdf": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if VISION_MARKER not in json.dumps(body):
            return base(request)
        if "image/png" in json.dumps(body):
            lines = corpus.VISION_SCREENSHOT
        else:
            lines = corpus.VISION_PAGES[pages["pdf"] % len(corpus.VISION_PAGES)]
            pages["pdf"] += 1
        scripted.requests.append({"vision": True, "messages": []})
        content = json.dumps({"lines": lines})
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    return handle


MODES = ["local", "local+pseudonymise", "local+local-only", "cloud", "cloud+pseudonymise",
         "local+vision", "cloud+vision"]  # fmt: skip


@pytest.mark.parametrize("mode", MODES)
def test_nothing_private_is_sent_in_any_mode(ingest_env, mode):
    services, scripted = ingest_env
    for name in corpus.NAMES:
        services.household.create_person(PersonIn(display_name=name, role="adult"))
    if mode.startswith("cloud"):
        cloud_model(services, acknowledge=True)
    else:
        use_local_model(services)
    if "pseudonymise" in mode:
        services.settings.set("privacy.pseudonymise", True, expected_version=0)
    if "local-only" in mode:
        services.settings.set("privacy.local_only", True, expected_version=0)
    if "vision" in mode:
        with services.db.transaction() as conn:
            conn.execute("UPDATE llm_model SET supports_vision = 1")
        services.settings.set("ingest.vision_for_scans", True, expected_version=0)
        scripted.handler = vision_handler(scripted)
    current = add_account(services, "other", "current", "Probe current", last4="5678")
    card = add_account(services, "other", "credit_card", "Probe card", last4="4242")
    statuses = {}
    for name, data in files().items():
        out = services.ingest.upload(name, data)
        drain(services)
        record = services.statements.get(out.record.id)
        if record.status == "needs_account":
            target = card if "card" in name else current
            services.ingest.answer_account(
                record.id, account_id=target.id, expected_version=record.version
            )
            drain(services)
            record = services.statements.get(out.record.id)
        statuses[name] = record.status
    assert set(statuses.values()) <= {"imported", "needs_review"}, statuses
    assert statuses["current.pdf"] == "imported"  # the rows with account details were read
    sent = folded("\n".join(user_texts(r for r in scripted.requests if not r.get("vision"))))
    assert sent  # the PDFs, the screenshot and the layouts were read by the model
    assert [s for s in corpus.SECRETS if s in sent] == []
    log = json.dumps([entry.model_dump() for entry in services.privacy_log.list(limit=500)])
    assert [s for s in corpus.SECRETS if s in log] == []


@pytest.mark.parametrize("mode", ["local", "local+vision"])
def test_every_line_a_model_receives_comes_from_prepare_outbound(ingest_env, monkeypatch, mode):
    """No send path builds outbound text itself: each statement line in a read request is a
    `sensitive.prepare_outbound` result, and each heading in a layout sketch is one (or
    hidden), its cells only type tokens."""
    import re

    from tuppence.ingest import sensitive

    services, scripted = ingest_env
    for name in corpus.NAMES:
        services.household.create_person(PersonIn(display_name=name, role="adult"))
    use_local_model(services)
    if mode.endswith("vision"):
        with services.db.transaction() as conn:
            conn.execute("UPDATE llm_model SET supports_vision = 1")
        services.settings.set("ingest.vision_for_scans", True, expected_version=0)
        scripted.handler = vision_handler(scripted)
    prepared: set[str] = set()
    real = sensitive.prepare_outbound

    def spy(text, *, names=(), **near):
        out = real(text, names=names, **near)
        if out is not None:
            prepared.add(out.text)
        return out

    monkeypatch.setattr(sensitive, "prepare_outbound", spy)
    current = add_account(services, "other", "current", "Probe current", last4="5678")
    card = add_account(services, "other", "credit_card", "Probe card", last4="4242")
    for name, data in files().items():
        out = services.ingest.upload(name, data)
        drain(services)
        record = services.statements.get(out.record.id)
        if record.status == "needs_account":
            target = card if "card" in name else current
            services.ingest.answer_account(
                record.id, account_id=target.id, expected_version=record.version
            )
            drain(services)
    lines = headings = 0
    for text in user_texts(r for r in scripted.requests if not r.get("vision")):
        base = text.split("\n\nYour previous answer")[0]
        if "HEADINGS:" in base:
            shown = json.loads(base.split("HEADINGS:\n", 1)[1].split("\n", 1)[0])
            allowed = {sensitive.HIDDEN, ""} | {re.sub(r"\d{4,}", "<NUM>", t) for t in prepared}
            assert all(h in allowed for h in shown), shown
            headings += len(shown)
            for row in base.split("ROWS:\n", 1)[1].splitlines():
                assert all(re.fullmatch(r"<[A-Z]+(?::[^<>]*)?>", c) for c in json.loads(row))
        for match in re.finditer(r"^D\d+: (.*)$", base, re.MULTILINE):
            assert match.group(1) in prepared, match.group(1)
            lines += 1
    assert lines > 20 and headings > 5
