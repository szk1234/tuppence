import contextlib
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel

from tuppence.config.models import Budgets
from tuppence.core.household import PersonIn
from tuppence.llm.budget import RunBudget
from tuppence.llm.types import (
    AllModelsBlocked,
    AllModelsFailed,
    BudgetExceeded,
    LLMBadResponse,
    Message,
    NoticeRequired,
)


def setup_local(services, model="m-small"):
    c = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
    services.connections.test(c.id)
    services.settings.set(
        "llm.simple_model", {"connection_id": c.id, "model_id": model}, expected_version=0
    )
    return c


def setup_cloud(services):
    c = services.connections.create("openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1")
    services.connections.test(c.id)
    services.connections.acknowledge_notice(c.id)
    services.settings.set(
        "llm.simple_model", {"connection_id": c.id, "model_id": "m-small"}, expected_version=0
    )
    return c


U = [Message(role="user", content="hello")]
CLOCK = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)


def month_now(services):
    """Pin the ledger's UTC clock so the month can't change under a test."""
    services.usage.clock = lambda: CLOCK
    return CLOCK.year, CLOCK.month


def spend(services):
    return services.usage.month_spend_gbp(CLOCK.year, CLOCK.month)


def test_chat_records_usage_and_privacy_log(env):
    services, scripted = env
    c = setup_local(services)
    scripted.replies = [{"content": "hi there"}]
    r = services.llm.chat("coach", U)
    assert r.text == "hi there" and r.connection_id == c.id and r.cost_gbp == 0.0
    summary = services.usage.summary(*month_now(services))
    assert summary["calls"] == 1 and summary["failed_calls"] == 0
    assert any(e.outcome == "sent" and e.task == "coach" for e in services.privacy_log.list())


