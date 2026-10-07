import httpx
import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict
from tuppence.core.secrets import (
    EncryptedDbStore,
    SecretError,
    SecretUnreadable,
    load_or_create_key,
)
from tuppence.llm.catalogue import CatalogueEntry, ModelCatalogue
from tuppence.llm.connections import ApiKeyMissing, ConnectionRegistry, detect_local
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
    c2 = reg.acknowledge_notice(c.id, expected_version=c.version)
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
    assert known.supports_json_schema
    # A local server's window comes from the server or the user, never the catalogue.
    assert known.context_window == 4096 and known.source == "default"
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
    assert [(h.name, h.has_value) for h in c.headers] == [("X-Org", True)]
    assert "hush-value" not in c.model_dump_json()
    with reg.db.connection() as conn:
        row = conn.execute("SELECT headers FROM llm_connection").fetchone()
    assert "hush-value" not in row[0]
    reg.test(c.id)
    assert seen[-1].headers["x-org"] == "hush-value"
    c = reg.update(c.id, {"headers": {}}, expected_version=c.version)
    assert c.headers == []
    with reg.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM secret").fetchone()[0] == 0


def test_forget_keys_clears_has_key(reg):
    c = reg.create("openai", api_key="k", headers={"X-A": "1"})
    reg.forget_keys()
    got = reg.get(c.id)
    assert not got.has_key
    assert [(h.name, h.has_value) for h in got.headers] == [("X-A", False)]


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


def test_cloud_catalogue_window_and_blank_key_edit(reg):
    c = reg.create("openai", api_key="k")
    models = {m.model_id: m for m in reg.test(c.id)}
    assert models["vendor/known"].context_window == 64000
    assert models["vendor/known"].source == "catalogue"
    c = reg.update(c.id, {"api_key": "", "name": "Renamed"}, expected_version=c.version)
    assert reg.api_key(c.id) == "k" and c.has_key
    c = reg.update(c.id, {"api_key": None}, expected_version=c.version)
    assert reg.api_key(c.id) == "k"
    with pytest.raises(InputError):
        reg.update(c.id, {"clear_api_key": True}, expected_version=c.version)


def test_clear_key_allowed_for_optional_key_presets(reg):
    c = reg.create("custom", base_url="http://10.0.0.5/v1", api_key="k")
    c = reg.update(c.id, {"clear_api_key": True}, expected_version=c.version)
    assert not c.has_key and reg.api_key(c.id) is None
    with pytest.raises(InputError):
        reg.update(c.id, {"api_key": "x", "clear_api_key": True}, expected_version=c.version)


def test_keyless_cloud_connection_fails_before_any_request(reg, seen):
    c = reg.create("openai", api_key="k")
    reg.forget_keys()
    with pytest.raises(ApiKeyMissing) as exc:
        reg.test(c.id)
    assert "An API key is needed for OpenAI" in str(exc.value)
    assert seen == []


def test_header_validation_never_echoes_input(reg):
    pasted = "Authorization: Bearer sk-PASTED-SECRET"
    with pytest.raises(InputError) as exc:
        reg.create("custom", base_url="http://10.0.0.5/v1", headers={pasted: "v"})
    assert "sk-PASTED" not in str(exc.value) and "Authorization" not in str(exc.value)
    with pytest.raises(InputError, match="ASCII"):
        reg.create("custom", base_url="http://10.0.0.5/v1", headers={"X-A": "caf\u00e9"})


@pytest.mark.parametrize("headers", ["x", ["a"], {"A": 1}, {1: "a"}])
def test_bad_headers_are_input_errors(reg, headers):
    c = reg.create("custom", base_url="http://10.0.0.5/v1")
    with pytest.raises(InputError):
        reg.update(c.id, {"headers": headers}, expected_version=c.version)


@pytest.mark.parametrize(
    "changes", [{"name": None}, {"name": "  "}, {"name": 5}, {"enabled": "false"}, {"enabled": 1}]
)
def test_bad_changes_are_input_errors(reg, changes):
    c = reg.create("ollama")
    with pytest.raises(InputError):
        reg.update(c.id, changes, expected_version=c.version)
    assert reg.get(c.id).name == "Ollama" and reg.get(c.id).enabled


def test_base_url_credentials_and_query_rejected(reg):
    with pytest.raises(InputError, match="credentials"):
        reg.create("custom", base_url="https://user:pw@proxy.example.com/v1")
    with pytest.raises(InputError, match="query"):
        reg.create("custom", base_url="https://x.example.com/v1?api-version=1", api_key="k")


