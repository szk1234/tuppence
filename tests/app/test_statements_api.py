import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from tuppence.app.routes.statements import check_length

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "statements"


def drain(client):
    services = client.app.state.services
    while services.worker.run_once():
        pass


def person(client) -> str:
    r = client.post("/api/household/people", json={"display_name": "Alex Example", "role": "adult"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def account(client, owner, provider, kind, nickname, last4=None) -> dict:
    body = {
        "provider": provider,
        "kind": kind,
        "nickname": nickname,
        "last4": last4,
        "owner_ids": [owner],
    }
    r = client.post("/api/accounts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def files(*relative: str):
    return [
        ("files", (Path(p).name, (FIXTURES / p).read_bytes(), "application/octet-stream"))
        for p in relative
    ]


def by_name(client) -> dict[str, dict]:
    return {s["filename"]: s for s in client.get("/api/statements").json()["statements"]}


def test_sign_in_is_required(anon_client):
    assert anon_client.get("/api/statements").status_code == 401


def test_upload_progress_and_the_imported_transactions(client):
    owner = person(client)
    monzo = account(client, owner, "monzo", "current", "Monzo")
    r = client.post("/api/statements", files=files("csv/monzo.csv", "qif/bank.qif"))
    assert r.status_code == 201 and r.json()["rejected"] == []
    assert [s["status_label"] for s in r.json()["statements"]] == ["Waiting to be read"] * 2
    drain(client)
    listed = by_name(client)
    assert listed["monzo.csv"]["status_label"] == "Imported"
    assert listed["monzo.csv"]["account_name"] == "Monzo (Monzo)"
    assert listed["monzo.csv"]["counts"] == {"rows": 9, "new": 9, "duplicates": 0, "skipped": 1}
    assert listed["bank.qif"]["status"] == "needs_account"
    assert listed["bank.qif"]["question"]["text"] == "Which account is this?"
    assert monzo["id"] in listed["bank.qif"]["question"]["candidates"]
    detail = client.get(f"/api/statements/{listed['monzo.csv']['id']}").json()
    first = detail["transactions"][0]
    assert (first["date"], first["amount"], first["description"]) == (
        "2026-10-01",
        "-42.18",
        "Greenbasket Stores",
    )
    again = client.post("/api/statements", files=files("csv/monzo.csv")).json()
    assert (
        again["statements"][0]["duplicate"]
        and again["statements"][0]["id"] == listed["monzo.csv"]["id"]
    )


def test_answering_with_a_new_account(client):
    owner = person(client)
    account(client, owner, "monzo", "current", "Monzo")
    client.post("/api/statements", files=files("qif/bank.qif"))
    drain(client)
    asked = by_name(client)["bank.qif"]
    url = f"/api/statements/{asked['id']}/account"
    stale = client.post(
        url,
        json={
            "account_id": asked["question"]["candidates"][0],
            "expected_version": asked["version"] - 1,
        },
    )
    assert stale.status_code == 409
    new_account = {
        "provider": "nationwide",
        "kind": "savings",
        "nickname": "Rainy day",
        "owner_ids": [owner],
    }
    r = client.post(url, json={"new_account": new_account, "expected_version": asked["version"]})
    assert r.status_code == 200 and r.json()["status_label"] == "Reading transactions"
    drain(client)
    done = client.get(f"/api/statements/{asked['id']}").json()
    assert done["status"] == "imported" and done["account_name"] == "Rainy day (Nationwide)"
    late = client.post(
        url, json={"account_id": done["account_id"], "expected_version": done["version"]}
    )
    assert late.status_code == 422 and "isn't waiting" in late.json()["detail"]


def test_the_fix_up_screen(client):
    owner = person(client)
    account(client, owner, "starling", "current", "Starling")
    typo = (FIXTURES / "csv" / "starling.csv").read_text().replace("-48.20,909.62", "-48.02,909.62")
    client.post("/api/statements", files=[("files", ("starling.csv", typo.encode(), "text/csv"))])
    drain(client)
    sid = by_name(client)["starling.csv"]["id"]
    detail = client.get(f"/api/statements/{sid}").json()
    assert detail["status_label"] == "Needs your check" and len(detail["draft_rows"]) == 9
    third = next(r for r in detail["draft_rows"] if r["ref"] == "L3")
    assert third["amount"] == "-48.02" and any("running balance" in e for e in third["errors"])
    assert any(e.startswith("balance mismatch") for e in detail["check_errors"])
    rows = [
        {
            "ref": r["ref"],
            "date": r["date"],
            "amount": "-48.20" if r["ref"] == "L3" else r["amount"],
            "description": r["description"],
        }
        for r in detail["draft_rows"]
    ]
    saved = client.put(
        f"/api/statements/{sid}/draft",
        json={
            "rows": rows,
            "skipped": detail["draft_skipped"],
            "expected_version": detail["version"],
        },
    )
    assert saved.status_code == 200 and saved.json()["check_errors"] == []
    assert all(r["errors"] == [] for r in saved.json()["draft_rows"])
    bad = client.put(
        f"/api/statements/{sid}/draft",
        json={
            "rows": [{**rows[0], "amount": "12.345"}],
            "skipped": [],
            "expected_version": saved.json()["version"],
        },
    )
    assert bad.status_code == 422
    done = client.post(
        f"/api/statements/{sid}/accept", json={"expected_version": saved.json()["version"]}
    )
    assert done.status_code == 200 and done.json()["status"] == "imported"


def zip_bomb() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", "0" * (5 * 1024 * 1024))
    return buf.getvalue()


def test_hostile_and_oversized_uploads(client):
    owner = person(client)
    account(client, owner, "monzo", "current", "Monzo")
    r = client.patch("/api/settings/ingest.max_file_mb", json={"value": 1, "expected_version": 0})
    assert r.status_code == 200
    big = b"Date,Description,Amount\n" + b"01/10/2026,Shop,-1.00\n" * 60_000
    upload = [
        ("files", ("photo.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64, "image/heic")),
        ("files", ("statement.csv.exe", b"MZ\x90\x00\x03" + b"\x00" * 64, "text/csv")),
        ("files", ("statement.xlsx", zip_bomb(), "application/octet-stream")),
        ("files", ("big.csv", big, "text/csv")),
        *files("csv/monzo.csv"),
    ]
    r = client.post("/api/statements", files=upload)
    assert r.status_code == 201
    assert [s["filename"] for s in r.json()["statements"]] == ["monzo.csv"]
    reasons = {x["filename"]: x["reason"] for x in r.json()["rejected"]}
    assert "HEIC" in reasons["photo.heic"]
    assert "doesn't look like a statement" in reasons["statement.csv.exe"]
    assert "unpacks to far more data" in reasons["statement.xlsx"]
    assert reasons["big.csv"] == "Files can be at most 1 MB."
    many = [
        ("files", (f"f{i}.csv", b"Date,Amount\n01/10/2026,-1.00\n", "text/csv")) for i in range(21)
    ]
    too_many = client.post("/api/statements", files=many)
    assert too_many.status_code == 400 and "at most 20 files" in too_many.json()["detail"]
    assert client.post("/api/statements", data={"note": "x"}).status_code == 400
    assert len(by_name(client)) == 1  # nothing else was stored
    drain(client)
    assert by_name(client)["monzo.csv"]["status"] == "imported"  # the app kept working


@pytest.mark.parametrize("value,status", [(None, 411), ("abc", 400), (str(101 * 1024 * 1024), 413)])
def test_request_size_guard(value, status):
    with pytest.raises(HTTPException) as exc:
        check_length(value)
    assert exc.value.status_code == status


def test_retry_and_remove(client):
    owner = person(client)
    account(client, owner, "nationwide", "current", "Nationwide", last4="5678")
    client.post("/api/statements", files=files("pdf/current-text.pdf"))
    drain(client)
    failed = by_name(client)["current-text.pdf"]
    assert failed["status_label"] == "Couldn't import" and "Settings › AI" in failed["error"]
    r = client.post(
        f"/api/statements/{failed['id']}/retry", json={"expected_version": failed["version"]}
    )
    assert r.status_code == 200 and r.json()["status"] == "received"
    assert client.delete(f"/api/statements/{failed['id']}").status_code == 204
    assert client.get(f"/api/statements/{failed['id']}").status_code == 404


def test_a_closed_account_is_refused_in_the_answer(client):
    owner = person(client)
    account(client, owner, "monzo", "current", "Monzo")
    old = account(client, owner, "nationwide", "savings", "Old savings")
    client.post("/api/statements", files=files("qif/bank.qif"))
    drain(client)
    asked = by_name(client)["bank.qif"]
    closed = client.post(
        f"/api/accounts/{old['id']}/close", json={"expected_version": old["version"]}
    )
    assert closed.status_code == 200, closed.text
    r = client.post(
        f"/api/statements/{asked['id']}/account",
        json={"account_id": old["id"], "expected_version": asked["version"]},
    )
    assert r.status_code == 422 and "open accounts" in r.json()["detail"]
    both = client.post(
        f"/api/statements/{asked['id']}/account",
        json={"expected_version": asked["version"]},
    )
    assert both.status_code == 422
    bad = client.post(
        f"/api/statements/{asked['id']}/account",
        json={
            "new_account": {"provider": "nationwide", "kind": "savings", "nickname": "x" * 60},
            "expected_version": asked["version"],
        },
    )
    assert bad.status_code == 422 and "40 characters" in bad.json()["detail"]


def test_unreadable_multipart_is_a_plain_400(client):
    r = client.post(
        "/api/statements",
        content=b"not multipart at all",
        headers={"Content-Type": "multipart/form-data; boundary=zzz"},
    )
    assert r.status_code == 400
    assert "Traceback" not in r.text


def held_back(client):
    import datetime as dt

    from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement, SkippedLine

    services = client.app.state.services
    owner = person(client)
    acct = account(client, owner, "monzo", "current", "Monzo")
    record = services.statements.create(
        sha256="d" * 64, ext="pdf", filename="october.pdf", kind="pdf"
    )
    doc = Document(
        kind="pdf",
        sha256="d" * 64,
        lines=[
            Line(ref="P1L1", text="Date Description Paid out Paid in Balance"),
            Line(ref="P1L2", text="02 Oct 2026 Greenbasket Stores 42.18 957.82"),
            Line(ref="P2L1", text="Little Cafe 3.40 954.42"),
        ],
        data_refs=["P1L1", "P1L2"],
        preamble_refs=["P2L1"],
        held_amount_refs=["P2L1"],
        pages=2,
    )
    parsed = ParsedStatement(
        importer="ai-read",
        period_start=dt.date(2026, 10, 1),
        period_end=dt.date(2026, 10, 31),
        opening_balance_pence=100000,
        closing_balance_pence=95442,
        rows=[
            ParsedRow(
                ref="P1L2",
                date=dt.date(2026, 10, 2),
                amount_pence=-4218,
                amount_text="42.18",
                sign_from="Paid out",
                raw_description="Greenbasket Stores",
                balance_after_pence=95782,
            )
        ],
        skipped=[SkippedLine(ref="P1L1", reason="column headings")],
    )
    services.statements.update(
        record.id,
        status="needs_review",
        account_id=acct["id"],
        check_errors=["A line of this statement with an amount on it was held back"],
        draft={
            "document": doc.model_dump(mode="json"),
            "parsed": parsed.model_dump(mode="json"),
            "level": "full",
            "pending_layout": None,
            "proposed_signs": {},
        },
    )
    return record.id, acct


def test_held_back_lines_are_shown_and_can_become_rows(client):
    sid, _ = held_back(client)
    detail = client.get(f"/api/statements/{sid}").json()
    assert detail["held_lines"] == [{"ref": "P2L1", "text": "Little Cafe 3.40 954.42"}]
    rows = [
        {k: r[k] for k in ("ref", "date", "amount", "description")} for r in detail["draft_rows"]
    ]
    rows.append(
        {"ref": "P2L1", "date": "2026-10-03", "amount": "-3.40", "description": "Little Cafe"}
    )
    saved = client.put(
        f"/api/statements/{sid}/draft",
        json={
            "rows": rows,
            "skipped": detail["draft_skipped"],
            "expected_version": detail["version"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["held_lines"] == []
    assert len(saved.json()["draft_rows"]) == 2
    assert not any("held back" in e for e in saved.json()["check_errors"])


def test_wrong_account_rereads_the_file(client):
    sid, acct = held_back(client)
    other = account(client, acct["owner_ids"][0], "nationwide", "current", "Other")
    detail = client.get(f"/api/statements/{sid}").json()
    r = client.post(
        f"/api/statements/{sid}/change-account",
        json={"account_id": other["id"], "expected_version": detail["version"] - 1},
    )
    assert r.status_code == 409
    r = client.post(
        f"/api/statements/{sid}/change-account",
        json={"account_id": other["id"], "expected_version": detail["version"]},
    )
    assert r.status_code == 200 and r.json()["status"] == "received"


def test_members_work_on_statements_but_not_on_vision_settings(client):
    from fastapi.testclient import TestClient

    client.app.state.services.users.create("member", "another-long-password", is_admin=False)
    m = TestClient(client.app)
    r = m.post("/api/auth/login", json={"username": "member", "password": "another-long-password"})
    m.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    denied = m.patch(
        "/api/settings/ingest.vision_for_scans", json={"value": True, "expected_version": 0}
    )
    assert denied.status_code == 403
    up = m.post("/api/statements", files=files("csv/monzo.csv"))
    assert up.status_code == 201
    assert len(m.get("/api/statements").json()["statements"]) == 1


def _needs_review(client) -> dict:
    owner = person(client)
    account(client, owner, "starling", "current", "Starling")
    typo = (FIXTURES / "csv" / "starling.csv").read_text().replace("-48.20,909.62", "-48.02,909.62")
    client.post("/api/statements", files=[("files", ("starling.csv", typo.encode(), "text/csv"))])
    drain(client)
    sid = by_name(client)["starling.csv"]["id"]
    return client.get(f"/api/statements/{sid}").json()


@pytest.mark.parametrize("ref", ["", "x" * 41])
def test_a_bad_draft_ref_is_a_plain_422_not_a_500(client, ref):
    """M1."""
    detail = _needs_review(client)
    for body in (
        {"rows": [{"ref": ref, "date": "2026-10-01", "amount": "1.00", "description": "x"}],
         "skipped": [], "expected_version": detail["version"]},
        {"rows": [], "skipped": [{"ref": ref, "reason": "x"}],
         "expected_version": detail["version"]},
    ):  # fmt: skip
        r = client.put(f"/api/statements/{detail['id']}/draft", json=body)
        assert r.status_code == 422 and "ref" in r.text, r.text


def test_draft_lists_are_capped(client):
    detail = _needs_review(client)
    row = {"ref": "L2", "date": "2026-10-01", "amount": "1.00", "description": "x"}
    body = {"rows": [row] * 5001, "skipped": [], "expected_version": detail["version"]}
    assert client.put(f"/api/statements/{detail['id']}/draft", json=body).status_code == 422
    body = {
        "rows": [],
        "skipped": [{"ref": "L2", "reason": "x"}] * 5001,
        "expected_version": detail["version"],
    }
    assert client.put(f"/api/statements/{detail['id']}/draft", json=body).status_code == 422


def test_a_statement_with_more_than_five_thousand_rows_can_be_edited(client):
    """m6: the fix-up screen sends every row back; a long statement in review could be
    imported as it was but not edited (422). Now its own size is the limit."""
    import datetime as dt

    from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement

    detail = _needs_review(client)
    services = client.app.state.services
    record = services.statements.get(detail["id"])
    n = 6_000
    lines = [Line(ref=f"L{i}", text=f"02/10/2026 Shop {i} 1.00") for i in range(1, n + 1)]
    rows = [
        ParsedRow(ref=f"L{i}", date=dt.date(2026, 10, 2), amount_pence=-100, amount_text="1.00",
                  raw_description=f"Shop {i}")
        for i in range(1, n + 1)
    ]  # fmt: skip
    doc = Document(kind="text", sha256="e" * 64, lines=lines, data_refs=[ln.ref for ln in lines])
    parsed = ParsedStatement(importer="ai-read", rows=rows)
    services.statements.update(
        record.id,
        draft={**record.draft, "document": doc.model_dump(mode="json"),
               "parsed": parsed.model_dump(mode="json"), "basis": None},
    )  # fmt: skip
    detail = client.get(f"/api/statements/{record.id}").json()
    assert len(detail["draft_rows"]) == n
    body = {
        "rows": [
            {"ref": r["ref"], "date": r["date"], "amount": r["amount"], "description": "x"}
            for r in detail["draft_rows"]
        ],
        "skipped": [],
        "expected_version": detail["version"],
    }
    saved = client.put(f"/api/statements/{record.id}/draft", json=body)
    assert saved.status_code == 200, saved.text[:300]
    assert len(saved.json()["draft_rows"]) == n
    # more lines than the statement has is still refused, plainly
    body["rows"] = body["rows"] + body["rows"][:1]
    body["expected_version"] = saved.json()["version"]
    refused = client.put(f"/api/statements/{record.id}/draft", json=body)
    assert refused.status_code == 422


def test_empty_descriptions_and_skip_reasons_from_the_reader_can_be_saved(client):
    """M2: the reader left a description and a skip reason empty. The fix-up screen sends the
    draft back as it got it, and that saves."""
    from tuppence.ingest.models import ParsedStatement, SkippedLine

    detail = _needs_review(client)
    services = client.app.state.services
    record = services.statements.get(detail["id"])
    parsed = ParsedStatement.model_validate(record.draft["parsed"])
    skipped = parsed.rows.pop()  # the reader skipped a line and gave no reason
    parsed.skipped.append(SkippedLine(ref=skipped.ref, reason=""))
    services.statements.update(
        record.id, draft={**record.draft, "parsed": parsed.model_dump(mode="json")}
    )
    detail = client.get(f"/api/statements/{detail['id']}").json()
    rows = [
        {"ref": r["ref"], "date": r["date"], "amount": r["amount"],
         "description": "" if r["ref"] == "L2" else r["description"]}
        for r in detail["draft_rows"]
    ]  # fmt: skip
    r = client.put(
        f"/api/statements/{detail['id']}/draft",
        json={
            "rows": rows,
            "skipped": detail["draft_skipped"],
            "expected_version": detail["version"],
        },
    )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert all(s["reason"] for s in saved["draft_skipped"])
    assert next(row for row in saved["draft_rows"] if row["ref"] == "L2")["description"]


def test_a_long_statement_lists_all_its_transactions_or_says_how_many_more(client):
    """M4: no silent cap on the transactions shown."""
    owner = person(client)
    account(client, owner, "monzo", "current", "Monzo")
    header = (FIXTURES / "csv" / "monzo.csv").read_text().splitlines()[0]
    lines = [header] + [
        f"tx_{i:05d},{1 + i % 28:02d}/10/2026,12:00:00,Card payment,Shop {i},,Groceries,"
        "-1.00,GBP,-1.00,GBP,,,,,,-1.00,"
        for i in range(700)
    ]
    client.post("/api/statements", files=[("files", ("long.csv", ("\n".join(lines) + "\n").encode(),
                                                     "text/csv"))])  # fmt: skip
    drain(client)
    sid = by_name(client)["long.csv"]["id"]
    detail = client.get(f"/api/statements/{sid}").json()
    assert detail["status"] == "imported", detail["check_errors"]
    assert detail["transactions_total"] == 700 and len(detail["transactions"]) == 700
