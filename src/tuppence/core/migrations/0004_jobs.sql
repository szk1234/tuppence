CREATE TABLE job (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  scope_key TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  run_after TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  result TEXT
);
CREATE UNIQUE INDEX ux_job_queued_scope ON job (kind, scope_key) WHERE status = 'queued';
CREATE INDEX ix_job_ready ON job (status, run_after);

-- Lets maintenance.prune_auth drop stale throttle rows; pre-existing rows count as old.
ALTER TABLE login_attempt ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z';