def test_notice_resets_when_host_changes_to_non_local(reg):
    c = reg.create("custom", base_url="https://8.8.8.8/v1", api_key="k")
    c = reg.acknowledge_notice(c.id, expected_version=c.version)
    assert not c.needs_notice
    c = reg.update(c.id, {"base_url": "https://8.8.4.4/v1"}, expected_version=c.version)
    assert c.needs_notice  # cloud -> other cloud host
    c = reg.acknowledge_notice(c.id, expected_version=c.version)
    c = reg.update(c.id, {"base_url": "https://8.8.4.4/v2"}, expected_version=c.version)
    assert not c.needs_notice  # same host
    c = reg.update(c.id, {"base_url": "http://10.0.0.9/v1"}, expected_version=c.version)
    assert c.is_local and not c.needs_notice  # cloud -> local
    c = reg.update(c.id, {"base_url": "https://8.8.8.8/v1"}, expected_version=c.version)
    assert c.needs_notice  # local -> cloud


def test_test_recomputes_locality(reg):
    c = reg.create("custom", base_url="http://10.0.0.5/v1")
    with reg.db.transaction() as conn:
        conn.execute("UPDATE llm_connection SET is_local = 0")
    reg.test(c.id)
    assert reg.get(c.id).is_local


def test_delete_removes_key_and_header_secrets(reg):
    c = reg.create("custom", base_url="http://10.0.0.5/v1", api_key="k", headers={"X-A": "1"})
    with reg.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM secret").fetchone()[0] == 2
    reg.delete(c.id)
    with reg.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM secret").fetchone()[0] == 0


def test_stale_version_conflicts_before_touching_secrets(reg):
    c = reg.create("openai", api_key="old")
    reg.update(c.id, {"name": "A"}, expected_version=1)
    with pytest.raises(VersionConflict):
        reg.update(c.id, {"api_key": "new"}, expected_version=1)
    with reg.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM secret").fetchone()[0] == 1


def test_secret_cleanup_failure_after_commit_is_not_an_error(reg, monkeypatch):
    c = reg.create("openai", api_key="old")

    def boom(ref):
        raise SecretError("keychain locked")

    monkeypatch.setattr(reg.secrets, "delete", boom)
    c = reg.update(c.id, {"api_key": "new"}, expected_version=c.version)
    assert reg.api_key(c.id) == "new"
    reg.delete(c.id)
    assert reg.list() == []


BAD_KEYS = ["sk-abc\x0bdef", "sk abc", "sk-café", "k" * 4001, "sk-a\tb", "sk-a\nb"]


@pytest.mark.parametrize(
    "key", BAD_KEYS, ids=["control", "space", "non-ascii", "too-long", "tab", "newline"]
)
def test_bad_api_keys_are_rejected_without_echoing_them(reg, key):
    with pytest.raises(InputError) as exc:
        reg.create("openai", api_key=key)
    assert key not in str(exc.value) and "sk-" not in str(exc.value)
    c = reg.create("openai", api_key="sk-good-123")
    with pytest.raises(InputError):
        reg.update(c.id, {"api_key": key}, expected_version=c.version)
    assert reg.api_key(c.id) == "sk-good-123"


def test_surrounding_spaces_on_a_pasted_key_are_trimmed(reg):
    c = reg.create("openai", api_key="  sk-good-123 \n")
    assert reg.api_key(c.id) == "sk-good-123"


def test_header_names_and_values_are_trimmed_like_keys(reg, seen):
    c = reg.create("custom", base_url="http://10.0.0.5:8000/v1", headers={" X-Org ": " abc \n"})
    reg.test(c.id)
    assert seen[-1].headers["x-org"] == "abc"
    c = reg.update(c.id, {"headers": {"X-Org": "  def  "}}, expected_version=c.version)
    reg.test(c.id)
    assert seen[-1].headers["x-org"] == "def"


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Gw": "abc\x01def"},
        {"X-Gw": "abc\tdef"},
        {"X-Gw": "café"},
        {"X-Gw": "v" * 4001},
        {"X(Gw)": "v"},
        {"X" * 101: "v"},
        {f"X-{i}": "v" for i in range(21)},
    ],
    ids=["control", "tab", "non-ascii", "long-value", "bad-name", "long-name", "too-many"],
)
def test_bad_header_names_and_values_are_rejected(reg, headers):
    with pytest.raises(InputError) as exc:
        reg.create("custom", base_url="http://10.0.0.5/v1", headers=headers)
    assert "abc" not in str(exc.value) and "caf" not in str(exc.value)
    c = reg.create("custom", base_url="http://10.0.0.5/v1")
    with pytest.raises(InputError):
        reg.update(c.id, {"headers": headers}, expected_version=c.version)


def test_header_values_may_hold_a_gateway_credential(reg):
    c = reg.create("custom", base_url="http://10.0.0.5/v1", headers={"Authorization": "Basic a2V5"})
    assert [h.name for h in c.headers] == ["Authorization"]


def test_connection_names_are_bounded(reg):
    with pytest.raises(InputError):
        reg.create("ollama", name="x" * 201)
    c = reg.create("ollama", name="y" * 200)
    with pytest.raises(InputError):
        reg.update(c.id, {"name": "x" * 201}, expected_version=c.version)


