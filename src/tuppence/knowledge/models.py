"""Shapes shared by the knowledge store and the specialists (spec §7). Plain data, no I/O."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["unknown", "guessed", "inferred", "confirmed"]
DecidedBy = Literal["rule", "memory", "research", "llm", "review", "human"]
Waiting = Literal["queued", "deferred", "awaiting_ai"]
CategoryKind = Literal["spend", "income", "transfer"]
CategorySource = Literal["seed", "user", "agent"]
MemoryState = Literal["none", "inferred", "confirmed"]
BusinessType = Literal["bill", "subscription", "instalment", "none"]
VersionKind = Literal["rule", "merchant", "category", "correction"]
HOUSEHOLD = "household"  # `who` for spending that is for everyone


class Category(BaseModel):
    id: str
    parent_id: str | None
    level: int
    label: str
    kind: CategoryKind
    essential: bool
    source: CategorySource
    retired: bool
    sort_order: int = 0
    version: int = 1


class Understanding(BaseModel):
    transaction_id: str
    merchant_id: str | None = None
    category_id: str | None = None
    who: str | None = None
    is_transfer: bool = False
    transfer_pair_id: str | None = None
    ignored: bool = False
    status: Status = "unknown"
    confidence: float = 0.0
    decided_by: DecidedBy | None = None
    authority: int = 0
    rule_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    knowledge_version: int = 0
    reviewed_at: str | None = None
    waiting: Waiting | None = None
    version: int = 1
    updated_at: str = ""


class Decision(BaseModel):
    """What a specialist (or the person) wants an understanding row to say.

    `None` means "leave this field as it is"."""

    decided_by: DecidedBy
    authority: int
    status: Status
    confidence: float = Field(ge=0, le=1)
    category_id: str | None = None
    who: str | None = None
    merchant_id: str | None = None
    is_transfer: bool | None = None
    transfer_pair_id: str | None = None
    ignored: bool | None = None
    rule_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class HistoryEntry(BaseModel):
    row_version: int
    category_id: str | None
    who: str | None
    is_transfer: bool
    ignored: bool
    status: Status
    confidence: float
    decided_by: DecidedBy | None
    rule_id: str | None
    evidence: dict[str, Any]
    knowledge_version: int
    changed_by: str
    run_id: str | None
    reason: str
    created_at: str
