import json
from importlib import resources

import pytest

import build_bank_holidays as gen

RAW = {
    "england-and-wales": {"division": "england-and-wales", "events": [{"date": "2027-01-01"}]},
    "scotland": {
        "division": "scotland",
        "events": [{"date": "2027-01-04"}, {"date": "2027-01-01"}],
    },
    "northern-ireland": {"division": "northern-ireland", "events": [{"date": "2027-03-17"}]},
}


def test_build_is_reproducible_and_carries_the_ogl_attribution():
    a, b = gen.render(gen.build(RAW)), gen.render(gen.build(json.loads(json.dumps(RAW))))
    assert a == b  # same gov.uk data, same bytes: no fetch date is stamped
    data = json.loads(a)
    assert "fetched" not in data
    assert data["licence"] == "Open Government Licence v3.0"
    assert "open-government-licence/version/3" in data["licence_url"]
    assert data["attribution"].startswith(
        "Contains public sector information licensed under the Open"
    )
    assert data["divisions"]["scotland"] == ["2027-01-01", "2027-01-04"]


@pytest.mark.parametrize(
    "bad",
    [
        {k: v for k, v in RAW.items() if k != "scotland"},
        {**RAW, "scotland": {"events": []}},
        {**RAW, "scotland": {"events": [{"date": "4 Jan 2027"}]}},
        {**RAW, "scotland": {"events": [{"title": "no date"}]}},
    ],
)
def test_build_refuses_data_that_does_not_look_right(bad):
    with pytest.raises(ValueError):
        gen.build(bad)


def test_write_is_atomic(tmp_path):
    out = tmp_path / "uk-bank-holidays.json"
    out.write_text("old", encoding="utf-8")
    gen.write_atomic(out, "new\n")
    assert out.read_text(encoding="utf-8") == "new\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["uk-bank-holidays.json"]


def test_bundled_file_is_exactly_what_the_generator_writes():
    text = (
        resources.files("tuppence.datapacks.baseline")
        .joinpath("uk-bank-holidays.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(text)
    assert data["attribution"] == gen.ATTRIBUTION and data["licence"] == gen.LICENCE
    raw = {k: {"events": [{"date": d} for d in v]} for k, v in data["divisions"].items()}
    assert gen.render(gen.build(raw)) == text
