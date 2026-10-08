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
  -- The person's answer to "Which account is this?" (or "Wrong account?"), kept on the row so
  -- a restart or a re-queued job never loses it, with the statement version it was given at.
  account_answer_id TEXT REFERENCES account(id),
  account_answer_version INTEGER,
  analysis_state TEXT NOT NULL DEFAULT 'none' CHECK (analysis_state IN ('none', 'pending', 'done')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (status != 'imported' OR account_id IS NOT NULL),
  CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end)
);
CREATE INDEX ix_statement_status ON statement (status);
CREATE INDEX ix_statement_account ON statement (account_id, period_end);

-- statement_id: the statement the row was first imported from. Every statement that covers the
-- row (that one, and any later one that found it already imported) is linked to it in
-- statement_transaction; a row goes only when no statement covers it any more.
CREATE TABLE "transaction" (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES account(id),
  statement_id TEXT NOT NULL REFERENCES statement(id),
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
-- The facts never change. Only which statement the row is counted as coming from may move,
-- when that statement is removed and another one still covers the row.
CREATE TRIGGER transaction_is_immutable BEFORE UPDATE OF id, account_id, date, amount_pence,
  currency, raw_description, merchant_text, bank_category, bank_type, balance_after_pence,
  fingerprint, occurrence, created_at ON "transaction"
BEGIN
  SELECT RAISE(ABORT, 'transactions never change once imported');
END;

CREATE TABLE statement_transaction (
  statement_id TEXT NOT NULL REFERENCES statement(id) ON DELETE CASCADE,
  transaction_id TEXT NOT NULL REFERENCES "transaction"(id) ON DELETE CASCADE,
  source_ref TEXT NOT NULL,
  match TEXT NOT NULL CHECK (match IN ('new', 'exact', 'similar')),
  PRIMARY KEY (statement_id, transaction_id)
);
CREATE INDEX ix_statement_transaction_txn ON statement_transaction (transaction_id);

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
