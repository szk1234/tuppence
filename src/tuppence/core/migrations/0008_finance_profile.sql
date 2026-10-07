CREATE TABLE account (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('current', 'savings', 'credit_card', 'cash_wallet')),
  nickname TEXT NOT NULL CHECK (length(trim(nickname)) > 0),
  last4 TEXT CHECK (last4 IS NULL OR (length(last4) = 4 AND last4 GLOB '[0-9][0-9][0-9][0-9]')),
  credit_limit_pence INTEGER CHECK (credit_limit_pence IS NULL OR credit_limit_pence >= 0),
  purchase_apr REAL CHECK (purchase_apr IS NULL OR purchase_apr BETWEEN 0 AND 100),
  promo_apr REAL CHECK (promo_apr IS NULL OR promo_apr BETWEEN 0 AND 100),
  promo_end TEXT,
  statement_day INTEGER CHECK (statement_day IS NULL OR statement_day BETWEEN 1 AND 31),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE account_owner (
  account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL REFERENCES person(id),
  PRIMARY KEY (account_id, person_id)
);

CREATE TABLE income_source (
  id TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES person(id),
  kind TEXT NOT NULL CHECK (kind IN ('salary', 'self_employment', 'benefits', 'pension', 'rental', 'maintenance', 'other')),
  name TEXT NOT NULL CHECK (length(trim(name)) > 0),
  net_pence INTEGER NOT NULL CHECK (net_pence >= 0),
  account_id TEXT REFERENCES account(id),
  pay_rule TEXT NOT NULL,
  variable_components TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE debt (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('personal_loan', 'car_finance_pcp', 'car_finance_hp', 'mortgage', 'student_loan', 'bnpl', 'overdraft', 'informal', 'other')),
  lender TEXT NOT NULL CHECK (length(trim(lender)) > 0),
  person_id TEXT REFERENCES person(id),
  balance_pence INTEGER NOT NULL CHECK (balance_pence >= 0),
  balance_date TEXT NOT NULL,
  apr REAL CHECK (apr IS NULL OR apr BETWEEN 0 AND 1000),
  monthly_payment_pence INTEGER CHECK (monthly_payment_pence IS NULL OR monthly_payment_pence >= 0),
  end_date TEXT,
  student_loan_plan TEXT CHECK (student_loan_plan IS NULL OR (kind = 'student_loan' AND student_loan_plan IN ('plan1', 'plan2', 'plan4', 'plan5', 'postgraduate'))),
  details TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'settled')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE debt_entry (
  id INTEGER PRIMARY KEY,
  debt_id TEXT NOT NULL REFERENCES debt(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  amount_pence INTEGER NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE goal (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL CHECK (length(trim(name)) > 0),
  kind TEXT NOT NULL CHECK (kind IN ('emergency_fund', 'house_deposit', 'holiday', 'car', 'wedding', 'education', 'retirement', 'debt_free', 'other')),
  target_pence INTEGER CHECK (target_pence IS NULL OR target_pence > 0),
  saved_pence INTEGER NOT NULL DEFAULT 0 CHECK (saved_pence >= 0),
  target_date TEXT,
  priority INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'achieved', 'abandoned')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE onboarding_step (
  step TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('done', 'skipped')),
  updated_at TEXT NOT NULL
);

-- Timeline entries are user-owned rows: they carry a version for conflict checks.
ALTER TABLE profile_entry ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
