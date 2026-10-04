CREATE TABLE IF NOT EXISTS agent_configs (
  config_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  patch_json TEXT NOT NULL,
  expires_at_ms INTEGER,
  updated_at_ms INTEGER NOT NULL
);
