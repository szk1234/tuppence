import json

import pytest
from evals.corpus import CASES
from evals.harness import run_case
from evals.oracle import OracleLLM
from evals.run import main
from evals.table import END, START, render, write_readme


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_corpus_case_passes_with_the_oracle(case):
    result = run_case(case, llm=OracleLLM(), context_window=4096)
    assert result.ok, result.errors
    assert result.accuracy == 1.0
    if not case.needs_ai:
        assert result.llm_calls == 0  # fixed importers never call a model


def test_the_corpus_covers_the_m3_exit_criteria():
    fixed_csv = [c for c in CASES if c.kind == "csv" and not c.needs_ai]
    assert len(fixed_csv) == 12
    ids = {c.id for c in CASES}
    assert {
        "ofx-current",
        "qfx-card",
        "qif-bank",
        "camt-053",
        "xlsx",
        "pdf-card-text",
        "pdf-card-scanned",
        "pdf-current-text",
        "image-screenshot",
        "csv-unknown-layout",
    } <= ids


def test_the_cli_reports_and_saves_results(tmp_path, capsys):
    out = tmp_path / "oracle.json"
    assert (
        main(
            [
                "--model",
                "oracle",
                "--cases",
                "csv-monzo,pdf-card-text",
                "--out",
                str(out),
                "--require-pass",
            ]
        )
        == 0
    )
    saved = json.loads(out.read_text())
    assert saved["summary"]["passed"] == 2 and saved["summary"]["ai_cases"] == 1
    assert "oracle: 2/2 cases passed" in capsys.readouterr().out


def test_the_readme_table(tmp_path):
    summary = {
        "model": "Ollama/example:7b",
        "cases": 22,
        "passed": 21,
        "ai_cases": 5,
        "ai_passed": 4,
        "row_accuracy": 0.97,
        "cost_gbp_per_ai_case": 0.0,
        "seconds_per_ai_case": 41.5,
    }
    table = render([summary])
    assert "| Ollama/example:7b | 21/22 | 4/5 | 97.0% | £0.0000 | 41.5 |" in table
    readme = tmp_path / "README.md"
    readme.write_text(f"intro\n{START}\nold\n{END}\nend\n")
    write_readme(readme, table)
    assert "old" not in readme.read_text() and table in readme.read_text()