def test_retry_after_then_success(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [
        httpx.Response(429, headers={"Retry-After": "2"}, json={}),
        {"content": "ok"},
    ]
    assert services.llm.chat("coach", U).text == "ok"
    assert {"slept": 2.0} in scripted.requests


def test_falls_back_to_next_model_on_failure(env):
    services, scripted = env
    c = setup_local(services)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task(
        "coach",
        [
            {"connection_id": c.id, "model_id": "m-big"},
            {"connection_id": c.id, "model_id": "m-small"},
        ],
        local_only=False,
        expected_version=0,
    )
    scripted.replies = [httpx.Response(500, json={})] * 3 + [{"content": "from small"}]
    r = services.llm.chat("coach", U)
    assert r.text == "from small" and r.model_id == "m-small"


def test_all_fail_raises_with_reasons(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(400, json={"error": "bad"})]
    with pytest.raises(AllModelsFailed) as exc:
        services.llm.chat("coach", U)
    assert "HTTP 400" in str(exc.value)


def test_cloud_needs_notice_first(env):
    services, _ = env
    c = services.connections.create("openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1")
    services.connections.test(c.id)
    services.settings.set(
        "llm.simple_model", {"connection_id": c.id, "model_id": "m-small"}, expected_version=0
    )
    with pytest.raises(NoticeRequired, match="Confirm what"):
        services.llm.chat("coach", U)


def test_local_only_blocks_cloud_before_sending(env):
    services, scripted = env
    setup_cloud(services)
    services.settings.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(AllModelsBlocked, match="Local only"):
        services.llm.chat("coach", U)
    assert scripted.requests == []
    assert services.privacy_log.list()[0].outcome == "blocked"


def test_pseudonymise_redacts_outbound_and_restores_reply(env):
    services, scripted = env
    services.household.create_person(PersonIn(display_name="Alex Example", role="adult"))
    setup_cloud(services)
    services.settings.set("privacy.pseudonymise", True, expected_version=0)
    scripted.replies = [
        lambda req: httpx.Response(
            200, json={"choices": [{"message": {"content": "Hello Adult A"}}], "usage": {}}
        )
    ]
    r = services.llm.chat("coach", [Message(role="user", content="I am Alex Example")])
    assert "Alex Example" not in str(scripted.requests[-1]) and "Adult A" in str(
        scripted.requests[-1]
    )
    assert r.text == "Hello Alex Example" and r.redactions >= 1


def test_pseudonymise_never_applies_to_local(env):
    services, scripted = env
    services.household.create_person(PersonIn(display_name="Alex Example", role="adult"))
    setup_local(services)
    services.settings.set("privacy.pseudonymise", True, expected_version=0)
    services.llm.chat("coach", [Message(role="user", content="I am Alex Example")])
    assert "Alex Example" in str(scripted.requests[-1])


def test_context_too_large_fails_fast(env):
    services, scripted = env
    setup_local(services)  # default local context 4096
    with pytest.raises(AllModelsFailed, match="too much text"):
        services.llm.chat("coach", [Message(role="user", content="x" * 40_000)])
    assert scripted.requests == []


def test_run_budget_enforced(env):
    services, _ = env
    setup_local(services)
    run = RunBudget(max_calls=1, max_tokens=100_000, max_gbp=1, max_seconds=60)
    services.llm.chat("coach", U, run=run)
    with pytest.raises(BudgetExceeded):
        services.llm.chat("coach", U, run=run)


def test_monthly_cap(env):
    services, _ = env
    setup_cloud(services)
    # make the model expensive and the cap tiny
    with services.db.transaction() as conn:
        conn.execute(
            "UPDATE llm_model SET price_in_usd_per_mtok = 1000000, price_out_usd_per_mtok = 1000000"
        )
    services.settings.set("llm.monthly_cap_gbp", 0.01, expected_version=0)
    with pytest.raises(BudgetExceeded, match="monthly|month"):
        services.llm.chat("coach", U)


class Verdict(BaseModel):
    category: str
    confidence: float


def test_structured_extracts_from_prose_and_repairs_once(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [
        {"content": 'Sure! {"category": "groceries"}'},  # invalid: missing confidence
        {"content": '```json\n{"category": "groceries", "confidence": 0.9}\n```'},
    ]
    v = services.llm.structured("categorise", U, Verdict)
    assert v == Verdict(category="groceries", confidence=0.9)
    assert len([r for r in scripted.requests if "messages" in r]) == 2


def test_structured_gives_up_after_one_repair(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [{"content": "nope"}, {"content": "still nope"}]
    with pytest.raises(LLMBadResponse):
        services.llm.structured("categorise", U, Verdict)


def test_structured_uses_native_schema_when_supported(env):
    services, scripted = env
    setup_local(services)
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_model SET supports_json_schema = 1")
    scripted.replies = [{"content": '{"category": "bills", "confidence": 0.5}'}]
    services.llm.structured("categorise", U, Verdict)
    assert scripted.requests[-1]["response_format"]["type"] == "json_schema"


def test_small_local_window_answers_a_short_prompt(env):
    services, scripted = env
    setup_local(services)  # default local context 4096
    services.llm.chat("coach", U, max_tokens=8000)
    assert scripted.requests[-1]["max_tokens"] <= 4096 - 10


def test_max_tokens_clamped_to_model_output_limit(env):
    services, scripted = env
    setup_local(services)
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_model SET context_window = 100000, max_output_tokens = 500")
    services.llm.chat("coach", U, max_tokens=4096)
    assert scripted.requests[-1]["max_tokens"] == 500


def test_retry_after_is_capped_at_30_seconds(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [
        httpx.Response(429, headers={"Retry-After": "9999"}, json={}),
        {"content": "ok"},
    ]
    services.llm.chat("coach", U)
    assert {"slept": 30.0} in scripted.requests


def test_value_error_is_not_retried_or_fallen_back(env, monkeypatch):
    services, scripted = env
    c = setup_local(services)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    chain = [
        {"connection_id": c.id, "model_id": "m-big"},
        {"connection_id": c.id, "model_id": "m-small"},
    ]
    services.router.set_task("coach", chain, local_only=False, expected_version=0)
    calls = []
    real = services.connections.provider

    def provider(*args, **kwargs):
        p = real(*args, **kwargs)

        def chat(req):
            calls.append(req.model)
            raise ValueError("There is no message content to send")

        monkeypatch.setattr(p, "chat", chat)
        return p

    monkeypatch.setattr(services.connections, "provider", provider)
    with pytest.raises(ValueError, match="no message content"):
        services.llm.chat("coach", U)
    assert calls == ["m-big"]
    assert scripted.requests == []
    assert services.breakers.failures == {}


def test_breaker_pauses_a_failing_connection(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(500, json={})] * 9
    for _ in range(3):
        with pytest.raises(AllModelsFailed):
            services.llm.chat("coach", U)
    sent = len(scripted.requests)
    with pytest.raises(AllModelsFailed, match="paused"):
        services.llm.chat("coach", U)
    assert len(scripted.requests) == sent


def test_missing_key_surfaces_secret_error(env):
    from tuppence.llm.connections import ApiKeyMissing

    services, _ = env
    c = setup_cloud(services)
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_connection SET secret_ref = NULL WHERE id = ?", [c.id])
    with pytest.raises(ApiKeyMissing):
        services.llm.chat("coach", U)


def test_new_run_applies_the_user_run_cap(env):
    from tuppence.config.models import Budgets

    services, _ = env
    services.settings.set("llm.run_cap_gbp", 0.25, expected_version=0)
    budgets = Budgets(max_llm_calls=5, max_tokens=1000, max_gbp=2.0, max_seconds=30)
    assert services.llm.new_run(budgets).max_gbp == 0.25


def cloud_priced(services, price=1000.0):
    c = setup_cloud(services)
    with services.db.transaction() as conn:
        conn.execute(
            "UPDATE llm_model SET price_in_usd_per_mtok = ?, price_out_usd_per_mtok = ?",
            [price, price],
        )
    return c


def sent_chats(scripted):
    return [r for r in scripted.requests if "messages" in r]


def test_unknown_price_cloud_counts_at_fallback_price_and_trips_monthly_cap(env):
    services, scripted = env
    month_now(services)
    setup_cloud(services)  # the fake provider lists no prices
    services.settings.set("llm.monthly_cap_gbp", 0.0, expected_version=0)
    with pytest.raises(BudgetExceeded, match="monthly|month"):
        services.llm.chat("coach", U)
    assert sent_chats(scripted) == []


def test_unknown_price_cloud_is_recorded_as_estimated(env):
    services, _ = env
    month_now(services)
    setup_cloud(services)
    r = services.llm.chat("coach", U)
    assert r.cost_gbp and r.cost_gbp > 0
    assert spend(services) == pytest.approx(r.cost_gbp)
    assert services.usage.summary(*month_now(services))["estimated_calls"] == 1


def test_unknown_price_trips_run_cap(env):
    services, scripted = env
    setup_cloud(services)
    run = RunBudget(max_calls=100, max_tokens=10_000_000, max_gbp=0.0001, max_seconds=600)
    with pytest.raises(BudgetExceeded, match="spending limit"):
        services.llm.chat("coach", U, run=run)
    assert sent_chats(scripted) == []


def test_missing_usage_is_estimated_and_counts(env):
    services, scripted = env
    month_now(services)
    cloud_priced(services)
    services.settings.set("llm.monthly_cap_gbp", 4.0, expected_version=0)
    big = "y" * 4000  # about 1000 output tokens, roughly £0.75 at this price
    scripted.replies = [
        lambda req: httpx.Response(200, json={"choices": [{"message": {"content": big}}]})
    ] * 10
    first = services.llm.chat("coach", U)
    assert first.cost_gbp and first.cost_gbp > 0.5
    assert spend(services) == pytest.approx(first.cost_gbp)
    with pytest.raises(BudgetExceeded, match="monthly|month"):
        for _ in range(9):
            services.llm.chat("coach", U)
    assert services.usage.summary(*month_now(services))["estimated_calls"] >= 1


def test_local_models_stay_free_under_a_zero_cap(env):
    services, _ = env
    setup_local(services)
    services.settings.set("llm.monthly_cap_gbp", 0.0, expected_version=0)
    services.settings.set("llm.run_cap_gbp", 0.0, expected_version=0)
    run = services.llm.new_run(
        Budgets(max_llm_calls=5, max_tokens=100_000, max_gbp=1.0, max_seconds=60)
    )
    r = services.llm.chat("coach", U, run=run)
    assert r.cost_gbp == 0.0


def test_run_cap_uses_projected_cost_before_the_call(env):
    services, scripted = env
    cloud_priced(services, price=100_000.0)
    services.settings.set("llm.monthly_cap_gbp", 10_000.0, expected_version=0)
    run = RunBudget(max_calls=10, max_tokens=1_000_000, max_gbp=0.10, max_seconds=600)
    with pytest.raises(BudgetExceeded, match="spending limit"):
        services.llm.chat("coach", U, run=run)
    assert sent_chats(scripted) == []


def test_pinned_local_task_is_blocked_at_http_even_if_locality_is_stale(env, monkeypatch):
    from tuppence.net import hosts

    services, scripted = env
    addr = {"ip": "192.168.1.5"}
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: [addr["ip"]])
    c = services.connections.create("custom", base_url="http://gpu-box:11434/v1")
    services.connections.test(c.id)
    assert services.connections.get(c.id).is_local
    addr["ip"] = "203.0.113.5"  # the name now resolves to a public address
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task(
        "coach",
        [{"connection_id": c.id, "model_id": "m-small"}],
        local_only=True,
        expected_version=0,
    )
    # Refused because a connection classed as local must still resolve inside the network.
    with pytest.raises(AllModelsFailed, match="now points outside"):
        services.llm.chat("coach", U)
    assert sent_chats(scripted) == []
    assert services.privacy_log.list()[0].outcome == "blocked"


def test_stale_local_connection_is_refused_even_with_local_only_off(env, monkeypatch):
    """Closes the stale-locality gap: no notice, pseudonymising or price for a public host."""
    from tuppence.net import hosts

    services, scripted = env
    addr = {"ip": "192.168.1.5"}
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: [addr["ip"]])
    c = services.connections.create("custom", base_url="http://gpu-box:11434/v1")
    services.connections.test(c.id)
    services.settings.set(
        "llm.simple_model", {"connection_id": c.id, "model_id": "m-small"}, expected_version=0
    )
    addr["ip"] = "203.0.113.5"
    with pytest.raises(AllModelsFailed, match="now points outside"):
        services.llm.chat("coach", U)
    assert sent_chats(scripted) == []
    assert services.privacy_log.list()[0].outcome == "blocked"


def test_blocked_cloud_call_is_logged_not_a_notice_error(env):
    services, scripted = env
    c = setup_cloud(services)
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_connection SET notice_acknowledged_at = NULL")
    assert services.connections.get(c.id).needs_notice
    services.settings.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(AllModelsBlocked) as exc:
        services.llm.chat("coach", U)
    assert "Confirm what" not in str(exc.value) and "Local only" in str(exc.value)
    assert services.privacy_log.list()[0].outcome == "blocked"
    assert sent_chats(scripted) == []


def test_malformed_replies_do_not_open_the_breaker(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(200, json={"choices": []})] * 3
    for _ in range(3):
        with pytest.raises(AllModelsFailed):
            services.llm.chat("coach", U)
    assert services.llm.chat("coach", U).text == "ok"


def test_client_errors_and_refusals_do_not_open_the_breaker(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(404, json={})] * 3
    for _ in range(3):
        with pytest.raises(AllModelsFailed):
            services.llm.chat("coach", U)
    assert services.breakers.opened_at == {}


def test_run_time_limit_bounds_retry_waits(env):
    services, scripted = env
    setup_local(services)
    t = [0.0]
    run = RunBudget(
        max_calls=10, max_tokens=1_000_000, max_gbp=1, max_seconds=10, monotonic=lambda: t[0]
    )

    def sleep(s):
        t[0] += s

    services.llm.sleep = sleep
    scripted.replies = [httpx.Response(429, headers={"Retry-After": "30"}, json={})] * 3
    with pytest.raises(BudgetExceeded, match="time"):
        services.llm.chat("coach", U, run=run)
    assert t[0] <= 10


def test_each_call_timeout_is_clamped_to_remaining_run_time(env, monkeypatch):
    services, _ = env
    setup_local(services)
    t = [0.0]
    run = RunBudget(
        max_calls=10, max_tokens=1_000_000, max_gbp=1, max_seconds=50, monotonic=lambda: t[0]
    )
    t[0] = 20.0
    seen = []
    real = services.connections.provider

    def provider(*args, **kwargs):
        seen.append(kwargs["timeout"])
        return real(*args, **kwargs)

    monkeypatch.setattr(services.connections, "provider", provider)
    services.llm.chat("coach", U, run=run)  # a local coach call would otherwise get 180 s
    assert seen == [30.0]


def test_run_time_exhausted_before_a_fallback_stops_the_run(env):
    services, scripted = env
    c = setup_local(services)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    chain = [
        {"connection_id": c.id, "model_id": "m-big"},
        {"connection_id": c.id, "model_id": "m-small"},
    ]
    services.router.set_task("coach", chain, local_only=False, expected_version=0)
    t = [0.0]
    run = RunBudget(
        max_calls=10, max_tokens=1_000_000, max_gbp=1, max_seconds=5, monotonic=lambda: t[0]
    )

    def slow_fail(req):
        t[0] += 6
        return httpx.Response(400, json={})

    scripted.replies = [slow_fail]
    with pytest.raises(BudgetExceeded, match="time"):
        services.llm.chat("coach", U, run=run)


def test_fallback_on_same_connection_waits_for_retry_after(env):
    services, scripted = env
    c = setup_local(services)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    chain = [
        {"connection_id": c.id, "model_id": "m-big"},
        {"connection_id": c.id, "model_id": "m-small"},
    ]
    services.router.set_task("coach", chain, local_only=False, expected_version=0)
    scripted.replies = [httpx.Response(429, headers={"Retry-After": "2"}, json={})] * 3 + [
        {"content": "from small"}
    ]
    r = services.llm.chat("coach", U)
    assert r.model_id == "m-small"
    assert [q for q in scripted.requests if "slept" in q] == [{"slept": 2.0}] * 3


def test_repair_prompt_names_the_problem(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [
        {"content": '{"category": "groceries"}'},
        {"content": '{"category": "g", "confidence": 1}'},
    ]
    services.llm.structured("categorise", U, Verdict, run_id="r1")
    assert "confidence: Field required" in scripted.requests[-1]["messages"][-1]["content"]
    with services.db.connection() as conn:
        rows = conn.execute("SELECT run_id FROM llm_usage").fetchall()
    assert [r["run_id"] for r in rows] == ["r1", "r1"]


def test_pin_with_no_local_model_says_so(env):
    from tuppence.llm.types import NoModelConfigured

    services, _ = env
    setup_cloud(services)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task("read", [], local_only=True, expected_version=0)
    with pytest.raises(NoModelConfigured, match="only models on your own computer"):
        services.llm.chat("read", U)


def test_failed_calls_are_counted_apart(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(400, json={}), {"content": "ok"}]
    with pytest.raises(AllModelsFailed):
        services.llm.chat("coach", U)
    services.llm.chat("coach", U)
    summary = services.usage.summary(*month_now(services))
    assert summary["calls"] == 1 and summary["failed_calls"] == 1
    assert summary["by_task"]["coach"]["failed_calls"] == 1


def test_concurrent_calls_cannot_both_fit_under_the_monthly_cap(env):
    import threading

    services, scripted = env
    cloud_priced(services)
    services.settings.set("llm.monthly_cap_gbp", 4.0, expected_version=0)
    barrier = threading.Barrier(2, timeout=3)

    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m-small"}]})
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait()  # the capped call never arrives, so this times out
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 4000},
            },
        )

    scripted.handler = handler
    results = []

    def go():
        try:
            results.append(services.llm.chat("coach", U, max_tokens=4000).text)
        except BudgetExceeded as exc:
            results.append("capped" if "spending cap" in str(exc) else str(exc))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["capped", "ok"]


