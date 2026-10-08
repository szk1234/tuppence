CREATE TABLE knowledge_version (
  version INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  merchant_id TEXT,
  category_id TEXT,
  rule_id TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX ix_knowledge_version_merchant ON knowledge_version (merchant_id, version);
CREATE INDEX ix_knowledge_version_category ON knowledge_version (category_id, version);

CREATE TABLE category (
  id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200 AND id NOT GLOB '*[^a-z0-9.-]*'),
  parent_id TEXT REFERENCES category(id),
  level INTEGER NOT NULL CHECK (level >= 1),
  label TEXT NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 40),
  kind TEXT NOT NULL CHECK (kind IN ('spend', 'income', 'transfer')),
  essential INTEGER NOT NULL DEFAULT 0 CHECK (essential IN (0, 1)),
  source TEXT NOT NULL CHECK (source IN ('seed', 'user', 'agent')),
  retired INTEGER NOT NULL DEFAULT 0 CHECK (retired IN (0, 1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((parent_id IS NULL) = (level = 1)),
  CHECK (source != 'agent' OR level >= 2)
);
CREATE INDEX ix_category_parent ON category (parent_id);

CREATE TABLE merchant (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL UNIQUE CHECK (length(key) >= 1),
  name TEXT NOT NULL,
  business_type TEXT CHECK (business_type IS NULL
    OR business_type IN ('bill', 'subscription', 'instalment', 'none')),
  business_type_source TEXT CHECK (business_type_source IS NULL
    OR business_type_source IN ('code', 'llm', 'user')),
  default_category_id TEXT REFERENCES category(id),
  default_who TEXT,
  memory TEXT NOT NULL DEFAULT 'none' CHECK (memory IN ('none', 'inferred', 'confirmed')),
  confidence REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
  seen_count INTEGER NOT NULL DEFAULT 0,
  evidence TEXT NOT NULL DEFAULT '{}',
  companies_house_number TEXT,
  sic_code TEXT,
  website TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (memory = 'none' OR default_category_id IS NOT NULL)
);

CREATE TABLE merchant_variant (
  text TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
  seen_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_merchant_variant_merchant ON merchant_variant (merchant_id);

CREATE TABLE rule (
  id TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  merchant_id TEXT REFERENCES merchant(id),
  text_pattern TEXT CHECK (text_pattern IS NULL OR length(text_pattern) BETWEEN 2 AND 100),
  min_amount_pence INTEGER CHECK (min_amount_pence IS NULL OR min_amount_pence > 0),
  max_amount_pence INTEGER CHECK (max_amount_pence IS NULL OR max_amount_pence > 0),
  account_id TEXT REFERENCES account(id),
  direction TEXT CHECK (direction IS NULL OR direction IN ('in', 'out')),
  date_from TEXT,
  date_to TEXT,
  person_id TEXT REFERENCES person(id),
  set_category_id TEXT REFERENCES category(id),
  set_who TEXT,
  set_transfer INTEGER CHECK (set_transfer IS NULL OR set_transfer IN (0, 1)),
  set_ignore INTEGER NOT NULL DEFAULT 0 CHECK (set_ignore IN (0, 1)),
  source TEXT NOT NULL CHECK (source IN ('user', 'learned', 'seed')),
  confirmed INTEGER NOT NULL DEFAULT 1 CHECK (confirmed IN (0, 1)),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  scope TEXT NOT NULL DEFAULT 'household',
  hit_count INTEGER NOT NULL DEFAULT 0,
  last_hit_at TEXT,
  created_from_transaction_id TEXT,
  feedback_id TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (merchant_id IS NOT NULL OR text_pattern IS NOT NULL OR account_id IS NOT NULL
    OR person_id IS NOT NULL OR min_amount_pence IS NOT NULL OR max_amount_pence IS NOT NULL),
  CHECK (set_category_id IS NOT NULL OR set_who IS NOT NULL OR set_transfer IS NOT NULL
    OR set_ignore = 1),
  CHECK (min_amount_pence IS NULL OR max_amount_pence IS NULL
    OR min_amount_pence <= max_amount_pence),
  CHECK (date_from IS NULL OR date_to IS NULL OR date_from <= date_to)
);

CREATE TABLE understanding (
  transaction_id TEXT PRIMARY KEY REFERENCES "transaction"(id) ON DELETE CASCADE,
  merchant_id TEXT REFERENCES merchant(id),
  category_id TEXT REFERENCES category(id),
  who TEXT,
  is_transfer INTEGER NOT NULL DEFAULT 0 CHECK (is_transfer IN (0, 1)),
  transfer_pair_id TEXT REFERENCES "transaction"(id) ON DELETE SET NULL,
  ignored INTEGER NOT NULL DEFAULT 0 CHECK (ignored IN (0, 1)),
  status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (status IN ('unknown', 'guessed', 'inferred', 'confirmed')),
  confidence REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
  decided_by TEXT CHECK (decided_by IS NULL
    OR decided_by IN ('rule', 'memory', 'research', 'llm', 'review', 'human')),
  authority INTEGER NOT NULL DEFAULT 0,
  rule_id TEXT REFERENCES rule(id),
  evidence TEXT NOT NULL DEFAULT '{}',
  knowledge_version INTEGER NOT NULL DEFAULT 0,
  reviewed_at TEXT,
  waiting TEXT CHECK (waiting IS NULL OR waiting IN ('queued', 'deferred', 'awaiting_ai')),
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  CHECK (status != 'confirmed' OR decided_by = 'human'),
  CHECK ((status = 'unknown') = (decided_by IS NULL))
);
CREATE INDEX ix_understanding_category ON understanding (category_id);
CREATE INDEX ix_understanding_merchant ON understanding (merchant_id);
CREATE INDEX ix_understanding_status ON understanding (status, waiting);
CREATE INDEX ix_transaction_date ON "transaction" (date);

CREATE TRIGGER understanding_confirmed_is_the_persons BEFORE UPDATE ON understanding
WHEN OLD.status = 'confirmed' AND NEW.decided_by IS NOT 'human'
  AND json_extract(NEW.evidence, '$.released_by') IS NOT 'person'
BEGIN
  SELECT RAISE(ABORT, 'only the person can change a confirmed understanding');
END;

-- A statement read again re-creates its rows with new ids but the same keyed fingerprint. What
-- the person confirmed is kept here, by (account, fingerprint), while the row is gone, and is
-- handed back when the same transaction reappears. No foreign keys: the category or merchant
-- may be gone by then, and it then comes back as NULL. A pairing is never carried.
CREATE TABLE understanding_carry (
  account_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  merchant_id TEXT,
  category_id TEXT,
  who TEXT,
  is_transfer INTEGER NOT NULL,
  ignored INTEGER NOT NULL,
  evidence TEXT NOT NULL,
  carried_at TEXT NOT NULL,
  PRIMARY KEY (account_id, fingerprint)
);

CREATE TRIGGER transaction_keeps_confirmed_understanding BEFORE DELETE ON "transaction"
BEGIN
  INSERT OR REPLACE INTO understanding_carry (account_id, fingerprint, merchant_id, category_id,
    who, is_transfer, ignored, evidence, carried_at)
  SELECT OLD.account_id, OLD.fingerprint, u.merchant_id, u.category_id, u.who, u.is_transfer,
    u.ignored, u.evidence, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
  FROM understanding u WHERE u.transaction_id = OLD.id AND u.status = 'confirmed';
END;

CREATE TRIGGER transaction_gets_understanding AFTER INSERT ON "transaction"
BEGIN
  INSERT INTO understanding (transaction_id, updated_at)
  SELECT NEW.id, NEW.created_at
  WHERE NOT EXISTS (SELECT 1 FROM understanding_carry c
    WHERE c.account_id = NEW.account_id AND c.fingerprint = NEW.fingerprint);

  INSERT INTO understanding (transaction_id, merchant_id, category_id, who, is_transfer, ignored,
    status, confidence, decided_by, authority, evidence, knowledge_version, version, updated_at)
  SELECT NEW.id,
    CASE WHEN EXISTS (SELECT 1 FROM merchant m WHERE m.id = c.merchant_id)
      THEN c.merchant_id END,
    CASE WHEN EXISTS (SELECT 1 FROM category g WHERE g.id = c.category_id AND g.retired = 0)
      THEN c.category_id END,
    c.who, c.is_transfer, c.ignored, 'confirmed', 1.0, 'human', 100,
    json_set(c.evidence, '$.carried', 'reread'),
    COALESCE((SELECT MAX(version) FROM knowledge_version), 0), 1, NEW.created_at
  FROM understanding_carry c
  WHERE c.account_id = NEW.account_id AND c.fingerprint = NEW.fingerprint;

  INSERT INTO understanding_history (transaction_id, row_version, merchant_id, category_id, who,
    is_transfer, transfer_pair_id, ignored, status, confidence, decided_by, authority, rule_id,
    evidence, knowledge_version, changed_by, reason, created_at)
  SELECT u.transaction_id, u.version, u.merchant_id, u.category_id, u.who, u.is_transfer, NULL,
    u.ignored, u.status, u.confidence, u.decided_by, u.authority, NULL, u.evidence,
    u.knowledge_version, 'person', 'kept from before the statement was read again', NEW.created_at
  FROM understanding u
  WHERE u.transaction_id = NEW.id AND json_extract(u.evidence, '$.carried') = 'reread';

  DELETE FROM understanding_carry
  WHERE account_id = NEW.account_id AND fingerprint = NEW.fingerprint;
END;
INSERT INTO understanding (transaction_id, updated_at) SELECT id, created_at FROM "transaction";

CREATE TABLE understanding_history (
  id INTEGER PRIMARY KEY,
  transaction_id TEXT NOT NULL REFERENCES "transaction"(id) ON DELETE CASCADE,
  row_version INTEGER NOT NULL,
  merchant_id TEXT,
  category_id TEXT,
  who TEXT,
  is_transfer INTEGER NOT NULL,
  transfer_pair_id TEXT,
  ignored INTEGER NOT NULL,
  status TEXT NOT NULL,
  confidence REAL NOT NULL,
  decided_by TEXT,
  authority INTEGER NOT NULL,
  rule_id TEXT,
  evidence TEXT NOT NULL,
  knowledge_version INTEGER NOT NULL,
  changed_by TEXT NOT NULL,
  run_id TEXT,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX ix_understanding_history_txn ON understanding_history (transaction_id, id);

CREATE TABLE category_refile (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  parent_id TEXT NOT NULL REFERENCES category(id),
  created_ids TEXT NOT NULL,
  moves TEXT NOT NULL,
  undone_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE commitment (
  id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL REFERENCES merchant(id),
  account_id TEXT NOT NULL REFERENCES account(id),
  category_id TEXT REFERENCES category(id),
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('bill', 'subscription', 'instalment')),
  kind_source TEXT NOT NULL CHECK (kind_source IN ('code', 'llm', 'user')),
  cadence TEXT NOT NULL CHECK (cadence IN
    ('weekly', 'fortnightly', 'four_weekly', 'monthly', 'quarterly', 'annual')),
  expected_amount_pence INTEGER NOT NULL CHECK (expected_amount_pence > 0),
  expected_day INTEGER,
  skip_months TEXT NOT NULL DEFAULT '[]',
  first_date TEXT NOT NULL,
  last_date TEXT NOT NULL,
  next_due TEXT,
  annual_cost_pence INTEGER NOT NULL CHECK (annual_cost_pence >= 0),
  occurrences INTEGER NOT NULL CHECK (occurrences >= 1),
  price_history TEXT NOT NULL DEFAULT '[]',
  flags TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK (status IN ('active', 'lapsed', 'ended')),
  dismissed INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0, 1)),
  evidence TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ix_commitment_merchant ON commitment (merchant_id, account_id);

CREATE TABLE commitment_payment (
  commitment_id TEXT NOT NULL REFERENCES commitment(id) ON DELETE CASCADE,
  transaction_id TEXT NOT NULL REFERENCES "transaction"(id) ON DELETE CASCADE,
  PRIMARY KEY (commitment_id, transaction_id)
);
CREATE INDEX ix_commitment_payment_txn ON commitment_payment (transaction_id);

CREATE TABLE analysis_run (
  id TEXT PRIMARY KEY,
  job_id INTEGER,
  triggers TEXT NOT NULL DEFAULT '[]',
  statement_ids TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK (status IN ('running', 'done', 'partial', 'failed')),
  knowledge_version_start INTEGER NOT NULL,
  knowledge_version_end INTEGER,
  counts TEXT NOT NULL DEFAULT '{}',
  summary TEXT NOT NULL DEFAULT '',
  llm_calls INTEGER NOT NULL DEFAULT 0,
  tokens INTEGER NOT NULL DEFAULT 0,
  cost_gbp REAL NOT NULL DEFAULT 0,
  stopped_reason TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX ix_analysis_run_started ON analysis_run (started_at);
