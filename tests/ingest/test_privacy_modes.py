"""End-to-end privacy over an adversarial synthetic corpus, every format, in every mode: a local
or a cloud model, Local only, Pseudonymise on or off, and scans read by a vision model. Every
outbound request is captured; none may carry an identity detail, an address line, a balance or
limit, and the privacy log may hold none of them either."""

import json

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
    sent = "\n".join(user_texts(r for r in scripted.requests if not r.get("vision")))
    assert sent  # the PDFs, the screenshot and the layouts were read by the model
    assert [s for s in corpus.SECRETS if s in sent] == []
    log = json.dumps([entry.model_dump() for entry in services.privacy_log.list(limit=500)])
    assert [s for s in corpus.SECRETS if s in log] == []