def two_models(services, first, second, local_only=False):
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task(
        "coach",
        [
            {"connection_id": first.id, "model_id": "m-small"},
            {"connection_id": second.id, "model_id": "m-small"},
        ],
        local_only=local_only,
        expected_version=0,
    )


def new_local(services):
    c = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
    services.connections.test(c.id)
    return c


def drop_key(services, connection):
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_connection SET secret_ref = NULL WHERE id = ?", [connection.id])


def blocked_rows(services):
    return [e for e in services.privacy_log.list() if e.outcome == "blocked"]


def test_local_only_skips_keyless_cloud_and_local_answers(env):
    services, scripted = env
    cloud = setup_cloud(services)
    local = new_local(services)
    drop_key(services, cloud)
    two_models(services, cloud, local)
    services.settings.set("privacy.local_only", True, expected_version=0)
    r = services.llm.chat("coach", U)  # no ApiKeyMissing
    assert r.connection_id == local.id
    assert len(blocked_rows(services)) == 1
    assert len(sent_chats(scripted)) == 1


def test_local_only_keyless_cloud_alone_is_a_block_not_a_key_error(env):
    services, _ = env
    cloud = setup_cloud(services)
    drop_key(services, cloud)
    services.settings.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(AllModelsBlocked):
        services.llm.chat("coach", U)
    assert len(blocked_rows(services)) == 1


