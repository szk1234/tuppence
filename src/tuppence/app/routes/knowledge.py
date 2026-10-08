"""Categories, rules and the sub-category log (spec §7, §13)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError

from tuppence.app.deps import get_services
from tuppence.app.routes._ids import BodyId, ExpectedVersion, PathId
from tuppence.app.services import Services
from tuppence.core.errors import InputError
from tuppence.core.money import format_pounds, parse_pounds
from tuppence.core.records import NotFound
from tuppence.knowledge.models import HOUSEHOLD, Category, CategoryKind
from tuppence.knowledge.rules import Rule, RuleIn, RulePreview

Svc = Annotated[Services, Depends(get_services)]
router = APIRouter(prefix="/api", tags=["knowledge"])


class CategoryIn(BaseModel):
    parent_id: BodyId | None = None
    label: str = Field(min_length=1, max_length=40)
    kind: CategoryKind | None = None
    essential: bool | None = None


class CategoryPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=40)
    essential: bool | None = None
    expected_version: ExpectedVersion


class VersionIn(BaseModel):
    expected_version: ExpectedVersion


class RuleBody(BaseModel):
    """A rule as the Spending page sends it: amounts in pounds."""

    merchant_id: BodyId | None = None
    text_pattern: str | None = Field(default=None, max_length=100)
    min_amount: str | None = Field(default=None, max_length=32)
    max_amount: str | None = Field(default=None, max_length=32)
    account_id: BodyId | None = None
    direction: Literal["in", "out"] | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    person_id: BodyId | None = None
    set_category_id: BodyId | None = None
    set_who: BodyId | None = None
    set_transfer: bool | None = None
    set_ignore: bool = False


class RuleCreate(RuleBody):
    apply_to_past: bool = True
    created_from_transaction_id: BodyId | None = None


class RuleView(BaseModel):
    id: str
    description: str
    source: str
    enabled: bool
    hit_count: int
    version: int
    merchant_id: str | None
    set_category_id: str | None
    min_amount: str | None
    max_amount: str | None


def rule_in(body: RuleBody) -> RuleIn:
    try:
        return RuleIn(
            **body.model_dump(exclude={"min_amount", "max_amount"}),
            min_amount_pence=parse_pounds(body.min_amount) if body.min_amount else None,
            max_amount_pence=parse_pounds(body.max_amount) if body.max_amount else None,
        )
    except ValidationError as exc:
        raise InputError(str(exc.errors()[0]["msg"]).removeprefix("Value error, ")) from None


def check_references(services: Services, body: RuleBody) -> None:
    """Every id the rule names must exist (a plain 422 otherwise, never a database error)."""
    checks = [
        (body.merchant_id, services.merchants.get),
        (body.account_id, services.accounts.get),
        (body.person_id, services.household.get_person),
    ]
    for value, lookup in checks:
        if value is not None:
            try:
                lookup(value)
            except NotFound:
                raise InputError("Choose a merchant, account and people that exist.") from None
    if body.set_category_id is not None and not services.categories.tree().usable(
        body.set_category_id
    ):
        raise InputError("Choose a category from the list.")
    if body.set_who is not None and body.set_who != HOUSEHOLD:
        try:
            services.household.get_person(body.set_who)
        except NotFound:
            raise InputError("Choose someone from your household.") from None


def rule_view(rule: Rule) -> RuleView:
    return RuleView(
        id=rule.id,
        description=rule.description,
        source=rule.source,
        enabled=rule.enabled,
        hit_count=rule.hit_count,
        version=rule.version,
        merchant_id=rule.merchant_id,
        set_category_id=rule.set_category_id,
        min_amount=format_pounds(rule.min_amount_pence) if rule.min_amount_pence else None,
        max_amount=format_pounds(rule.max_amount_pence) if rule.max_amount_pence else None,
    )


@router.get("/categories")
def list_categories(services: Svc, include_retired: bool = False) -> dict[str, list[Category]]:
    tree = services.categories.tree()
    out: list[Category] = []

    def walk(parent: str | None) -> None:
        for c in tree.children(parent, include_retired=include_retired):
            out.append(c)
            walk(c.id)

    walk(None)
    return {"categories": out}


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, services: Svc) -> Category:
    created = services.categories.create(
        parent_id=body.parent_id, label=body.label, kind=body.kind, essential=body.essential
    )
    services.analysis.request("category_changed")
    return created


@router.patch("/categories/{category_id}")
def update_category(category_id: PathId, body: CategoryPatch, services: Svc) -> Category:
    return services.categories.update(
        category_id, body.expected_version, label=body.label, essential=body.essential
    )


@router.post("/categories/{category_id}/retire")
def retire_category(category_id: PathId, body: VersionIn, services: Svc) -> dict[str, list[str]]:
    retired = services.categories.retire(category_id, body.expected_version)
    services.analysis.request("category_changed")
    return {"retired": retired}


@router.get("/categories/refiles")
def list_refiles(services: Svc) -> dict[str, list[dict[str, object]]]:
    tree = services.categories.tree()
    return {
        "refiles": [
            {
                "id": r.id,
                "parent": tree.by_id[r.parent_id].label
                if r.parent_id in tree.by_id
                else r.parent_id,
                "created": [tree.by_id[c].label for c in r.created_ids if c in tree.by_id],
                "moved": len(r.moves),
                "created_at": r.created_at,
            }
            for r in services.refiles.list()
        ]
    }


@router.post("/categories/refiles/{refile_id}/undo")
def undo_refile(refile_id: PathId, services: Svc) -> dict[str, int]:
    moved = services.refiles.undo(refile_id)
    services.analysis.request("category_changed")
    return {"moved": moved}


@router.get("/rules")
def list_rules(services: Svc, include_disabled: bool = False) -> dict[str, list[RuleView]]:
    return {"rules": [rule_view(r) for r in services.rules.list(include_disabled=include_disabled)]}


@router.post("/rules/preview")
def preview_rule(body: RuleBody, services: Svc) -> RulePreview:
    check_references(services, body)
    return services.rules.preview(rule_in(body))


@router.post("/rules", status_code=201)
def create_rule(body: RuleCreate, services: Svc) -> dict[str, object]:
    check_references(services, body)
    if body.created_from_transaction_id is not None:
        services.understanding.get(body.created_from_transaction_id)  # NotFound -> 404
    rule, changed = services.rules.create(
        rule_in(body),
        apply_to_past=body.apply_to_past,
        created_from_transaction_id=body.created_from_transaction_id,
    )
    services.analysis.request("rule_changed")
    return {"rule": rule_view(rule), "changed": changed}


@router.post("/rules/{rule_id}/disable")
def disable_rule(rule_id: PathId, body: VersionIn, services: Svc) -> dict[str, object]:
    rule, released = services.rules.disable(rule_id, body.expected_version)
    services.analysis.request("rule_changed")
    return {"rule": rule_view(rule), "released": released}
