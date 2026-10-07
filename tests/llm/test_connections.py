import httpx
import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict
from tuppence.core.secrets import EncryptedDbStore, SecretUnreadable, load_or_create_key
from tuppence.llm.catalogue import CatalogueEntry, ModelCatalogue
from tuppence.llm.connections import ConnectionRegistry, detect_local
from tuppence.net.client import CallContext


def fake_server(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "small-1"}, {"id": "vendor/known"}]})
    return httpx.Response(404)


@pytest.fixture
def seen():
    return []


@pytest.fixture
def reg(tmp_path, seen):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    secrets = EncryptedDbStore(db, load_or_create_key(tmp_path, env={}))
    cat = ModelCatalogue(
        [
            CatalogueEntry(
                id="vendor/known",
                aliases=["known"],
                context_window=64000,
                supports_json_schema=True,
                price_in_usd_per_mtok=1.0,
                price_out_usd_per_mtok=2.0,
            )
        ]
    )

    def client_factory(ctx: CallContext, timeout: float) -> httpx.Client:
        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return fake_server(req)

        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    return ConnectionRegistry(db, secrets, cat, client_factory=client_factory)


def test_create_local_connection_without_key(reg):
    c = reg.create("ollama")
    assert c.is_local and not c.has_key and not c.needs_notice
    assert c.base_url == "http://127.0.0.1:11434/v1"


def test_cloud_requires_key_and_notice(reg):
    with pytest.raises(InputError):
        reg.create("openai")
    c = reg.create("openai", api_key="sk-x")
    assert not c.is_local and c.has_key and c.needs_notice
    assert reg.api_key(c.id) == "sk-x"
    assert "sk-x" not in c.model_dump_json()
    c2 = reg.acknowledge_notice(c.id)
    assert not c2.needs_notice


def test_cloud_preset_pointed_at_localhost_is_still_cloud(reg):
    c = reg.create("openai", api_key="k", base_url="http://127.0.0.1:9999/v1")
    assert not c.is_local


def test_custom_connection_locality_follows_host(reg):
    assert reg.create("custom", base_url="http://192.168.1.50:8080/v1").is_local
    assert not reg.create("custom", base_url="https://8.8.8.8/v1", api_key="k").is_local


def test_test_connection_stores_enriched_models(reg):
    c = reg.create("ollama")
    models = {m.model_id: m for m in reg.test(c.id)}
    assert models["small-1"].context_window == 4096 and models["small-1"].source == "default"
    assert models["small-1"].price_in_usd_per_mtok == 0.0
    known = models["vendor/known"]
    assert known.context_window == 64000 and known.supports_json_schema
    assert known.source == "catalogue"
    assert {m.model_id for m in reg.models(c.id)} == {"small-1", "vendor/known"}


def test_cloud_defaults_when_nothing_known(reg):
    c = reg.create("openai", api_key="k")
    m = {x.model_id: x for x in reg.test(c.id)}["small-1"]
    assert m.context_window == 32768 and m.price_in_usd_per_mtok is None
    assert not (m.supports_tools or m.supports_json_schema or m.supports_vision)


def test_user_context_window_override_survives_retest(reg):
    c = reg.create("ollama")
    reg.test(c.id)
    reg.set_context_window(c.id, "small-1", 16384)
    reg.test(c.id)
    assert reg.model(c.id, "small-1").context_window == 16384
    assert reg.model(c.id, "small-1").source == "user"


def test_model_override_is_versioned_and_handles_slash_ids(reg):
    c = reg.create("ollama")
    reg.test(c.id)
    m = reg.model(c.id, "vendor/known")
    updated = reg.set_context_window(c.id, "vendor/known", 8192, expected_version=m.version)
    assert updated.version == m.version + 1
    with pytest.raises(VersionConflict):
        reg.set_context_window(c.id, "vendor/known", 9000, expected_version=m.version)
    assert reg.model(c.id, "vendor/known").context_window == 8192


def test_update_and_delete(reg):
    c = reg.create("openai", api_key="old")
    c = reg.update(c.id, {"api_key": "new", "name": "Work OpenAI"}, expected_version=1)
    assert reg.api_key(c.id) == "new" and c.name == "Work OpenAI" and c.version == 2
    reg.delete(c.id)
    assert reg.list() == []


def test_stale_update_does_not_rotate_the_key(reg):
    c = reg.create("openai", api_key="old")
    reg.update(c.id, {"name": "A"}, expected_version=1)
    with pytest.raises(VersionConflict):
        reg.update(c.id, {"api_key": "new"}, expected_version=1)
    assert reg.api_key(c.id) == "old"
    with reg.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM secret").fetchone()[0] == 1


def test_header_values_are_secret_and_sent(reg, seen):
    c = reg.create("custom", base_url="http://10.0.0.5:8000/v1", headers={"X-Org": "hush-value"})
    assert c.header_names == ["X-Org"]
    assert "hush-value" not in c.model_dump_json()
    with reg.db.connection() as conn:
        row = conn.execute("SELECT headers FROM llm_connection").fetchone()
    assert "hush-value" not in row[0]
    reg.test(c.id)
    assert seen[-1].headers["x-org"] == "hush-value"
    c = reg.update(c.id, {"headers": {}}, expected_version=c.version)
    assert c.header_names == []
    with reg.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM secret").fetchone()[0] == 0


def test_forget_keys_clears_has_key(reg):
    c = reg.create("openai", api_key="k", headers={"X-A": "1"})
    reg.forget_keys()
    got = reg.get(c.id)
    assert not got.has_key
    assert got.header_names == ["X-A"]


def test_missing_secret_is_a_clear_error(reg):
    c = reg.create("openai", api_key="k")
    with reg.db.transaction() as conn:
        conn.execute("DELETE FROM secret")
    with pytest.raises(SecretUnreadable):
        reg.test(c.id)


def test_detect_local_reports_responding_servers():
    def factory(ctx, timeout):
        def handler(req):
            if req.url.port == 11434:
                return httpx.Response(200, json={"data": [{"id": "llama"}]})
            raise httpx.ConnectError("refused", request=req)

        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    found = detect_local(factory)
    assert [(d.preset, d.model_count) for d in found] == [("ollama", 1)]
