-- Where a model's price came from: 'provider', 'catalogue', 'user' (set in Settings › AI and
-- kept when the model list is fetched again), 'local' (free), or NULL (unknown: the fallback
-- price is used and the call is marked estimated).
ALTER TABLE llm_model ADD COLUMN price_source TEXT;
