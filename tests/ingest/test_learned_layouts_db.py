from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.ingest.registry import CsvLayout, LayoutRegistry, LearnedLayouts, load_bank_pack
from tuppence.ingest.textprep import csv_document


def test_learned_layouts_survive_a_restart(tmp_path, fixtures):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    doc = csv_document((fixtures / "csv-unknown" / "credit-union.csv").read_bytes(), sha256="x")
    layout = CsvLayout(
        id="learned-1",
        name="learned",
        signature=["Posting Date"],
        date="Posting Date",
        description=["Details"],
        money_out="Withdrawals",
        money_in="Deposits",
        balance="Running Balance",
    )
    LayoutRegistry(load_bank_pack(), learned=LearnedLayouts(db)).save_learned(doc, layout)
    after_restart = LayoutRegistry(load_bank_pack(), learned=LearnedLayouts(db))
    found = after_restart.match(doc, kind="current")
    assert found is not None and found.id.startswith("learned-") and found.source == "learned"
