from tuppence.app.services import build_services
from tuppence.settings import RuntimeSettings


def test_server_mode_uses_encrypted_db(tmp_path):
    runtime = RuntimeSettings.for_mode("server", data_dir=tmp_path / "data", web_dir=tmp_path / "x")
    assert build_services(runtime).secrets.kind == "encrypted-db"


def test_app_starts_with_missing_key_file_then_reports_at_use(tmp_path, monkeypatch):
    import pytest

    from tuppence.core.secrets import SecretKeyMissing

    monkeypatch.delenv("TUPPENCE_SECRET_KEY_FILE", raising=False)
    runtime = RuntimeSettings.for_mode("server", data_dir=tmp_path / "data", web_dir=tmp_path / "x")
    first = build_services(runtime)
    ref = first.secrets.put("sk-1")
    (first.paths.root / "secret.key").unlink()
    second = build_services(runtime)  # must not raise
    with pytest.raises(SecretKeyMissing):
        second.secrets.get(ref)
