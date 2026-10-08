"""The knowledge stores plus a scriptable model, for specialist tests."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from evals import oracle
from evals.oracle import OracleLLM

from fakes.scripted import Scripted
from knowledge.helpers import KnowledgeEnv
from tuppence.agents.categoriser import Categoriser, CategoriserDeps, PersonRef
from tuppence.agents.runtime import AnalysisContext, LayeredBudget
from tuppence.config.models import AgentManifest
from tuppence.knowledge.merchants import MerchantStore
from tuppence.knowledge.models import Decision
from tuppence.knowledge.refiles import RefileStore
from tuppence.knowledge.rules import RuleStore
from tuppence.llm.budget import RunBudget
from tuppence.llm.types import LLMBadResponse


def manifest(name: str) -> AgentManifest:
    """A default agent manifest, as shipped (no preset, no user file)."""
    text = resources.files("tuppence.config.defaults").joinpath("agents", f"{name}.toml")
    return AgentManifest.model_validate(tomllib.loads(text.read_text(encoding="utf-8")))


@dataclass
class ScriptedLLM:
    """Answers from `script` first (a str is the raw reply, a dict its JSON, an exception is
    raised), then the oracle. Records every call. Follows the LLM client's budget protocol: a
    scripted exception is raised before the call is counted; otherwise `check_limits()` and
    `start_call()` before replying and `record()` after."""

    script: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def structured(self, task, messages, schema, *, max_tokens=4096, run=None, run_id=None):
        self.calls.append(
            {
                "task": task,
                "user": messages[-1].content,
                "messages": [m.content for m in messages],
                "max_tokens": max_tokens,
                "run_id": run_id,
            }
        )
        if self.script and isinstance(self.script[0], BaseException):
            raise self.script.pop(0)
        if run is not None:
            run.check_limits()
            run.start_call()
        if self.script:
            item = self.script.pop(0)
            if run is not None:
                run.record(100, 0.001)
            try:
                return schema.model_validate_json(
                    item if isinstance(item, str) else json.dumps(item)
                )
            except ValueError as exc:
                raise LLMBadResponse(str(exc)) from exc
        answer = OracleLLM().structured(task, messages, schema, max_tokens=max_tokens)
        if run is not None:
            run.record(100, 0.001)
        return answer


def oracle_handler(scripted: Scripted):
    """For the real LLM client: scripted replies first (an httpx.Response, {"content": ...}, or
    a function of the request body that returns the content), then the oracle."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return scripted._default(request)
        body = json.loads(request.content)
        scripted.requests.append(body)
        if scripted.replies:
            reply = scripted.replies.pop(0)
            if isinstance(reply, httpx.Response):
                return reply
            content = reply(body) if callable(reply) else reply["content"]
        else:
            content = oracle.reply(body["messages"])
        return httpx.Response(
            200,
            json={
                "model": body.get("model", "m"),
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    return handle


@dataclass
class AgentEnv(KnowledgeEnv):
    merchants: MerchantStore
    rules: RuleStore
    refiles: RefileStore
    llm: Any  # a ScriptedLLM, or the app's LLMClient
    categoriser_manifest: AgentManifest
    window: int = 8192
    last_context: AnalysisContext | None = None

    @classmethod
    def around(cls, base: KnowledgeEnv, llm: Any) -> AgentEnv:
        rules = RuleStore(base.db, base.versions, base.understanding)
        rules.seed()
        return cls(
            base.db,
            base.versions,
            base.categories,
            base.understanding,
            merchants=MerchantStore(base.db),
            rules=rules,
            refiles=RefileStore(base.db, base.versions, base.understanding),
            llm=llm,
            categoriser_manifest=manifest("categoriser"),
        )

    @classmethod
    def create(cls, tmp_path: Path) -> AgentEnv:
        return cls.around(KnowledgeEnv.create(tmp_path), ScriptedLLM())

    def window_for(self, task: str) -> int:
        return self.window

    def categoriser(self) -> Categoriser:
        return Categoriser(
            CategoriserDeps(
                db=self.db,
                versions=self.versions,
                understanding=self.understanding,
                categories=self.categories,
                merchants=self.merchants,
                rules=self.rules,
                refiles=self.refiles,
                llm=self.llm,
                context_window=self.window_for,
                people=lambda: [PersonRef("p_alex", "Alex Example", "adult")],
                manifest=lambda: self.categoriser_manifest,
            )
        )

    def context(self, *, calls: int = 50, run_calls: int = 100) -> AnalysisContext:
        own = RunBudget(max_calls=calls, max_tokens=10**7, max_gbp=10, max_seconds=600)
        run = RunBudget(max_calls=run_calls, max_tokens=10**7, max_gbp=10, max_seconds=600)
        return AnalysisContext(
            run_id="run_test",
            budgets={name: LayeredBudget(own, run) for name in ("categoriser", "commitments")},
        )

    def categorise(self, ids: list[str], **kw: Any) -> dict[str, Any]:
        graph = self.categoriser().build()
        self.last_context = self.context(**kw)
        out = graph.invoke({"run_id": "run_test", "scope_ids": ids}, context=self.last_context)
        return out["categoriser"]

    def prompts(self) -> list[str]:
        """Every message the scripted model was sent, in order."""
        return [content for call in self.llm.calls for content in call["messages"]]


@dataclass
class ClientEnv:
    """An AgentEnv on the app's own services: the real LLM client, its router, budgets and
    usage ledger, with every HTTP request answered by `scripted` (then the oracle)."""

    agent: AgentEnv
    services: Any
    scripted: Scripted

    def local_model(self, *, window: int | None = None) -> int:
        services = self.services
        conn = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
        services.connections.test(conn.id)
        services.settings.set(
            "llm.simple_model",
            {"connection_id": conn.id, "model_id": "m-small"},
            expected_version=0,
        )
        if window is not None:
            model = services.connections.model(conn.id, "m-small")
            services.connections.set_context_window(conn.id, "m-small", window, model.version)
        return services.router.chain_for("categorise")[0][1].context_window

    def cloud_model(self, *, acknowledge: bool) -> str:
        services = self.services
        conn = services.connections.create(
            "openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1"
        )
        services.connections.test(conn.id)
        if acknowledge:
            services.connections.acknowledge_notice(
                conn.id, expected_version=services.connections.get(conn.id).version
            )
        services.settings.set(
            "llm.simple_model",
            {"connection_id": conn.id, "model_id": "m-small"},
            expected_version=0,
        )
        return conn.id

    def chats(self) -> list[dict[str, Any]]:
        """The chat requests that left (model lists aren't counted)."""
        return list(self.scripted.requests)


def crowd(env: AgentEnv) -> list[str]:
    """Twelve rows of four merchants that the scripted model files under food.groceries, with
    the crowding threshold lowered to 8: the merchants are listed M1..M4 by spend."""
    env.categoriser_manifest.limits["crowded_category_rows"] = 8
    names = ["GREENBASKET STORES", "VALUEMART", "FARMGATE BUTCHERS", "CRUSTY BAKERY"]
    sizes = [5000, 4000, 1500, 1000]
    ids = [env.add_txn(date(2026, 9, d), -sizes[d % 4], names[d % 4]) for d in range(1, 13)]
    env.llm.script = [
        {
            "transactions": [
                {
                    "ref": f"T{n}",
                    "category_id": "food.groceries",
                    "who": "household",
                    "confidence": 0.9,
                    "reason": "food",
                }
                for n in range(1, 13)
            ]
        }
    ]
    return ids


def file_as(env: KnowledgeEnv, txn_id: str, category_id: str, merchants: MerchantStore) -> None:
    """Give a transaction its merchant and a model decision, as the Categoriser would."""
    with env.db.transaction() as conn:
        raw = conn.execute(
            'SELECT raw_description FROM "transaction" WHERE id = ?', [txn_id]
        ).fetchone()[0]
        merchant = merchants.resolve(conn, raw, None)
        assert merchant is not None
        env.understanding.apply(
            conn,
            txn_id,
            Decision(
                decided_by="llm",
                authority=20,
                status="inferred",
                confidence=0.9,
                category_id=category_id,
                merchant_id=merchant.id,
            ),
            actor="test",
            knowledge_version=0,
        )
