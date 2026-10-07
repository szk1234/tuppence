import time

from fastapi.testclient import TestClient


def test_daily_backup_job_runs_on_startup(tmp_path, make_app):
    app = make_app("server")
    with TestClient(app):
        backups = tmp_path / "data" / "backups"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(backups.glob("daily-*.db")):
            time.sleep(0.1)
        assert any(backups.glob("daily-*.db"))


def test_jobs_endpoint_lists_jobs(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200 and "jobs" in r.json()


def test_prune_job_is_registered_and_runs(make_app):
    app = make_app("server")
    with TestClient(app):
        q = app.state.services.queue
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            j.kind == "maintenance.prune_auth" and j.status == "done" for j in q.list()
        ):
            time.sleep(0.1)
        assert any(j.kind == "maintenance.prune_auth" and j.status == "done" for j in q.list())
