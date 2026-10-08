from importlib import resources

import pytest

from tuppence.ingest.registry import (
    CsvLayout,
    LayoutRegistry,
    header_key,
    load_bank_pack,
    load_pack,
    norm,
)
from tuppence.ingest.textprep import csv_document

TWELVE = {
    "monzo",
    "starling",
    "hsbc",
    "barclays",
    "lloyds-halifax",
    "natwest",
    "santander",
    "nationwide",
    "chase",
    "revolut",
    "amex",
    "barclaycard",
}


def test_baseline_pack_has_the_twelve_uk_layouts():
    pack = load_bank_pack()
    assert {layout.id for layout in pack.layouts} == TWELVE
    assert all(not layout.confirmed and layout.source == "pack" for layout in pack.layouts)
    assert pack.pdf_markers[0].provider == "barclaycard"  # before the broader Barclays marker
    assert pack.sort_codes["040004"] == "monzo" and pack.bics["NAIAGB21"] == "nationwide"


def test_tampered_pack_file_is_refused(tmp_path):
    source = resources.files("tuppence.datapacks.baseline").joinpath("uk-banks")
    copy = tmp_path / "uk-banks"
    copy.mkdir()
    for name in ("manifest.json", "layouts.yaml", "markers.yaml"):
        (copy / name).write_bytes(source.joinpath(name).read_bytes())
    (copy / "layouts.yaml").write_text((copy / "layouts.yaml").read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="checksum"):
        load_pack(copy)


def test_layout_shape_rules():
    with pytest.raises(ValueError):
        CsvLayout(
            id="x",
            name="x",
            signature=["Date"],
            date="Date",
            description=["D"],
            amount="A",
            money_out="O",
        )
    with pytest.raises(ValueError):
        CsvLayout(
            id="x", name="x", signature=["Date"], date="Date", description=["D"], money_out="O"
        )
    with pytest.raises(ValueError):
        CsvLayout(id="x", name="x", date="Date", description=["D"], amount="A")


@pytest.mark.parametrize("name", sorted(TWELVE))
def test_each_fixture_matches_exactly_one_layout(fixtures, name):
    registry = LayoutRegistry(load_bank_pack())
    doc = csv_document(
        (fixtures / "csv" / f"{name}.csv").read_bytes(), sha256="x", known=registry.is_known_header
    )
    assert registry.match(doc).id == name
    header = (
        doc.table[[ln.ref for ln in doc.lines].index(doc.header_refs[0])]
        if doc.header_refs
        else None
    )
    if header is not None:
        present = {norm(c) for c in header}
        fits = [
            layout.id
            for layout in registry.pack.layouts
            if layout.signature and {norm(s) for s in layout.signature} <= present
        ]
        assert fits == [name]


def test_user_layout_files(tmp_path, fixtures):
    folder = tmp_path / "importers"
    folder.mkdir()
    (folder / "credit-union.yaml").write_text(
        "layouts:\n  - id: my-credit-union\n    name: My credit union\n"
        "    signature: [Posting Date, Details, Withdrawals, Deposits]\n    date: Posting Date\n"
        "    description: [Details]\n    money_out: Withdrawals\n    money_in: Deposits\n"
        "    balance: Running Balance\n"
    )
    (folder / "broken.yaml").write_text("layouts: [this is: not valid")
    registry = LayoutRegistry(load_bank_pack(), user_dir=folder)
    doc = csv_document((fixtures / "csv-unknown" / "credit-union.csv").read_bytes(), sha256="x")
    layout = registry.match(doc)
    assert layout.id == "my-credit-union" and layout.source == "user"
    assert registry.user_errors() and registry.user_errors()[0].startswith("broken.yaml")


def test_learned_layout_is_matched_by_its_exact_headings(fixtures):
    registry = LayoutRegistry(load_bank_pack())
    doc = csv_document((fixtures / "csv-unknown" / "credit-union.csv").read_bytes(), sha256="x")
    assert registry.match(doc) is None
    layout = CsvLayout(
        id="learned-1",
        name="learned",
        kind="savings",
        signature=["Posting Date"],
        date="Posting Date",
        description=["Details"],
        money_out="Withdrawals",
        money_in="Deposits",
    )
    saved = registry.save_learned(doc, layout)
    assert saved.source == "learned" and saved.signature == [
        "Posting Date",
        "Details",
        "Withdrawals",
        "Deposits",
        "Running Balance",
    ]
    later = csv_document(
        (fixtures / "csv-unknown" / "credit-union-nov.csv").read_bytes(),
        sha256="y",
        known=registry.is_known_header,
    )
    assert registry.match(later) is None  # not before the account (and its side) is known
    assert (
        registry.match(later, kind="current").id
        == saved.id
        == f"learned-{
            header_key(['Posting Date', 'Details', 'Withdrawals', 'Deposits', 'Running Balance'])
        }-bank"
    )
    assert registry.match(later, kind="credit_card") is None
    assert header_key(["Posting Date", " details "]) == header_key(["posting date", "Details"])
