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
        "Card 4111 1111 1111 1234 used by Alex Example; Sam Example paid Jo Landlord £1,450.00\n"
        "Contact alex@example.com or 07700 900123; IBAN GB33BUKB20201555555555; LS6 2AB\n"
        "OFX date 20260907 and ref 99887766"
    )
    out = p.redact(text)
    secrets = [
        "12-34-56",
        "12345678",
        "4111 1111 1111 1234",
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
