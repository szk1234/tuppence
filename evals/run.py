"""Score a model on the synthetic statement corpus (spec §6.3).

    uv run python -m evals.run --model oracle
    uv run python -m evals.run --model "<connection id or name>/<model id>" [--data-dir DIR]
        [--cases csv-monzo,pdf-card-text] [--out evals/results/<name>.json] [--require-pass]
        [--allow-cloud] [--max-gbp 2]

A real model is used through the same LLM client as the app, so its usage and cost
are recorded in that data folder's Usage page like any other AI call. The corpus is
synthetic, but a cloud model is used only with --allow-cloud, and a run spends at most
--max-gbp pounds (£2 unless told otherwise).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from evals.corpus import CASES
from evals.harness import DEFAULT_MAX_GBP, CaseResult, eval_budget, run_case
from evals.oracle import OracleLLM


class FixedRouter:
    """Sends every task to one model, whatever the data folder's routing says."""

    def __init__(self, connection: Any, model: Any, inner: Any) -> None:
        self.connection, self.model, self.inner = connection, model, inner

    def chain_for(self, task: str) -> list[tuple[Any, Any]]:
        return [(self.connection, self.model)]

    def is_pinned_local(self, task: str) -> bool:
        return self.inner.is_pinned_local(task)


def real_model(
    spec: str, data_dir: str | None, *, allow_cloud: bool = False
) -> tuple[Any, int, str]:
    from tuppence.app.services import build_services
    from tuppence.llm.client import LLMClient
    from tuppence.paths import InstanceLocked, acquire_instance_lock, resolve_data_dir
    from tuppence.settings import RuntimeSettings

    reference, _, model_id = spec.partition("/")
    if not model_id:
        sys.exit('Use --model "<connection>/<model id>", for example "Ollama/qwen2.5:7b".')
    folder = resolve_data_dir(data_dir)
    try:  # build_services migrates and purges launch sessions, so no app may be using the folder
        lock = acquire_instance_lock(folder)
    except InstanceLocked:
        sys.exit("Close Tuppence first: this data folder is in use by a running Tuppence.")
    services = build_services(RuntimeSettings.for_mode("local", data_dir=folder))

    def refuse(message: str) -> NoReturn:
        services.stop()
        lock.release()
        sys.exit(message)

    connection = next((c for c in services.connections.list() if reference in (c.id, c.name)), None)
    if connection is None:
        refuse(f"No AI connection called {reference!r} in that data folder.")
    if not connection.is_local:
        if not allow_cloud:
            refuse(
                f"{connection.name} is a cloud model: the eval would send it the synthetic "
                "statements. Add --allow-cloud to run it anyway."
            )
        print(f"Sending the synthetic statements to {connection.name}, a cloud model.")
    model = services.connections.model(connection.id, model_id)
    llm = LLMClient(
        connections=services.connections,
        router=FixedRouter(connection, model, services.router),  # type: ignore[arg-type]
        usage=services.usage,
        settings=services.settings,
        household=services.household,
        breakers=services.breakers,
        privacy_log=services.privacy_log,
    )
    return llm, model.context_window, f"{connection.name}/{model_id}"


def summarise(label: str, results: list[CaseResult]) -> dict[str, Any]:
    ai = [r for r in results if any(c.id == r.id and c.needs_ai for c in CASES)]
    return {
        "model": label,
        "cases": len(results),
        "passed": sum(r.ok for r in results),
        "ai_cases": len(ai),
        "ai_passed": sum(r.ok for r in ai),
        "row_accuracy": round(sum(r.accuracy for r in results) / len(results), 4)
        if results
        else 0.0,
        "cost_gbp_per_ai_case": round(sum(r.cost_gbp for r in ai) / len(ai), 4) if ai else 0.0,
        "seconds_per_ai_case": round(sum(r.seconds for r in ai) / len(ai), 2) if ai else 0.0,
    }


def print_table(results: list[CaseResult], summary: dict[str, Any]) -> None:
    heading = [
        "case".ljust(24),
        "ok".ljust(4),
        "rows".rjust(9),
        "accuracy".rjust(8),
        "AI calls".rjust(8),
        "£".rjust(8),
        "seconds".rjust(8),
        " importer",
    ]
    print(" ".join(heading))
    for r in results:
        rows = f"{r.matched}/{r.expected_rows}"
        ok = "yes" if r.ok else "NO"
        print(
            f"{r.id:24} {ok:4} {rows:>9} {r.accuracy:>8.2%} {r.llm_calls:>8} "
            f"{r.cost_gbp:>8.4f} {r.seconds:>8.2f}  {r.importer}"
        )
        for error in r.errors[:3]:
            print(f"{'':29}{error}")
    print(
        f"\n{summary['model']}: {summary['passed']}/{summary['cases']} cases passed "
        f"({summary['ai_passed']}/{summary['ai_cases']} needing AI); "
        f"row accuracy {summary['row_accuracy']:.1%}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--model", required=True, help='"oracle", or "<connection>/<model id>"')
    parser.add_argument(
        "--data-dir", help="Tuppence data folder holding the connection (default: the usual one)"
    )
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--out", help="write the results as JSON to this file")
    parser.add_argument(
        "--require-pass", action="store_true", help="exit 1 unless every case passes"
    )
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="allow a cloud model (the synthetic statements are sent to it)",
    )
    parser.add_argument(
        "--max-gbp",
        type=float,
        default=DEFAULT_MAX_GBP,
        help=f"the most the whole run may spend (default £{DEFAULT_MAX_GBP:.0f})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wanted = set(args.cases.split(",")) if args.cases else None
    cases = [c for c in CASES if wanted is None or c.id in wanted]
    if args.model == "oracle":
        llm, window, label = OracleLLM(), 4096, "oracle"
    else:
        llm, window, label = real_model(args.model, args.data_dir, allow_cloud=args.allow_cloud)
    results: list[CaseResult] = []
    spent = 0.0
    for case in cases:  # every case draws on what is left of --max-gbp
        budget = eval_budget(max(args.max_gbp - spent, 0.0))
        results.append(run_case(case, llm=llm, context_window=window, budget=budget))
        spent += budget.gbp
    summary = summarise(label, results)
    print_table(results, summary)
    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": summary, "results": [r.model_dump() for r in results]}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    fixed_failed = any(
        not r.ok for r in results if not any(c.id == r.id and c.needs_ai for c in CASES)
    )
    return 1 if fixed_failed or (args.require_pass and summary["passed"] < summary["cases"]) else 0


if __name__ == "__main__":
    sys.exit(main())
