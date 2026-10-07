from tuppence.app.services import build_services
from tuppence.settings import RuntimeSettings


def test_server_mode_uses_encrypted_db(tmp_path):
    runtime = RuntimeSettings.for_mode("server", data_dir=tmp_path / "data", web_dir=tmp_path / "x")
    assert build_services(runtime).secrets.kind == "encrypted-db"
