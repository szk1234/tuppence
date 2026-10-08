import json
from datetime import date

import pytest
from evals.household import FIXTURE, generate, three_month_fixture
from evals.oracle import OracleLLM
from evals.understand import END, START, TARGET_L1, TARGET_L2, main, render, run_household


def test_the_household_is_deterministic_and_dated():
    a, b = generate(months=12, seed=7), generate(months=12, seed=7)
    assert a.transactions == b.transactions and a.transactions != generate(seed=8).transactions
    assert a.start == date(2025, 11, 1) and a.end == date(2026, 10, 31)
    assert all(a.start <= t.date <= a.end for t in a.transactions)
    assert {t.account for t in a.transactions} == {"current", "card", "savings"}
    assert len(a.commitments) == 20


@pytest.mark.parametrize("months", [12, 24])
def test_the_oracle_meets_every_target(tmp_path, months):
    result = run_household(
        generate(months=months, seed=3), llm=OracleLLM(), context_window=8192, work=tmp_path
    )
    assert result["l1_accuracy"] >= TARGET_L1 and result["l2_accuracy"] >= TARGET_L2
    assert result["commitments_ok"] == result["commitments_expected"], [
        r for r in result["commitment_results"] if not r["ok"]
    ]
    assert result["commitments_extra"] == []
    assert result["decided_by"]["rule"] > 0 and result["llm_calls"] > 0


def test_the_committed_browser_fixture_is_current():
    assert FIXTURE.read_text(encoding="utf-8") == three_month_fixture()
    assert "Sunrise Bakery" in FIXTURE.read_text(encoding="utf-8")


def test_the_cli_and_the_readme_table(tmp_path, capsys):
    out = tmp_path / "understanding-oracle.json"
    assert (
        main(["--model", "oracle", "--months", "12", "--out", str(out), "--require-targets"]) == 0
    )
    assert "oracle: top level" in capsys.readouterr().out
    table = render([json.loads(out.read_text())])
    assert table.startswith("| Model | Top level | Level 2 |") and "| oracle |" in table
    assert START.startswith("<!--") and END.startswith("<!--")


def test_the_statement_table_skips_understanding_results(tmp_path, capsys):
    from evals.table import load
    from evals.table import main as table_main

    (tmp_path / "understanding-oracle.json").write_text('{"model": "oracle"}')
    (tmp_path / "ollama.json").write_text('{"summary": {"model": "m"}}')
    assert load(tmp_path) == [{"model": "m"}]
    assert table_main([]) == 0  # the committed results folder, understanding-oracle.json included
    capsys.readouterr()
