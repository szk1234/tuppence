import time

from tuppence.llm.pseudonymise import Pseudonymiser, role_labels

PEOPLE = [("Alex Example", "adult"), ("Sam Example", "adult"), ("Kid A", "child")]


def test_role_labels():
    assert role_labels(PEOPLE) == {
        "Alex Example": "Adult A",
        "Sam Example": "Adult B",
        "Kid A": "Child 1",
    }


def test_role_labels_dependants():
    assert role_labels([("Nan", "dependent_adult")]) == {"Nan": "Dependant 1"}


def test_redacts_identifiers_but_not_money_dates_or_merchants():
    p = Pseudonymiser(PEOPLE, hidden_names=["Jo Landlord"])
    text = (
        "03/09/2026 TESCO STORES 3123 -£54.20\n"
        "04/09/2026 TO A EXAMPLE sort code 12-34-56 account no 12345678 £300.00\n"
        "Card 4111 1111 1111 0634 used by Alex Example; Sam Example paid Jo Landlord £1,450.00\n"
        "Contact alex@example.com or 07700 900123; IBAN GB33BUKB20201555555555; LS6 2AB\n"
        "OFX date 20260907 and ref 99887766"
    )
    out = p.redact(text)
    secrets = [
        "12-34-56",
        "12345678",
        "4111 1111 1111 0634",
        "Alex Example",
        "Sam Example",
        "Jo Landlord",
        "alex@example.com",
        "07700 900123",
        "GB33BUKB20201555555555",
        "LS6 2AB",
    ]
    for secret in secrets:
        assert secret not in out, secret
    for kept in ["TESCO STORES 3123", "-£54.20", "03/09/2026", "£1,450.00", "20260907", "99887766"]:
        assert kept in out, kept
    assert "Adult A" in out and "Adult B" in out and "Person 1" in out
    assert "ACCT_1 ending ••78" in out and "ending ••34" in out and "SORTCODE_1" in out
    assert p.count >= 10


def test_consistent_tokens_and_restore():
    p = Pseudonymiser(PEOPLE)
    a = p.redact("Alex Example paid sort code 12-34-56")
    b = p.redact("again sort code 12-34-56 for Alex Example")
    assert "SORTCODE_1" in a and "SORTCODE_1" in b
    reply = "Adult A sends money via SORTCODE_1; ACCT tokens unchanged"
    assert p.restore(reply) == "Alex Example sends money via 12-34-56; ACCT tokens unchanged"


def test_restore_value_recurses_and_handles_bare_account_token():
    p = Pseudonymiser(PEOPLE)
    p.redact("account number 87654321")
    value = {"who": "Adult A", "items": ["ACCT_1", "ACCT_1 ending ••21"], "n": 3}
    assert p.restore_value(value) == {
        "who": "Alex Example",
        "items": ["87654321", "87654321"],
        "n": 3,
    }


def test_names_are_word_bounded_and_case_insensitive():
    p = Pseudonymiser([("Alex Example", "adult")])
    assert p.redact("ALEX EXAMPLE and alexandra examples") == "Adult A and alexandra examples"


def test_restore_tolerates_casing_punctuation_and_spacing():
    p = Pseudonymiser(PEOPLE)
    p.redact("sort code 12-34-56 account number 87654321")
    out = p.restore("**sortcode_1**, (Acct_1  ending ••21), Adult A's and Adult B.")
    assert out == "**12-34-56**, (87654321), Alex Example's and Sam Example."


def test_restore_never_restores_unknown_or_partial_tokens():
    p = Pseudonymiser(PEOPLE)
    p.redact("sort code 12-34-56")
    # SORTCODE_2 and SORTCODE_12 were never issued; SORTCODE_1 must not match inside them.
    text = "SORTCODE_2 SORTCODE_12 XSORTCODE_1 Adult Attendance Adult C"
    assert p.restore(text) == text
    assert Pseudonymiser(PEOPLE).restore("SORTCODE_1") == "SORTCODE_1"


def test_distinct_numbers_get_distinct_tokens():
    p = Pseudonymiser([])
    out = p.redact("account 11111111 and account 22222222 and account 11111111")
    assert out.count("ACCT_1 ending ••11") == 2 and "ACCT_2 ending ••22" in out


