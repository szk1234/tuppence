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


def test_running_job_is_recovered_on_startup(make_app):
    from tuppence.core.clock import to_iso, utcnow

    app = make_app("server")
    q = app.state.services.queue
    now = to_iso(utcnow())
    with app.state.services.db.transaction() as conn:
        conn.execute(
            "INSERT INTO job (kind, scope_key, status, attempts, run_after, created_at, started_at)"
            " VALUES ('maintenance.daily_backup', 'orphan', 'running', 1, ?, ?, ?)",
            [now, now, now],
        )
    with TestClient(app):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            j.scope_key == "orphan" and j.status == "done" for j in q.list()
        ):
            time.sleep(0.1)
        assert any(j.scope_key == "orphan" and j.status == "done" for j in q.list())


def test_raising_merge_does_not_block_startup(make_app):
    from tuppence.core.clock import to_iso, utcnow

    app = make_app("server")
    services = app.state.services

    def boom(old, new):
        raise KeyError("old shape")

    services.queue.merges["analysis"] = boom
    now = to_iso(utcnow())
    with services.db.transaction() as conn:
        for status in ("running", "queued"):
            conn.execute(
                "INSERT INTO job (kind, scope_key, status, attempts, run_after, created_at)"
                " VALUES ('analysis', 's', ?, 1, ?, ?)",
                [status, now, now],
            )
    with TestClient(app):
        pass
    jobs = {j.status: j for j in services.queue.list() if j.kind == "analysis"}
    assert jobs["cancelled"].error == "Superseded; its work couldn't be merged"