def test_local_only_block_comes_before_the_monthly_cap(env):
    services, _ = env
    setup_cloud(services)
    services.settings.set("privacy.local_only", True, expected_version=0)
    services.settings.set("llm.monthly_cap_gbp", 0.0, expected_version=0)
    with pytest.raises(AllModelsBlocked):
        services.llm.chat("coach", U)
    assert len(blocked_rows(services)) == 1


def test_pin_message_names_the_task_not_the_global_setting(env):
    services, _ = env
    cloud = setup_cloud(services)
    local = new_local(services)
    two_models(services, cloud, local, local_only=True)
    # The pin filters the cloud model out of the chain; point the chain at cloud only.
    services.router.set_task(
        "coach",
        [{"connection_id": cloud.id, "model_id": "m-small"}],
        local_only=False,
        expected_version=1,
    )
    block = services.llm._log_block(cloud, "coach", True)
    assert "This task is set to use only local models" in str(block)


def test_zero_run_cap_skips_paid_model_and_local_answers(env):
    services, scripted = env
    cloud = cloud_priced(services, price=1.0)
    local = new_local(services)
    two_models(services, cloud, local)
    services.settings.set("llm.run_cap_gbp", 0.0, expected_version=0)
    run = services.llm.new_run(
        Budgets(max_llm_calls=5, max_tokens=100_000, max_gbp=1.0, max_seconds=60)
    )
    r = services.llm.chat("coach", U, run=run)
    assert r.connection_id == local.id and len(sent_chats(scripted)) == 1


