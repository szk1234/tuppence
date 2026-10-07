import httpx
import pytest
from pydantic import BaseModel

from tuppence.core.household import PersonIn
from tuppence.llm.budget import RunBudget
from tuppence.llm.types import AllModelsFailed, BudgetExceeded, LLMBadResponse, Message


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


def test_chat_records_usage_and_privacy_log(env):
    services, scripted = env
    c = setup_local(services)
    scripted.replies = [{"content": "hi there"}]
    r = services.llm.chat("coach", U)
    assert r.text == "hi there" and r.connection_id == c.id and r.cost_gbp == 0.0
    summary = services.usage.summary(
        *map(int, __import__("datetime").date.today().isoformat().split("-")[:2])
    )
    assert summary["calls"] == 1
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
    with pytest.raises(AllModelsFailed, match="Confirm what"):
        services.llm.chat("coach", U)


def test_local_only_blocks_cloud_before_sending(env):
    services, scripted = env
    setup_cloud(services)
    services.settings.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(AllModelsFailed, match="Local only"):
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
    with pytest.raises(AllModelsFailed, match="spending cap"):
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
    scripted.replies = [httpx.Response(400, json={})] * 3
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
