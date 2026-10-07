CREATE TABLE privacy_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('llm', 'research', 'market', 'datapack')),
  task TEXT,
  connection_id TEXT,
  destination TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  bytes_out INTEGER NOT NULL DEFAULT 0,
  bytes_in INTEGER NOT NULL DEFAULT 0,
  status INTEGER,
  redactions INTEGER NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL CHECK (outcome IN ('sent', 'blocked', 'error')),
  note TEXT
);
CREATE INDEX ix_privacy_log_ts ON privacy_log (ts);