def test_partly_spent_run_falls_back_to_the_cheaper_model(env):
    services, _ = env
    cloud = cloud_priced(services, price=60.0)
    local = new_local(services)
    two_models(services, cloud, local)
    run = RunBudget(max_calls=10, max_tokens=1_000_000, max_gbp=0.25, max_seconds=600)
    run.gbp = 0.10
    assert services.llm.chat("coach", U, run=run).connection_id == local.id


def test_run_cap_with_no_model_that_fits_raises_budget_exceeded(env):
    services, scripted = env
    cloud = cloud_priced(services, price=1.0)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    chain = [
        {"connection_id": cloud.id, "model_id": "m-small"},
        {"connection_id": cloud.id, "model_id": "m-big"},
    ]
    services.router.set_task("coach", chain, local_only=False, expected_version=0)
    run = RunBudget(max_calls=10, max_tokens=1_000_000, max_gbp=0.0, max_seconds=600)
    with pytest.raises(BudgetExceeded, match="spending limit"):
        services.llm.chat("coach", U, run=run)
    assert sent_chats(scripted) == []


def test_run_call_limit_still_ends_the_run(env):
    services, _ = env
    cloud = cloud_priced(services, price=1.0)
    local = new_local(services)
    two_models(services, cloud, local)
    run = RunBudget(max_calls=0, max_tokens=1_000_000, max_gbp=1.0, max_seconds=600)
    with pytest.raises(BudgetExceeded, match="calls"):
        services.llm.chat("coach", U, run=run)


