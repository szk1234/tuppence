CREATE TABLE app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE person (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
  role TEXT NOT NULL CHECK (role IN ('adult', 'child', 'dependent_adult')),
  birth_year INTEGER CHECK (birth_year IS NULL OR birth_year BETWEEN 1900 AND 2100),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE household (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  nation TEXT CHECK (nation IS NULL OR nation IN ('england', 'wales', 'scotland', 'northern_ireland')),
  postcode_district TEXT,
  currency TEXT NOT NULL DEFAULT 'GBP',
  period_mode TEXT NOT NULL DEFAULT 'calendar_month' CHECK (period_mode IN ('calendar_month', 'pay_cycle')),
  period_anchor_person_id TEXT REFERENCES person(id),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE profile_entry (
  id INTEGER PRIMARY KEY,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('household', 'person', 'account', 'income_source')),
  subject_id TEXT NOT NULL,
  attribute TEXT NOT NULL,
  value TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'proposal', 'import')),
  created_at TEXT NOT NULL,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX ix_profile_entry_lookup ON profile_entry (subject_type, subject_id, attribute, valid_from);
