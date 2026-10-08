import re
from datetime import date

import pytest

from tuppence.knowledge.merchants import (
    MerchantStore,
    display_name,
    infer_memory,
    merchant_key,
)


@pytest.mark.parametrize(
    "text,key",
    [
        ("SQ *JS TRADING", "js trading"),
        ("SQ*JS TRADING", "js trading"),
        ("JS Trading Ltd", "js trading"),
        ("CARD PAYMENT TO JS TRADING ON 05 OCT", "js trading"),
        ("PAYPAL *STREAMLY", "streamly"),
        ("CRV*LITTLE CAFE", "little cafe"),
        ("SumUp  *Corner Cafe", "corner cafe"),
        ("IZ *CORNER CAFE LEEDS", "corner cafe"),
        ("ZTL*CORNER CAFE", "corner cafe"),
        ("GREENBASKET STORES 0873 LONDON GB", "greenbasket stores"),
        ("GREENBASKET STORES S0873", "greenbasket stores"),
        ("Greenbasket Stores", "greenbasket stores"),
        ("DIRECT DEBIT PAYMENT TO CITY WATER REF 12345678", "city water"),
        ("CITY WATER DD", "city water"),
        ("ACME PAYROLL LTD BGC", "acme payroll"),
        ("AMAZON.CO.UK*AB12CD345", "amazon"),
        ("AMZN Mktp UK*AB12C3D45", "amzn mktp"),
        ("STREAMLY.COM 0800 123 456", "streamly"),
        ("NORTHLINE RAIL 05/10", "northline rail"),
        ("VIS NORTHLINE RAIL 05OCT26", "northline rail"),
        ("Halfords 0873", "halfords"),
        ("Shop 12 London", "shop"),
        ("3 MOBILE", "3 mobile"),
        ("123", ""),
        ("", ""),
    ],
)
def test_merchant_keys(text, key):
    assert merchant_key(text) == key


def test_display_names():
    assert display_name("GREENBASKET STORES 0873 LONDON") == "Greenbasket Stores"
    assert display_name("SQ *JS TRADING") == "Js Trading"
    assert display_name("Little Cafe") == "Little Cafe"
    assert display_name("123") == "123"


def test_inferred_memory_needs_a_clear_majority():
    groceries = [("food.groceries", "household", 0.9, 4218)] * 3
    guess = infer_memory(groceries + [("food.eating-out", "p_alex", 0.8, 340)])
    assert guess is not None and guess.category_id == "food.groceries" and guess.rows == 3
    assert guess.share == 0.75 and guess.confidence == pytest.approx(0.675)
    assert infer_memory(groceries[:2]) is None  # too few
    assert infer_memory(groceries[:2] + [("other", None, 0.9, 1)] * 2) is None  # no majority


def test_resolve_creates_once_and_remembers_variants(kenv):
    store = MerchantStore(kenv.db)
    with kenv.db.transaction() as conn:
        a = store.resolve(conn, "SQ *JS TRADING", None)
        b = store.resolve(conn, "CARD PAYMENT TO JS TRADING ON 05 OCT", None)
        c = store.resolve(conn, "SQ *JS TRADING", None)
        none = store.resolve(conn, "123456", None)
    assert a is not None and b is not None and c is not None and none is None
    assert a.id == b.id == c.id and a.name == "Js Trading" and a.key == "js trading"
    with kenv.db.connection() as conn:
        variants = [r[0] for r in conn.execute("SELECT text FROM merchant_variant ORDER BY text")]
    assert variants == ["CARD PAYMENT TO JS TRADING ON # OCT", "SQ *JS TRADING"]


def test_merchant_text_from_the_statement_wins_for_the_name(kenv):
    store = MerchantStore(kenv.db)
    with kenv.db.transaction() as conn:
        m = store.resolve(conn, "DD 0012345 STRMLY", "Streamly")
    assert m is not None and m.key == "streamly" and m.name == "Streamly"
    assert date  # imported for parity with other tests


@pytest.mark.parametrize(
    "text",
    [
        "ACME SORT 20-00-00 ACC 12345678",
        "12345678 ACME",
        "TFR TO SAVINGS 40-11-62 31926819",
        "ACME 20 00 00 SHOP",
        "ACME XXXX1234",
        "ACME 4929123456781234",
        "ACME 1234 5678 9012 3456 SHOP",
    ],
)
def test_keys_and_names_never_keep_account_numbers_or_card_digits(text):
    for value in (merchant_key(text), display_name(text)):
        assert not re.search(r"\d{4,}", value) and not re.search(r"\d{2,}-\d{2,}", value)
        assert "xxxx" not in value.lower() and not re.search(r"\d \d", value)


def test_variants_and_names_stored_blank_every_number(kenv):
    store = MerchantStore(kenv.db)
    with kenv.db.transaction() as conn:
        m = store.resolve(conn, "ACME XXXX1234 SORT 20-00-00 ACC 12345678", "12345678")
        assert m is not None
    with kenv.db.connection() as conn:
        texts = [r[0] for r in conn.execute("SELECT text FROM merchant_variant")]
        names = [r[0] for r in conn.execute("SELECT name FROM merchant")]
        keys = [r[0] for r in conn.execute("SELECT key FROM merchant")]
    assert texts and not any(re.search(r"\d", t) for t in texts)
    assert not any(re.search(r"\d{2,}", v) for v in names + keys)
    assert m.key.startswith("acme")