def test_guard_enforces_the_decision_if_local_only_flips_mid_call(env, monkeypatch):
    services, scripted = env
    setup_cloud(services)
    with services.db.transaction() as conn:
        conn.execute("UPDATE llm_connection SET notice_acknowledged_at = NULL")
    services.settings.set("privacy.local_only", True, expected_version=0)
    real_get = services.settings.get
    reads = {"n": 0}

    def get(key):
        if key == "privacy.local_only":
            reads["n"] += 1
            if reads["n"] > 1:
                return False  # the user switches Local only off right after chat() decided
        return real_get(key)

    monkeypatch.setattr(services.settings, "get", get)
    with pytest.raises(AllModelsBlocked):
        services.llm.chat("coach", U)
    assert sent_chats(scripted) == []


def test_local_only_decision_reaches_the_guard_for_a_local_connection(env, monkeypatch):
    services, _ = env
    setup_local(services)
    seen = []
    real = services.connections.provider

    def provider(*args, **kwargs):
        seen.append(kwargs["require_local"])
        return real(*args, **kwargs)

    monkeypatch.setattr(services.connections, "provider", provider)
    services.settings.set("privacy.local_only", True, expected_version=0)
    services.llm.chat("coach", U)
    assert seen == [True]


def test_cooldown_wait_is_skipped_for_a_model_that_is_then_skipped(env):
    services, scripted = env
    a = new_local(services)
    b = new_local(services)
    with services.db.transaction() as conn:
        conn.execute(
            "UPDATE llm_model SET context_window = 300"
            " WHERE connection_id = ? AND model_id = 'm-small'",
            [a.id],
        )
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task(
        "coach",
        [
            {"connection_id": a.id, "model_id": "m-big"},
            {"connection_id": a.id, "model_id": "m-small"},
            {"connection_id": b.id, "model_id": "m-small"},
        ],
        local_only=False,
        expected_version=0,
    )
    t = [0.0]
    run = RunBudget(
        max_calls=10, max_tokens=1_000_000, max_gbp=1, max_seconds=80, monotonic=lambda: t[0]
    )
    services.llm.sleep = lambda s: t.__setitem__(0, t[0] + s)
    scripted.replies = [httpx.Response(429, headers={"Retry-After": "30"}, json={})] * 3
    r = services.llm.chat("coach", [Message(role="user", content="y" * 400)], run=run)
    assert r.connection_id == b.id


