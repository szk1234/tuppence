"""The "Why?" panel (spec §13 Spending): how Tuppence decided what a transaction is."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from pydantic import BaseModel

from tuppence.core.money import format_pounds
from tuppence.knowledge.categories import CategoryTree
from tuppence.knowledge.models import HistoryEntry, Understanding
from tuppence.knowledge.versions import STALE_SQL

STATUS_LABELS = {
    "unknown": "Not sorted yet",
    "guessed": "Best guess",
    "inferred": "Sorted",
    "confirmed": "You set this",
}
DECIDED_LABELS = {
    "rule": "A rule",
    "memory": "What Tuppence knows about this merchant",
    "research": "A merchant lookup",
    "llm": "The AI",
    "review": "The AI, on a second look",
    "human": "You",
}
WAITING_LABELS = {
    "queued": "Queued to be looked at again on the next run.",
    "deferred": "Put off to the next run (this run reached its AI budget, or the AI's answer"
    " couldn't be used).",
    "awaiting_ai": "Waiting for an AI model. Choose one in Settings › AI.",
}


class Why(BaseModel):
    transaction_id: str
    status: str
    status_label: str
    decided_by: str | None
    decided_by_label: str | None
    confidence: float
    category_path: list[str]
    steps: list[str]
    rule: dict[str, Any] | None
    merchant: dict[str, Any] | None
    knowledge_version: int
    current_knowledge_version: int
    stale: bool
    history: list[dict[str, Any]]
    version: int


def _uk(iso: str) -> str:
    return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")


def explain(
    conn: sqlite3.Connection,
    tree: CategoryTree,
    u: Understanding,
    history: list[HistoryEntry],
    current_version: int,
) -> Why:
    path = [c.label for c in tree.path(u.category_id)] if u.category_id else []
    steps: list[str] = []
    rule = None
    if u.rule_id:
        r = conn.execute(
            "SELECT id, description, source FROM rule WHERE id = ?", [u.rule_id]
        ).fetchone()
        if r is not None:
            rule = dict(r)
            who = "Tuppence's built-in rule" if r["source"] == "seed" else "your rule"
            steps.append(f"Matched {who} “{r['description']}”.")
    merchant = None
    if u.merchant_id:
        m = conn.execute(
            "SELECT id, name, default_category_id, memory, seen_count FROM merchant WHERE id = ?",
            [u.merchant_id],
        ).fetchone()
        if m is not None:
            usual = (
                " › ".join(c.label for c in tree.path(m["default_category_id"]))
                if m["default_category_id"]
                else None
            )
            merchant = {
                "id": m["id"],
                "name": m["name"],
                "usual_category": usual,
                "memory": m["memory"],
                "seen_count": m["seen_count"],
            }
    ev = u.evidence
    if u.decided_by == "memory" and merchant:
        seen = f"Tuppence has seen {merchant['name']} {merchant['seen_count']} times"
        if merchant["usual_category"]:
            steps.append(f"{seen} and it's usually {merchant['usual_category']}.")
        else:
            steps.append(f"{seen}.")
    if u.decided_by in ("llm", "review"):
        sure = f"{round(u.confidence * 100)}% sure"
        lead = "The AI took a second look" if u.decided_by == "review" else "The AI chose this"
        steps.append(f"{lead} ({sure}): {ev.get('reason') or 'no reason given'}.")
    if ev.get("kind") == "transfer_pair" and u.transfer_pair_id:
        other = conn.execute(
            'SELECT t.date, t.amount_pence, a.nickname FROM "transaction" t'
            " JOIN account a ON a.id = t.account_id WHERE t.id = ?",
            [u.transfer_pair_id],
        ).fetchone()
        if other is not None:
            matched = (
                f"Matched with £{format_pounds(abs(other['amount_pence']))} on"
                f" {other['nickname']} on {_uk(other['date'])}"
            )
            if ev.get("words"):
                steps.append(f"{matched}: money moving between your accounts.")
            else:  # equal amounts alone: as likely a coincidence (a refund, a friend)
                steps.append(
                    f"{matched}: the same amount, but neither description says it's a"
                    " transfer, so this is only a guess. If it isn't one, choose what it is."
                )
    if ev.get("kind") == "transfer_one_sided":
        steps.append(
            "The description names another of your accounts, whose statement for"
            " these dates isn't in Tuppence yet."
        )
    if ev.get("refiled_from"):
        steps.append(
            "Moved into a new sub-category when its category got crowded (you can undo this"
            " on the Spending page, under “Sub-categories Tuppence added”)."
        )
    if u.decided_by == "human" and history:
        steps.append(f"You set this on {_uk(history[0].created_at)}.")
    if u.waiting:
        steps.append(WAITING_LABELS[u.waiting])
    stale = (
        bool(
            conn.execute(
                f"SELECT 1 FROM understanding u WHERE u.transaction_id = ? AND {STALE_SQL}",  # noqa: S608
                [u.transaction_id],
            ).fetchone()
        )
        and u.status != "confirmed"
    )
    if stale:
        steps.append(
            "Something Tuppence knows has changed since this was decided, so it will"
            " look again on the next run."
        )
    if not steps:
        steps.append("Tuppence hasn't looked at this yet.")
    return Why(
        transaction_id=u.transaction_id,
        status=u.status,
        status_label=STATUS_LABELS[u.status],
        decided_by=u.decided_by,
        decided_by_label=DECIDED_LABELS.get(u.decided_by) if u.decided_by else None,
        confidence=u.confidence,
        category_path=path,
        steps=steps,
        rule=rule,
        merchant=merchant,
        knowledge_version=u.knowledge_version,
        current_knowledge_version=current_version,
        stale=stale,
        history=[
            {
                "when": _uk(h.created_at),
                "who": "You"
                if h.changed_by == "person"
                else DECIDED_LABELS.get(h.decided_by or "", h.changed_by),
                "category": " › ".join(c.label for c in tree.path(h.category_id))
                if h.category_id
                else "Not sorted",
                "reason": h.reason,
            }
            for h in history
        ],
        version=u.version,
    )
