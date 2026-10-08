"""Statements: upload, progress, the account question and the fix-up screen (spec §6, §13)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from tuppence.app.deps import get_services
from tuppence.app.routes.accounts import _input_error
from tuppence.app.services import Services
from tuppence.core.accounts import AccountIn
from tuppence.core.errors import InputError
from tuppence.core.money import format_pounds, parse_pounds
from tuppence.core.records import NotFound, VersionConflict
from tuppence.ingest.check import base_ref
from tuppence.ingest.models import Document, ParsedStatement, SkippedLine
from tuppence.ingest.service import RowEdit, clean_filename
from tuppence.ingest.sniff import UploadRejected
from tuppence.ingest.store import FINISHED, STATUS_LABELS, StatementRecord

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/statements", tags=["statements"])
MAX_FILES = 20
MAX_REQUEST_MB = 100
READ_CHUNK = 1024 * 1024


class Counts(BaseModel):
    rows: int = 0
    new: int = 0
    duplicates: int = 0
    skipped: int = 0


class QuestionView(BaseModel):
    text: str
    reason: str
    best_guess: str | None
    candidates: list[str]
    prefill: dict[str, str | None]


class StatementView(BaseModel):
    id: str
    filename: str
    format: str
    status: str
    status_label: str
    account_id: str | None
    account_name: str | None
    provider: str | None
    period_start: dt.date | None
    period_end: dt.date | None
    opening_balance: str | None
    closing_balance: str | None
    balance_verified: bool
    counts: Counts
    question: QuestionView | None
    error: str | None
    warnings: list[str]
    importer: str | None
    created_at: str
    version: int
    duplicate: bool = False


class DraftRowView(BaseModel):
    ref: str
    date: dt.date
    amount: str
    description: str
    balance_after: str | None
    edited: bool
    errors: list[str]


class TransactionView(BaseModel):
    id: str
    date: dt.date
    amount: str
    description: str
    balance_after: str | None


class HeldLine(BaseModel):
    ref: str
    text: str


class StatementDetail(StatementView):
    level: str
    check_errors: list[str]  # the ones not about a single row
    draft_rows: list[DraftRowView]
    draft_skipped: list[SkippedLine]
    held_lines: list[HeldLine]  # held back from the AI; still to be decided
    transactions: list[TransactionView]


class Rejected(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    statements: list[StatementView]
    rejected: list[Rejected]


class AccountAnswer(BaseModel):
    account_id: str | None = None
    new_account: dict[str, Any] | None = None  # the fields of AccountIn (M2)
    expected_version: int


class DraftRowIn(BaseModel):
    ref: str
    date: dt.date
    amount: str  # pounds, e.g. "-42.18"
    description: str = Field(min_length=1, max_length=300)


class DraftSkipIn(BaseModel):
    ref: str
    reason: str = Field(min_length=1, max_length=200)


class DraftIn(BaseModel):
    rows: list[DraftRowIn]
    skipped: list[DraftSkipIn]
    expected_version: int


class VersionIn(BaseModel):
    expected_version: int


def _money(pence: int | None) -> str | None:
    return None if pence is None else format_pounds(pence)


def _account_name(services: Services, account_id: str | None) -> str | None:
    if account_id is None:
        return None
    try:
        account = services.accounts.get(account_id)
    except NotFound:
        return None
    return f"{account.nickname} ({account.provider_name})"


def _view(services: Services, record: StatementRecord, *, duplicate: bool = False) -> StatementView:
    persist = record.stats.get("persist", {})
    if record.status == "needs_review" and record.draft:
        parsed = record.draft["parsed"]
        counts = Counts(rows=len(parsed["rows"]), skipped=len(parsed["skipped"]))
    else:
        counts = Counts(
            rows=persist.get("rows", 0),
            new=persist.get("inserted", 0),
            duplicates=persist.get("duplicates_exact", 0) + persist.get("duplicates_similar", 0),
            skipped=persist.get("skipped", 0),
        )
    return StatementView(
        id=record.id,
        filename=record.original_filename,
        format=record.format,
        status=record.status,
        status_label=STATUS_LABELS[record.status],
        account_id=record.account_id,
        account_name=_account_name(services, record.account_id),
        provider=record.provider,
        period_start=record.period_start,
        period_end=record.period_end,
        opening_balance=_money(record.opening_balance_pence),
        closing_balance=_money(record.closing_balance_pence),
        balance_verified=record.balance_verified,
        counts=counts,
        question=QuestionView.model_validate(record.question) if record.question else None,
        error=record.error,
        warnings=list(record.stats.get("warnings", [])),
        importer=record.importer,
        created_at=record.created_at,
        version=record.version,
        duplicate=duplicate,
    )


def _detail(services: Services, record: StatementRecord) -> StatementDetail:
    level, general = "full", list(record.check_errors)
    rows: list[DraftRowView] = []
    skipped: list[SkippedLine] = []
    held: list[HeldLine] = []
    if record.draft:
        parsed = ParsedStatement.model_validate(record.draft["parsed"])
        level = record.draft.get("level", "full")
        refs = {base_ref(r.ref) for r in parsed.rows}
        by_ref: dict[str, list[str]] = {}
        general = []
        for error in record.check_errors:
            ref = base_ref(error.split(":", 1)[0])
            if ref in refs:
                by_ref.setdefault(ref, []).append(error)
            else:
                general.append(error)
        rows = [
            DraftRowView(
                ref=r.ref,
                date=r.date,
                amount=format_pounds(r.amount_pence),
                description=r.raw_description,
                balance_after=_money(r.balance_after_pence),
                edited=r.edited,
                errors=by_ref.get(base_ref(r.ref), []),
            )
            for r in parsed.rows
        ]
        skipped = parsed.skipped
        document = Document.model_validate(record.draft["document"])
        decided = {r.ref for r in parsed.rows} | {x.ref for x in parsed.skipped}
        lines = document.by_ref()
        held = [
            HeldLine(ref=ref, text=lines[ref].text)
            for ref in document.held_amount_refs
            if ref not in decided and ref in lines
        ]
    transactions = [
        TransactionView(
            id=t.id,
            date=t.date,
            amount=format_pounds(t.amount_pence),
            description=t.raw_description,
            balance_after=_money(t.balance_after_pence),
        )
        for t in services.statements.transactions(record.id)
    ]
    return StatementDetail(
        **_view(services, record).model_dump(),
        level=level,
        check_errors=general,
        draft_rows=rows,
        draft_skipped=skipped,
        held_lines=held,
        transactions=transactions,
    )


def check_length(value: str | None) -> None:
    """Refuse a request too big to be statements before reading any of it."""
    if value is None:
        raise HTTPException(
            411, "The upload's size is unknown. Try again from the Statements page."
        )
    try:
        size = int(value)
    except ValueError:
        raise HTTPException(400, "That upload couldn't be read.") from None
    if size > MAX_REQUEST_MB * 1024 * 1024:
        raise HTTPException(
            413, f"That's too much at once. Upload at most {MAX_REQUEST_MB} MB in one go."
        )


async def _read_limited(upload: UploadFile, limit: int) -> bytes | None:
    parts: list[bytes] = []
    total = 0
    while chunk := await upload.read(READ_CHUNK):
        total += len(chunk)
        if total > limit:
            return None
        parts.append(chunk)
    return b"".join(parts)


@router.post("", status_code=201)
async def upload_statements(request: Request, services: Svc) -> UploadResponse:
    check_length(request.headers.get("content-length"))
    try:
        form = await request.form(max_files=MAX_FILES, max_fields=10)
    except (MultiPartException, StarletteHTTPException):
        raise HTTPException(
            400, f"That upload couldn't be read. Upload at most {MAX_FILES} files at a time."
        ) from None
    try:
        files = [v for k, v in form.multi_items() if k == "files" and isinstance(v, UploadFile)]
        if not files:
            raise HTTPException(400, "Choose at least one file to upload.")
        limit_mb: int = services.settings.get("ingest.max_file_mb")
        accepted: list[StatementView] = []
        rejected: list[Rejected] = []
        for upload in files:
            name = clean_filename(upload.filename)
            data = await _read_limited(upload, limit_mb * 1024 * 1024)
            if data is None:
                rejected.append(
                    Rejected(filename=name, reason=f"Files can be at most {limit_mb} MB.")
                )
                continue
            try:
                outcome = await run_in_threadpool(services.ingest.upload, name, data)
            except UploadRejected as exc:
                rejected.append(Rejected(filename=name, reason=str(exc)))
                continue
            accepted.append(_view(services, outcome.record, duplicate=outcome.duplicate))
    finally:
        await form.close()
    return UploadResponse(statements=accepted, rejected=rejected)


@router.get("")
def list_statements(services: Svc) -> dict[str, list[StatementView]]:
    return {"statements": [_view(services, r) for r in services.statements.list()]}


@router.get("/{statement_id}")
def get_statement(statement_id: str, services: Svc) -> StatementDetail:
    return _detail(services, services.statements.get(statement_id))


def _chosen_account(body: AccountAnswer, services: Svc) -> str:
    """The answer's account id, adding the new account first when that's what was chosen."""
    if body.new_account is not None:
        try:
            return services.accounts.create(AccountIn.model_validate(body.new_account)).id
        except ValidationError as exc:
            raise _input_error(exc) from None
    assert body.account_id is not None
    return body.account_id


def _check_answer(body: AccountAnswer) -> None:
    if (body.account_id is None) == (body.new_account is None):
        raise InputError("Choose one of your accounts, or add a new one.")
    if body.new_account is not None:  # fields are checked before anything is created
        try:
            AccountIn.model_validate(body.new_account)
        except ValidationError as exc:
            raise _input_error(exc) from None


@router.post("/{statement_id}/account")
def answer_account(statement_id: str, body: AccountAnswer, services: Svc) -> StatementView:
    _check_answer(body)
    record = services.statements.get(statement_id)
    if record.status != "needs_account":
        raise InputError("This statement isn't waiting for an answer any more.")
    if record.version != body.expected_version:
        raise VersionConflict("statement", statement_id, body.expected_version, record.version)
    account_id = _chosen_account(body, services)
    record = services.ingest.answer_account(
        statement_id, account_id=account_id, expected_version=body.expected_version
    )
    return _view(services, record)


@router.post("/{statement_id}/change-account")
def change_account(statement_id: str, body: AccountAnswer, services: Svc) -> StatementView:
    """ "Wrong account?": read the file again as another account's. It may cost another AI read."""
    _check_answer(body)
    record = services.statements.get(statement_id)
    if record.status not in (*FINISHED, "needs_account"):
        raise InputError(
            "This statement is still being read. Change its account once it has finished."
        )
    if record.version != body.expected_version:
        raise VersionConflict("statement", statement_id, body.expected_version, record.version)
    account_id = _chosen_account(body, services)
    record = services.ingest.change_account(
        statement_id, account_id=account_id, expected_version=body.expected_version
    )
    return _view(services, record)


@router.put("/{statement_id}/draft")
def save_draft(statement_id: str, body: DraftIn, services: Svc) -> StatementDetail:
    rows = [
        RowEdit(
            ref=r.ref,
            date=r.date,
            amount_pence=parse_pounds(r.amount, allow_negative=True),
            description=r.description,
        )
        for r in body.rows
    ]
    skipped = [SkippedLine(ref=s.ref, reason=s.reason) for s in body.skipped]
    record = services.ingest.save_draft(
        statement_id, rows=rows, skipped=skipped, expected_version=body.expected_version
    )
    return _detail(services, record)


@router.post("/{statement_id}/accept")
def accept(statement_id: str, body: VersionIn, services: Svc) -> StatementView:
    return _view(
        services, services.ingest.accept(statement_id, expected_version=body.expected_version)
    )


@router.post("/{statement_id}/retry")
def retry(statement_id: str, body: VersionIn, services: Svc) -> StatementView:
    return _view(
        services, services.ingest.retry(statement_id, expected_version=body.expected_version)
    )


@router.delete("/{statement_id}", status_code=204)
def delete(statement_id: str, services: Svc) -> Response:
    services.ingest.delete(statement_id)
    return Response(status_code=204)