def test_blank_and_duplicate_names_are_ignored():
    p = Pseudonymiser(
        [("Sam", "adult"), ("Sam", "adult"), ("  ", "adult"), ("Kim", "adult")],
        ["", " ", "Jo", "jo"],
    )
    text = "a  b. (x) -£5 , "
    assert p.redact(text) == text
    assert role_labels([("Sam", "adult"), ("Sam", "adult"), ("Kim", "adult")]) == {
        "Sam": "Adult A",
        "Kim": "Adult B",
    }
    assert p.redact("Jo paid Kim") == "Person 1 paid Adult B"
    assert Pseudonymiser([], ["", "Jo"]).redact("a  b. (x) -£5 , ") == "a  b. (x) -£5 , "


def test_sort_codes_and_accounts_real_world_forms():
    cases = {
        "12 34 56": "SORTCODE_1",
        "sort code 123456": "sort code SORTCODE_1",
        "SORT CODE 123456": "SORT CODE SORTCODE_1",
        "sc 123456": "sc SORTCODE_1",
        "s/c: 123456": "s/c: SORTCODE_1",
        "account 12345678": "account ACCT_1 ending ••78",
        "acct 12345678": "acct ACCT_1 ending ••78",
        "acc: 12345678": "acc: ACCT_1 ending ••78",
        "a/c 12345678": "a/c ACCT_1 ending ••78",
        "A/C No. 12345678": "A/C No. ACCT_1 ending ••78",
        "account number is 12345678": "account number is ACCT_1 ending ••78",
        "Acc no 1234 5678": "Acc no ACCT_1 ending ••78",
        "12-34-56 12345678": "SORTCODE_1 ACCT_1 ending ••78",
        "12345678 12-34-56": "ACCT_1 ending ••78 SORTCODE_1",
    }
    for text, expected in cases.items():
        assert Pseudonymiser([]).redact(text) == expected, text


def test_sort_code_and_account_false_positives():
    p = Pseudonymiser([])
    for text in [
        "£12345678.00",
        "20261007",
        "ref 99887766",
        "2026 10 07 12 34 56 78",
        "2026-10-07",
    ]:
        assert p.redact(text) == text, text
    assert p.redact("account £12345678.00") == "account £12345678.00"


def test_single_word_names_exact_case_or_caps_only():
    p = Pseudonymiser([("Will", "adult"), ("Sam", "adult")])
    assert p.redact("I will pay Will") == "I will pay Adult A"
    assert p.redact("WILL and SAM") == "Adult A and Adult B"
    assert p.redact("Sam's rent") == "Adult B's rent"
    assert (
        Pseudonymiser([("Sam Smith", "adult")]).redact("SAM SMITH sam smith") == "Adult A Adult A"
    )


def test_partial_and_statement_forms_of_multi_word_names():
    p = Pseudonymiser([("Alex Example", "adult"), ("Jordan Brown", "adult")])
    out = p.redact("TO A EXAMPLE; MR A EXAMPLE; A. Example; Example; ALEX only; J BROWN")
    assert out == "TO Adult A; MR Adult A; Adult A; Adult A; Adult A only; Adult B"
    assert p.restore("Adult A / Adult B") == "Alex Example / Jordan Brown"


def test_ambiguous_surname_is_not_masked_alone():
    p = Pseudonymiser([("Alex Example", "adult"), ("Sam Example", "adult")])
    assert p.redact("EXAMPLE, A EXAMPLE, S Example") == "EXAMPLE, Adult A, Adult B"


def test_unicode_names_and_apostrophes():
    p = Pseudonymiser([("Zoë Brown", "child"), ("Sean O'Neil", "adult")])
    assert p.redact("Zoe Brown / zoë brown / ZOË BROWN") == "Child 1 / Child 1 / Child 1"
    assert p.redact("Sean O’Neil and O’NEIL and Sean") == "Adult A and Adult A and Adult A"
    assert p.restore("Child 1 and Adult A") == "Zoë Brown and Sean O'Neil"
    assert Pseudonymiser([("Zoë", "adult")]).redact("Zoe ZOE") == "Adult A Adult A"


