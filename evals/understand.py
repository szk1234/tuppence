"""Score a model on understanding a synthetic household (spec §1.2, §16 M4 exit).

    uv run python -m evals.understand --model oracle [--months 12] [--seed 7]
    uv run python -m evals.understand --model "<connection>/<model id>" [--data-dir DIR]
        [--out evals/results/understanding-<name>.json] [--require-targets]
    uv run python -m evals.understand --table [--write README.md]

Runs the whole M4 analysis (rules, memory, the model in batches, review, transfers and
commitments) on a fresh database holding one generated household, then reports how many
transactions landed in the right top-level and level-2 category, how much is still unknown,
and which commitments were found. Targets (spec §1.2): 90% top level and 75% level 2 with
a local model; 95% top level with a cloud model.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from evals.household import Household, generate
from evals.oracle import OracleLLM
from tuppence.agents.analysis import AnalysisDeps, AnalysisGraph
from tuppence.agents.categoriser import Categoriser, CategoriserDeps, PersonRef
from tuppence.agents.commitments import Commitments, CommitmentsDeps
from tuppence.agents.runtime import AnalysisContext, LayeredBudget
from tuppence.agents.transfers import TransferMatcher
from tuppence.config.service import ConfigService
from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.core.settings_store import SettingsStore
from tuppence.knowledge.categories import CategoryStore, CategoryTree
from tuppence.knowledge.commitments import CommitmentStore
from tuppence.knowledge.merchants import MerchantStore, merchant_key
from tuppence.knowledge.refiles import RefileStore
from tuppence.knowledge.rules import RuleStore
from tuppence.knowledge.understanding import UnderstandingStore
from tuppence.knowledge.versions import KnowledgeVersions
from tuppence.llm.budget import RunBudget

RESULTS = Path(__file__).resolve().parent / "results"
START, END = "<!-- understanding-table:start -->", "<!-- understanding-table:end -->"
TARGET_L1, TARGET_L2 = 0.90, 0.75
ACCOUNTS = {"current": "current", "card": "credit_card", "savings": "savings"}


def counting() -> RunBudget:
    """Counts usage for the report; generous limits, as the eval sets no caps of its own."""
    return RunBudget(max_calls=10**6, max_tokens=10**9, max_gbp=10**6, max_seconds=10**6)


def load(db: Database, h: Household) -> dict[str, str]:
    """Write the household into a migrated database. Returns transaction id → true category."""
    truth: dict[str, str] = {}
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO person (id, display_name, role, created_at, updated_at)"
            " VALUES ('p_alex', 'Alex Example', 'adult', 'x', 'x')"
        )
        for name, kind in ACCOUNTS.items():
            conn.execute(
                "INSERT INTO account (id, provider, provider_name, kind, nickname, created_at,"
                " updated_at) VALUES (?, 'other', 'Example Bank', ?, ?, 'x', 'x')",
                [f"a_{name}", kind, {"savings": "Rainy Day"}.get(name, name.title())],
            )
            conn.execute(
                "INSERT INTO account_owner (account_id, person_id) VALUES (?, 'p_alex')",
                [f"a_{name}"],
            )
            conn.execute(
                "INSERT INTO statement (id, account_id, file_sha256, file_ext, original_filename,"
                " format, status, period_start, period_end, created_at, updated_at)"
                " VALUES (?, ?, ?, 'csv', 'synthetic.csv', 'csv', 'imported', ?, ?, 'x', 'x')",
                [
                    f"s_{name}",
                    f"a_{name}",
                    secrets.token_hex(32),
                    h.start.isoformat(),
                    h.end.isoformat(),
                ],
            )
        for n, t in enumerate(h.transactions):
            txn_id = f"t_{n:05d}"
            conn.execute(
                'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
                " raw_description, merchant_text, bank_category, bank_type, source_ref,"
                " fingerprint, occurrence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,"
                " '2026-11-01T00:00:00Z')",
                [
                    txn_id,
                    f"a_{t.account}",
                    f"s_{t.account}",
                    t.date.isoformat(),
                    t.amount_pence,
                    t.merchant.upper(),
                    t.merchant,
                    t.bank_category or None,
                    t.bank_type,
                    f"L{n + 2}",
                    f"{n:024d}",
                ],
            )
            conn.execute(  # M3's link table: the statement covers the row
                "INSERT INTO statement_transaction (statement_id, transaction_id, source_ref,"
                " match) VALUES (?, ?, ?, 'new')",
                [f"s_{t.account}", txn_id, f"L{n + 2}"],
            )
            truth[txn_id] = t.category_id
    return truth


def run_household(h: Household, *, llm: Any, context_window: int, work: Path) -> dict[str, Any]:
    db = Database(work / "eval.db")
    migrate(db, work / "backups")
    versions = KnowledgeVersions(db)
    understanding = UnderstandingStore(db, versions)
    categories = CategoryStore(db, versions)
    categories.seed()
    rules = RuleStore(db, versions, understanding)
    rules.seed()
    config = ConfigService(db, SettingsStore(db), work / "config")
    merchants = MerchantStore(db)
    store = CommitmentStore(db)
    truth = load(db, h)
    categoriser = Categoriser(
        CategoriserDeps(
            db=db,
            versions=versions,
            understanding=understanding,
            categories=categories,
            merchants=merchants,
            rules=rules,
            refiles=RefileStore(db, versions, understanding),
            llm=llm,
            context_window=lambda task: context_window,
            people=lambda: [PersonRef("p_alex", "Alex Example", "adult")],
            manifest=lambda: config.get("categoriser"),
        )
    )
    graph = AnalysisGraph(
        AnalysisDeps(
            db=db,
            versions=versions,
            categoriser=categoriser,
            transfers=TransferMatcher(
                db, understanding, versions, lambda: config.get("transfer_matcher")
            ),
            commitments=Commitments(
                CommitmentsDeps(
                    db=db,
                    merchants=merchants,
                    store=store,
                    llm=llm,
                    context_window=lambda task: context_window,
                    manifest=lambda: config.get("commitments"),
                )
            ),
            manifest=config.get,
        )
    ).build(None)
    run = counting()
    own = {n: counting() for n in ("categoriser", "commitments")}
    context = AnalysisContext(
        run_id="eval", budgets={n: LayeredBudget(b, run) for n, b in own.items()}
    )
    started = time.monotonic()
    graph.invoke(
        {"run_id": "eval", "statement_ids": ["s_current", "s_card", "s_savings"]}, context=context
    )
    seconds = round(time.monotonic() - started, 2)
    tree = categories.tree()
    with db.connection() as conn:
        got = {
            r[0]: r[1]
            for r in conn.execute("SELECT transaction_id, category_id FROM understanding")
        }
        by = Counter(
            r[0] or "unknown" for r in conn.execute("SELECT decided_by FROM understanding")
        )
    return {
        **score(truth, got, tree),
        **score_commitments(h, store),
        "llm_calls": run.calls,
        "tokens": run.tokens,
        "cost_gbp": round(run.gbp, 4),
        "seconds": seconds,
        "decided_by": dict(by),
        "transactions": len(truth),
    }


def score(truth: dict[str, str], got: dict[str, str | None], tree: CategoryTree) -> dict[str, Any]:
    l1 = sum(tree.ancestor_at(got.get(t), 1) == tree.ancestor_at(c, 1) for t, c in truth.items())
    with_l2 = {t: c for t, c in truth.items() if tree.ancestor_at(c, 2)}
    l2 = sum(tree.ancestor_at(got.get(t), 2) == tree.ancestor_at(c, 2) for t, c in with_l2.items())
    unknown = sum(got.get(t) is None for t in truth)
    return {
        "l1_accuracy": round(l1 / len(truth), 4),
        "l2_accuracy": round(l2 / len(with_l2), 4) if with_l2 else 1.0,
        "unknown_share": round(unknown / len(truth), 4),
    }


def score_commitments(h: Household, store: CommitmentStore) -> dict[str, Any]:
    found = {(merchant_key(c.name), c.account_id): c for c in store.list()}
    rows: list[dict[str, Any]] = []
    for e in h.commitments:
        c = found.get((merchant_key(e.merchant), f"a_{e.account}"))
        ok = (
            c is not None
            and c.cadence == e.cadence
            and c.expected_amount_pence == e.amount_pence
            and c.status == e.status
            and set(e.flags) <= set(c.flags)
        )
        rows.append(
            {
                "merchant": e.merchant,
                "ok": ok,
                "got": None
                if c is None
                else {
                    "cadence": c.cadence,
                    "status": c.status,
                    "amount_pence": c.expected_amount_pence,
                    "flags": c.flags,
                },
            }
        )
    expected = {(merchant_key(e.merchant), f"a_{e.account}") for e in h.commitments}
    extra = sorted(c.name for k, c in found.items() if k not in expected)
    return {
        "commitments_expected": len(rows),
        "commitments_ok": sum(r["ok"] for r in rows),
        "commitments_extra": extra,
        "commitment_results": rows,
    }


def render(results: list[dict[str, Any]]) -> str:
    lines = [
        "| Model | Top level | Level 2 | Still unknown | Commitments | AI calls | £ |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: -r["l1_accuracy"]):
        lines.append(
            f"| {r['model']} | {r['l1_accuracy']:.1%} | {r['l2_accuracy']:.1%} |"
            f" {r['unknown_share']:.1%} | {r['commitments_ok']}/{r['commitments_expected']} |"
            f" {r['llm_calls']} | £{r['cost_gbp']:.4f} |"
        )
    return "\n".join(lines)


def write_table(readme: Path) -> str:
    results = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(RESULTS.glob("understanding-*.json"))
    ]
    table = render(results)
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit(f"{readme} has no {START} … {END} markers.")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(f"{head}{START}\n{table}\n{END}{tail}", encoding="utf-8")
    return table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.understand", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--model", help='"oracle", or "<connection>/<model id>"')
    parser.add_argument("--data-dir", help="Tuppence data folder holding the connection")
    parser.add_argument("--months", type=int, default=12, choices=range(3, 25), metavar="3-24")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", help="write the result as JSON to this file")
    parser.add_argument(
        "--require-targets",
        action="store_true",
        help="exit 1 unless the spec's local-model targets are met",
    )
    parser.add_argument("--table", action="store_true", help="print the README table")
    parser.add_argument("--write", help="with --table: README file to update")
    args = parser.parse_args(argv)
    if args.table:
        print(
            write_table(Path(args.write))
            if args.write
            else render(
                [
                    json.loads(p.read_text(encoding="utf-8"))
                    for p in sorted(RESULTS.glob("understanding-*.json"))
                ]
            )
        )
        return 0
    if not args.model:
        parser.error("--model is required")
    if args.model == "oracle":
        llm, window, label = OracleLLM(), 8192, "oracle"
    else:
        from evals.run import real_model

        llm, window, label = real_model(args.model, args.data_dir)
    household = generate(months=args.months, seed=args.seed)
    with tempfile.TemporaryDirectory() as work:
        result = {
            "model": label,
            "months": args.months,
            "seed": args.seed,
            **run_household(household, llm=llm, context_window=window, work=Path(work)),
        }
    print(
        f"{label}: top level {result['l1_accuracy']:.1%}, level 2 {result['l2_accuracy']:.1%},"
        f" unknown {result['unknown_share']:.1%}, commitments"
        f" {result['commitments_ok']}/{result['commitments_expected']}"
        f" ({result['llm_calls']} AI calls, £{result['cost_gbp']:.4f}, {result['seconds']} s)"
    )
    for row in result["commitment_results"]:
        if not row["ok"]:
            print(f"  missed or wrong: {row['merchant']} → {row['got']}")
    if result["commitments_extra"]:
        print(f"  not expected: {', '.join(result['commitments_extra'])}")
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    met = (
        result["l1_accuracy"] >= TARGET_L1
        and result["l2_accuracy"] >= TARGET_L2
        and result["commitments_ok"] == result["commitments_expected"]
    )
    return 1 if args.require_targets and not met else 0


if __name__ == "__main__":
    sys.exit(main())
