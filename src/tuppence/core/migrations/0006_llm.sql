CREATE TABLE llm_connection (
  id TEXT PRIMARY KEY,
  preset TEXT NOT NULL,
  name TEXT NOT NULL,
  api_style TEXT NOT NULL CHECK (api_style IN ('openai', 'anthropic', 'gemini')),
  base_url TEXT NOT NULL,
  secret_ref TEXT,
  headers TEXT NOT NULL DEFAULT '{}',
  is_local INTEGER NOT NULL,
  notice_acknowledged_at TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE llm_model (
  connection_id TEXT NOT NULL REFERENCES llm_connection(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  display_name TEXT,
  context_window INTEGER NOT NULL,
  max_output_tokens INTEGER,
  supports_tools INTEGER NOT NULL DEFAULT 0,
  supports_json_schema INTEGER NOT NULL DEFAULT 0,
  supports_vision INTEGER NOT NULL DEFAULT 0,
  price_in_usd_per_mtok REAL,
  price_out_usd_per_mtok REAL,
  source TEXT NOT NULL CHECK (source IN ('provider', 'catalogue', 'default', 'user')),
  fetched_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (connection_id, model_id)
);

CREATE TABLE llm_route (
  task TEXT PRIMARY KEY CHECK (task IN ('read', 'categorise', 'review', 'research', 'coach', 'report', 'vision')),
  chain TEXT NOT NULL DEFAULT '[]',
  local_only INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE llm_usage (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  run_id TEXT,
  task TEXT NOT NULL,
  connection_id TEXT,
  model_id TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cost_gbp REAL,
  ok INTEGER NOT NULL,
  error TEXT,
  estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_llm_usage_ts ON llm_usage (ts);

CREATE TABLE secret (
  ref TEXT PRIMARY KEY,
  ciphertext BLOB NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE secret_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