def test_ibans_generic_and_before_cards():
    p = Pseudonymiser([])
    for iban in [
        "gb33bukb20201555555555",
        "DE89 3704 0044 0532 0130 00",
        "FR1420041010050500013M02606",
    ]:
        out = p.redact(f"pay {iban} now")
        assert out.startswith("pay IBAN_") and out.endswith(" now"), out
        assert iban[:4].lower() not in out.lower().replace("iban", "")
    assert p.restore(p.redact("DE89 3704 0044 0532 0130 00")) == "DE89 3704 0044 0532 0130 00"


def test_card_numbers_luhn_for_separated_runs():
    p = Pseudonymiser([])
    assert p.redact("4111 1111 1111 1111") == "ACCT_1 ending ••11"
    assert p.redact("4111-1111-1111-1111") == "ACCT_1 ending ••11"
    assert p.redact("1234567890123456") == "ACCT_2 ending ••56"
    for text in [
        "2026 10 07 12 34 56 78",
        "Total 1 2 3 4 5 6 7 8 9 10 11 12 13 14",
        "1234 5678 9012 3456",
    ]:
        assert p.redact(text) == text, text


def test_restore_labels_case_hyphen_and_unissued():
    p = Pseudonymiser(PEOPLE, hidden_names=["Jo Landlord"])
    assert p.restore("adult a, ADULT A, Adult-A, person 1") == (
        "Alex Example, Alex Example, Alex Example, Jo Landlord"
    )
    assert p.restore("Adult C Child 2 Adult Attendance") == "Adult C Child 2 Adult Attendance"


def test_patterns_are_cached():
    p = Pseudonymiser(PEOPLE)
    p.redact("sort code 12-34-56")
    first = p._restore_pattern()
    assert p._restore_pattern() is first
    p.redact("sort code 65-43-21")
    assert p._restore_pattern() is not first


def test_email_and_long_inputs_are_linear():
    import time

    p = Pseudonymiser([("Alex Example", "adult")])
    assert p.redact("mail a.b+c@x.co.uk now") == "mail EMAIL_1 now"
    start = time.perf_counter()
    for text in ["1-" * 50_000, "a." * 50_000, "1 " * 50_000]:
        p.redact(text)
    assert time.perf_counter() - start < 3


def test_long_separated_runs_fail_closed():
    p = Pseudonymiser([])
    text = "ref " + "1 " * 30 + "4111 1111 1111 1111"
    out = p.redact(text)
    assert "4111" not in out and "ACCT_" in out
    assert "0" * 25 not in p.redact("0" * 25)
    start = time.perf_counter()
    for adversarial in [
        "1 " * 50_000,
        "1-" * 50_000,
        "4111 " * 20_000,
        "GB12 " * 25_000,
        "AB12 " * 25_000,
    ]:
        p.redact(adversarial)
    assert time.perf_counter() - start < 1


def test_dates_are_not_masked_but_sort_codes_are():
    p = Pseudonymiser([])
    for text in ["07 10 26", "07-10-26", "26 10 07", "paid 15 09 26 AMAZON"]:
        assert p.redact(text) == text, text
    assert p.redact("sort code 07-10-26") == "sort code SORTCODE_1"
    assert p.redact("12-34-56") == "SORTCODE_2"
    assert p.redact("Account balance 20261007") == "Account balance 20261007"
    assert p.redact("account 20261301") == "account ACCT_1 ending ••01"


def test_ibans_need_a_valid_checksum_and_stop_at_their_length():
    p = Pseudonymiser([])
    assert p.redact("to GB33 BUKB 2020 1555 5555 55 thanks") == "to IBAN_1 thanks"
    assert p.redact("DE89370400440532013000 and more") == "IBAN_2 and more"
    assert p.redact("FR1420041010050500013M02606") == "IBAN_3"
    assert p.redact("gb33bukb20201555555555") == "IBAN_1"
    for text in [
        "FP12 3456 7890",
        "REF FP12 3456 7890 1234",
        "hello there ab12 word abcd efgh ijkl mnop",
    ]:
        assert p.redact(text) == text, text
    assert p.redact("FP12 3456 7890 1234 GB33BUKB20201555555555") == "FP12 3456 7890 1234 IBAN_1"


def test_hyphenated_configured_name_is_one_stand_in():
    p = Pseudonymiser([], ["Jo", "Jo-Jo"])
    assert p.redact("Jo-Jo and Jo") == "Person 2 and Person 1"
