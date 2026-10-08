from datetime import date, timedelta

from agents.helpers import file_as, manifest
from tuppence.agents.commitments import Commitments, CommitmentsDeps, kind_for_category
from tuppence.knowledge.commitments import CommitmentStore, project


def months(day, n, start=(2026, 1)):
    y, m = start
    out = []
    for _ in range(n):
        out.append(date(y, m, day))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def add(aenv, dates, pence, text, category, **kw):
    if isinstance(pence, int):
        pence = [pence] * len(dates)
    ids = []
    for d, p in zip(dates, pence, strict=True):
        t = aenv.add_txn(d, -p, text, **kw)
        file_as(aenv, t, category, aenv.merchants)
        ids.append(t)
    return ids


def specialist(aenv):
    store = CommitmentStore(aenv.db)
    return Commitments(
        CommitmentsDeps(
            db=aenv.db,
            merchants=aenv.merchants,
            store=store,
            llm=aenv.llm,
            context_window=aenv.window_for,
            manifest=lambda: manifest("commitments"),
        )
    ), store


def test_kinds_from_categories():
    assert kind_for_category("subscriptions.music") == "subscription"
    assert kind_for_category("subscriptions.mobile") == "bill"
    assert kind_for_category("transport.car.finance") == "instalment"
    assert kind_for_category("food.groceries") == "none"
    assert kind_for_category("other") is None and kind_for_category(None) is None


def test_finds_flags_and_costs_commitments(aenv):
    statement = aenv.add_statement("a_current", date(2026, 1, 1), date(2026, 8, 31))
    kw = {"statement_id": statement}
    add(aenv, months(14, 8), [999] * 5 + [1199] * 3, "STREAMLY", "subscriptions.tv-streaming", **kw)
    add(aenv, months(3, 8), 1099, "TUNEWAVE MUSIC", "subscriptions.music", **kw)
    add(aenv, months(20, 8), 1199, "MELODIA PREMIUM", "subscriptions.music", **kw)
    add(aenv, months(1, 4), 2500, "FLEXFIT GYM", "health.fitness", **kw)  # stops in April
    add(
        aenv,
        [date(2026, 2, 27)] + months(1, 6, start=(2026, 3)),
        [99] + [799] * 6,
        "CLOUDBOX STORAGE",
        "subscriptions.software",
        **kw,
    )
    weekly = [date(2026, 1, 3) + timedelta(days=7 * i) for i in range(30)]
    add(
        aenv,
        weekly,
        [4000 + (i * 731) % 3000 for i in range(30)],
        "GREENBASKET STORES",
        "food.groceries",
        **kw,
    )
    fortnightly = [date(2026, 1, 9) + timedelta(days=14 * i) for i in range(16)]
    add(
        aenv,
        fortnightly,
        1500,
        "SPARKLE WINDOW CLEANING",
        "other",
        bank_type="Standing order",
        **kw,
    )
    commitments, store = specialist(aenv)
    counts = commitments.run(run_id="r1", budget=None)
    found = {c.name: c for c in store.list()}
    assert set(found) == {
        "Streamly",
        "Tunewave Music",
        "Melodia Premium",
        "Flexfit Gym",
        "Cloudbox Storage",
        "Sparkle Window Cleaning",
    }
    assert counts["new"] == 6
    streamly = found["Streamly"]
    assert (streamly.cadence, streamly.kind, streamly.expected_amount_pence) == (
        "monthly",
        "subscription",
        1199,
    )
    assert "price_rise" in streamly.flags and streamly.annual_cost_pence == 1199 * 12
    assert streamly.next_due == date(2026, 9, 14) and streamly.occurrences == 8
    assert "duplicate" in found["Tunewave Music"].flags
    assert found["Melodia Premium"].evidence["duplicate_of"] == ["Tunewave Music"]
    assert found["Flexfit Gym"].status == "lapsed" and "lapsed" in found["Flexfit Gym"].flags
    assert "free_trial_converted" in found["Cloudbox Storage"].flags
    window = found["Sparkle Window Cleaning"]
    assert (window.cadence, window.kind) == ("fortnightly", "bill")
    assert project(streamly, date(2026, 9, 1), date(2026, 11, 30)) == [
        date(2026, 9, 14),
        date(2026, 10, 14),
        date(2026, 11, 14),
    ]
    assert commitments.run(run_id="r2", budget=None)["updated"] == 6  # stable ids


def test_dismissed_stays_dismissed_and_the_model_labels_unknown_kinds(aenv):
    add(aenv, months(5, 4), 1500, "MYSTERY CLUB", "other")
    commitments, store = specialist(aenv)
    commitments.run(run_id="r1", budget=None)
    assert store.list() == []  # the oracle says "none" for an 'other' payment
    aenv.llm.script = [{"payments": [{"ref": "P1", "kind": "subscription"}]}]
    with aenv.db.transaction() as conn:
        conn.execute("UPDATE merchant SET business_type = NULL, business_type_source = NULL")
    commitments.run(run_id="r2", budget=None)
    [club] = store.list()
    assert club.kind == "subscription" and club.kind_source == "llm"
    store.set_dismissed(club.id, club.version, True)
    add(aenv, months(5, 2, start=(2026, 5)), 1500, "MYSTERY CLUB", "other")
    commitments.run(run_id="r3", budget=None)
    assert store.list() == [] and len(store.list(include_dismissed=True)) == 1
    assert len(aenv.llm.calls) == 2  # labels are remembered on the merchant


