from datetime import date

from tuppence.core import calendar as cal


def test_divisions():
    assert cal.division_for("wales") == "england-and-wales"
    assert cal.division_for(None) == "england-and-wales"
    assert cal.division_for("northern_ireland") == "northern-ireland"


def test_christmas_2026_and_substitute_boxing_day():
    h = cal.bank_holidays("england")
    assert date(2026, 12, 25) in h and date(2026, 12, 28) in h
    assert not cal.is_working_day(date(2026, 12, 28), "england")
    assert cal.previous_working_day(date(2026, 12, 25), "england") == date(2026, 12, 24)


def test_scotland_specific_holiday():
    assert date(2027, 1, 4) in cal.bank_holidays("scotland")  # 2 January substitute
    assert date(2027, 1, 4) not in cal.bank_holidays("england")


def test_last_working_day():
    assert cal.last_working_day(2027, 5, "england") == date(
        2027, 5, 28
    )  # 31 May 2027 is Spring bank holiday
    assert cal.last_working_day(2026, 10, "england") == date(2026, 10, 30)


def test_next_working_day_and_coverage():
    assert cal.next_working_day(date(2026, 12, 25), "england") == date(2026, 12, 29)
    assert cal.next_working_day(date(2026, 10, 7), "england") == date(2026, 10, 7)
    assert cal.coverage_end() >= date(2027, 12, 31)
