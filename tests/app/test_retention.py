"""The daily maintenance job deletes privacy-log and usage rows older than 13 months."""

from datetime import UTC, datetime

from tuppence.app.services import RETENTION_MONTHS, build_services
from tuppence.core.clock import months_ago, to_iso, utcnow
from tuppence.llm.types import Usage
from tuppence.net.privacy_log import PrivacyEvent
from tuppence.settings import RuntimeSettings


def test_months_ago_is_calendar_months():
    assert months_ago(datetime(2026, 10, 7, 12, tzinfo=UTC), 13) == datetime(
        2025, 9, 7, 12, tzinfo=UTC
    )
    assert months_ago(datetime(2026, 3, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert months_ago(datetime(2024, 3, 31, tzinfo=UTC), 1) == datetime(2024, 2, 29, tzinfo=UTC)
    assert months_ago(datetime(2026, 1, 15, tzinfo=UTC), 13) == datetime(2024, 12, 15, tzinfo=UTC)


def event(ts):
    return PrivacyEvent(
        ts=ts,
        purpose="llm",
        task="coach",
        connection_id=None,
        destination="api.example.com",
        method="POST",
        path="/v1/chat",
        bytes_out=1,
        bytes_in=1,
        status=200,
        redactions=0,
        outcome="sent",
        note=None,
    )


def test_daily_maintenance_prunes_rows_older_than_13_months(tmp_path):
    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path / "data"))
    now = utcnow()
    old = to_iso(months_ago(now, RETENTION_MONTHS + 1))
    recent = to_iso(months_ago(now, RETENTION_MONTHS - 1))
    for ts in (old, recent):
        services.privacy_log.record(event(ts))
        services.usage.clock = lambda ts=ts: datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        services.usage.record("coach", None, "m", Usage(input_tokens=1), 0.01, ok=True)
    job_id = services.queue.enqueue("maintenance.prune_auth", scope_key="daily")
    assert services.worker.run_once()
    job = services.queue.get(job_id)
    assert job.status == "done", job.error
    assert job.result["privacy_log"] == 1 and job.result["llm_usage"] == 1
    assert [e.ts for e in services.privacy_log.list()] == [recent]
    with services.db.connection() as conn:
        assert [r[0] for r in conn.execute("SELECT ts FROM llm_usage")] == [recent]