def test_partial_usage_is_filled_in_per_side(env):
    services, scripted = env
    cloud_priced(services, price=10.0)
    scripted.replies = [
        lambda req: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {"completion_tokens": 5}},
        )
    ]
    r = services.llm.chat("coach", [Message(role="user", content="x" * 40_000)])
    assert r.usage.input_tokens > 9000 and r.usage.output_tokens == 5


def test_a_clamped_timeout_is_the_runs_limit_not_a_sick_server(env):
    from tuppence.llm.types import LLMTimeout

    services, _ = env
    setup_local(services)
    t = [0.0]
    run = RunBudget(
        max_calls=10, max_tokens=1_000_000, max_gbp=1, max_seconds=50, monotonic=lambda: t[0]
    )
    t[0] = 20.0

    def timeout(req):
        raise LLMTimeout("timed out")

    real = services.connections.provider

    def provider(*args, **kwargs):
        p = real(*args, **kwargs)
        p.chat = timeout
        return p

    services.connections.provider = provider
    with pytest.raises(BudgetExceeded, match="time"):
        services.llm.chat("coach", U, run=run)
    assert services.breakers.failures == {}


def test_monthly_cap_error_names_the_monthly_cap(env):
    services, _ = env
    setup_cloud(services)
    services.settings.set("llm.monthly_cap_gbp", 0.0, expected_version=0)
    with pytest.raises(BudgetExceeded, match="month"):
        services.llm.chat("coach", U)


def test_notice_required_carries_the_connection_id(env):
    services, _ = env
    c = services.connections.create("openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1")
    services.connections.test(c.id)
    services.settings.set(
        "llm.simple_model", {"connection_id": c.id, "model_id": "m-small"}, expected_version=0
    )
    with pytest.raises(NoticeRequired) as exc:
        services.llm.chat("coach", U)
    assert exc.value.connection_id == c.id


def test_provider_error_text_never_reaches_the_ledger_or_the_reasons(env):
    services, scripted = env
    setup_local(services)

    def leak(req):
        raise httpx.LocalProtocolError("Illegal header value b'Bearer PROBEKEY0123456789'")

    scripted.handler = leak
    with pytest.raises(AllModelsFailed) as exc:
        services.llm.chat("coach", U)
    assert "PROBEKEY" not in str(exc.value) and "LLMConnectionError" in str(exc.value)
    with services.db.connection() as conn:
        errors = [r[0] for r in conn.execute("SELECT error FROM llm_usage")]
    assert errors == ["LLMConnectionError"]


def test_http_error_bodies_are_not_stored_or_returned(env):
    services, scripted = env
    setup_local(services)
    scripted.replies = [httpx.Response(400, json={"error": "bad key PROBEKEY0123456789"})]
    with pytest.raises(AllModelsFailed) as exc:
        services.llm.chat("coach", U)
    assert "PROBEKEY" not in str(exc.value) and "LLMHTTPError (HTTP 400)" in str(exc.value)
    with services.db.connection() as conn:
        assert [r[0] for r in conn.execute("SELECT error FROM llm_usage")] == [
            "LLMHTTPError (HTTP 400)"
        ]