@pytest.fixture
def keychain_reg(tmp_path, reg):
    import keyring

    from fakes.keyrings import RefusingKeyring
    from tuppence.core.secrets import KeyringStore

    previous = keyring.get_keyring()
    backend = RefusingKeyring()
    keyring.set_keyring(backend)
    reg.secrets = KeyringStore(db=reg.db)
    yield reg, backend
    keyring.set_keyring(previous)


def test_keychain_refusing_reads_is_a_secret_error_before_sending(keychain_reg, seen):
    from tuppence.core.secrets import SecretStoreUnavailable

    reg, backend = keychain_reg
    c = reg.create("openai", api_key="sk-good-123")
    backend.refuse = {"get"}
    with pytest.raises(SecretStoreUnavailable, match="keychain"):
        reg.test(c.id)
    assert seen == []


def test_forget_keys_carries_on_past_entries_the_keychain_refuses(keychain_reg):
    reg, backend = keychain_reg
    a = reg.create("openai", api_key="sk-a-123", headers={"X-A": "1"})
    b = reg.create("openai", api_key="sk-b-123")
    backend.refuse = {"delete"}
    assert reg.forget_keys() == 3
    for c in (reg.get(a.id), reg.get(b.id)):
        assert not c.has_key and all(not h.has_value for h in c.headers)
    backend.refuse = set()
    assert reg.forget_keys() == 0 and backend.data == {}


def test_locality_is_checked_for_the_name_the_guard_connects_to(reg, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "tuppence.net.hosts._system_resolve", lambda h: seen.append(h) or ["10.0.0.9"]
    )
    c = reg.create("custom", base_url="http://straße.example:8080/v1")
    assert seen == ["xn--strae-oqa.example"] and c.is_local


def test_price_override_is_versioned_used_and_kept_on_retest(reg):
    c = reg.create("openai", api_key="k")
    reg.test(c.id)
    m = reg.model(c.id, "small-1")  # a cloud model nobody knows the price of
    assert m.price_in_usd_per_mtok is None and m.price_source is None
    assert reg.model(c.id, "vendor/known").price_source == "catalogue"
    m2 = reg.update_model(
        c.id,
        "small-1",
        {"price_in_usd_per_mtok": 0.15, "price_out_usd_per_mtok": 0.6},
        expected_version=m.version,
    )
    assert (m2.price_in_usd_per_mtok, m2.price_out_usd_per_mtok, m2.price_source) == (
        0.15,
        0.6,
        "user",
    )
    assert m2.version == m.version + 1
    with pytest.raises(VersionConflict):
        reg.update_model(c.id, "small-1", {"price_in_usd_per_mtok": 1.0}, m.version)
    reg.test(c.id)
    kept = reg.model(c.id, "small-1")
    assert (kept.price_in_usd_per_mtok, kept.price_out_usd_per_mtok) == (0.15, 0.6)
    assert kept.price_source == "user"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"price_in_usd_per_mtok": -1},
        {"price_out_usd_per_mtok": 10_001},
        {"price_in_usd_per_mtok": float("nan")},
        {"price_in_usd_per_mtok": float("inf")},
        {"price_in_usd_per_mtok": True},
        {"price_in_usd_per_mtok": "1"},
        {"context_window": 100},
        {"display_name": "x"},
    ],
)
def test_bad_model_changes_are_input_errors(reg, changes):
    c = reg.create("openai", api_key="k")
    reg.test(c.id)
    m = reg.model(c.id, "small-1")
    with pytest.raises(InputError):
        reg.update_model(c.id, "small-1", changes, expected_version=m.version)


def test_acknowledging_the_notice_is_versioned(reg):
    c = reg.create("openai", api_key="k")
    acked = reg.acknowledge_notice(c.id, expected_version=c.version)
    assert not acked.needs_notice and acked.version == c.version + 1
    with pytest.raises(VersionConflict):
        reg.acknowledge_notice(c.id, expected_version=c.version)


def test_header_values_can_be_kept_while_others_change(reg, seen):
    c = reg.create(
        "custom", base_url="http://10.0.0.5:8000/v1", headers={"X-Keep": "k1", "X-Drop": "d1"}
    )
    c = reg.update(c.id, {"headers": {"X-Keep": None, "X-New": "n1"}}, expected_version=c.version)
    assert sorted(h.name for h in c.headers) == ["X-Keep", "X-New"]
    reg.test(c.id)
    sent = seen[-1].headers
    assert sent["x-keep"] == "k1" and sent["x-new"] == "n1" and "x-drop" not in sent
    with pytest.raises(InputError):  # nothing saved to keep
        reg.update(c.id, {"headers": {"X-Other": None}}, expected_version=c.version)
    with pytest.raises(InputError):  # a new address never inherits saved values
        reg.update(
            c.id,
            {"base_url": "http://10.0.0.6:8000/v1", "headers": {"X-Keep": None}},
            expected_version=c.version,
        )