def test_council_tax_ten_instalments(aenv):
    dates = [date(2026, m, 1) for m in range(4, 13)] + [date(2027, 1, 1)]
    statement = aenv.add_statement("a_current", date(2026, 4, 1), date(2027, 3, 31))
    add(aenv, dates, 14200, "NORTHFIELD COUNCIL", "housing.council-tax", statement_id=statement)
    commitments, store = specialist(aenv)
    commitments.run(run_id="r1", budget=None)
    [tax] = store.list()
    assert tax.status == "active" and tax.skip_months == [2, 3] and tax.flags == []
    assert tax.next_due == date(2027, 4, 1) and tax.annual_cost_pence == 142000


def test_lapses_are_judged_against_the_statements_not_today(aenv):
    """Statements from January to March, analysed long after: still active, next due April."""
    statement = aenv.add_statement("a_current", date(2026, 1, 1), date(2026, 3, 31))
    add(aenv, months(14, 3), 999, "STREAMLY", "subscriptions.tv-streaming", statement_id=statement)
    commitments, store = specialist(aenv)
    commitments.run(run_id="r1", budget=None)
    [streamly] = store.list()
    assert streamly.status == "active" and streamly.flags == []
    assert streamly.next_due == date(2026, 4, 14)


def test_the_labels_prompt_holds_no_account_digits_and_is_grouped_by_run(aenv):
    """G6: the merchant name is scrubbed whole before it is cut; G4/Task 4: usage groups by run."""
    text = "SO TO 20-11-33 87654321 RENT"
    add(aenv, months(1, 4), 90000, text, "other")
    with aenv.db.transaction() as conn:  # a merchant name that kept the numbers
        conn.execute("UPDATE merchant SET name = ?", [text])
    commitments, store = specialist(aenv)
    commitments.run(run_id="run_x", budget=None)
    sent = "\n".join(aenv.prompts())
    assert "RENT" in sent and "<HIDDEN>" in sent
    for secret in ("87654321", "20-11-33", "201133"):
        assert secret not in sent
    assert [c["run_id"] for c in aenv.llm.calls] == ["run_x"]
    assert store.list() == []  # the oracle says "none" for an 'other' payment


def test_a_long_merchant_name_is_scrubbed_before_it_is_cut(aenv):
    text = "PAYMENT " + "A" * 70 + " 93716482 CLUB"
    add(aenv, months(1, 4), 1500, text, "other")
    with aenv.db.transaction() as conn:
        conn.execute("UPDATE merchant SET name = ?", [text])
    commitments, _ = specialist(aenv)
    commitments.run(run_id="r1", budget=None)
    sent = "\n".join(aenv.prompts())
    assert "93716482" not in sent and "9371" not in sent and "6482" not in sent


def test_labelling_stops_when_the_budget_is_reached(aenv):
    from tuppence.llm.types import BudgetExceeded

    add(aenv, months(5, 4), 1500, "MYSTERY CLUB", "other")
    aenv.llm.script = [BudgetExceeded("run cap")]
    commitments, store = specialist(aenv)
    counts = commitments.run(run_id="r1", budget=None)
    assert (counts["deferred"], counts["awaiting_ai"], counts["labelled"]) == (1, 0, 0)
    assert "ai_problem" not in counts and store.list() == []


def test_a_bad_reply_defers_that_batch_and_the_run_carries_on(aenv):
    add(aenv, months(5, 4), 1500, "MYSTERY CLUB", "other")
    add(aenv, months(7, 4), 999, "STREAMLY", "subscriptions.tv-streaming")
    aenv.llm.script = ["not json at all"]
    commitments, store = specialist(aenv)
    counts = commitments.run(run_id="r1", budget=None)
    assert (counts["bad_replies"], counts["deferred"]) == (1, 1)
    assert [c.name for c in store.list()] == ["Streamly"]  # found by code regardless


def test_a_model_problem_never_fails_the_run(aenv):
    from tuppence.llm.types import NoModelConfigured

    add(aenv, months(7, 4), 999, "STREAMLY", "subscriptions.tv-streaming")
    commitments, store = specialist(aenv)

    def no_model(task):
        raise NoModelConfigured("Choose a model in Settings › AI.")

    commitments.d.context_window = no_model
    add(aenv, months(5, 4), 1500, "MYSTERY CLUB", "other")
    counts = commitments.run(run_id="r1", budget=None)
    assert counts["awaiting_ai"] == 1 and "model" in counts["ai_problem"].lower()
    assert [c.name for c in store.list()] == ["Streamly"]
