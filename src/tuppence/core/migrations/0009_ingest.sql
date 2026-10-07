CREATE TABLE statement (
  id TEXT PRIMARY KEY,
  account_id TEXT REFERENCES account(id),
  file_sha256 TEXT NOT NULL UNIQUE,
  file_ext TEXT NOT NULL,
  original_filename TEXT NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  format TEXT NOT NULL CHECK (format IN ('csv', 'text', 'ofx', 'qif', 'camt053', 'xlsx', 'pdf', 'image')),
  importer TEXT,
  layout_fingerprint TEXT,
  provider TEXT,
  period_start TEXT,
  period_end TEXT,
  opening_balance_pence INTEGER,
  closing_balance_pence INTEGER,
  balance_verified INTEGER NOT NULL DEFAULT 0 CHECK (balance_verified IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'received' CHECK (status IN
    ('received', 'identifying', 'needs_account', 'parsing', 'needs_review', 'imported', 'failed')),
  question TEXT,
  draft TEXT,
  check_errors TEXT NOT NULL DEFAULT '[]',
  stats TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  run INTEGER NOT NULL DEFAULT 1,
  analysis_state TEXT NOT NULL DEFAULT 'none' CHECK (analysis_state IN ('none', 'pending', 'done')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (status != 'imported' OR account_id IS NOT NULL),
  CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end)
);
CREATE INDEX ix_statement_status ON statement (status);
CREATE INDEX ix_statement_account ON statement (account_id, period_end);

CREATE TABLE "transaction" (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES account(id),
  statement_id TEXT NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
  date TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  amount_pence INTEGER NOT NULL CHECK (amount_pence != 0),
  currency TEXT NOT NULL DEFAULT 'GBP',
  raw_description TEXT NOT NULL,
  merchant_text TEXT,
  bank_category TEXT,
  bank_type TEXT,
  balance_after_pence INTEGER,
  source_ref TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  occurrence INTEGER NOT NULL CHECK (occurrence >= 0),
  created_at TEXT NOT NULL,
  UNIQUE (account_id, fingerprint)
);
CREATE INDEX ix_transaction_account_date ON "transaction" (account_id, date);
CREATE INDEX ix_transaction_statement ON "transaction" (statement_id);
CREATE TRIGGER transaction_is_immutable BEFORE UPDATE ON "transaction"
BEGIN
  SELECT RAISE(ABORT, 'transactions never change once imported');
END;

CREATE TABLE account_balance (
  id INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES account(id),
  as_of TEXT NOT NULL,
  balance_pence INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('statement', 'manual')),
  statement_id TEXT REFERENCES statement(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  UNIQUE (account_id, as_of, statement_id)
);
CREATE INDEX ix_account_balance ON account_balance (account_id, as_of);

CREATE TABLE csv_layout (
  id TEXT PRIMARY KEY,
  header_key TEXT NOT NULL UNIQUE,
  layout TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE layout_memory (
  fingerprint TEXT PRIMARY KEY,
  account_ids TEXT NOT NULL DEFAULT '[]',
  importer TEXT,
  times_seen INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
