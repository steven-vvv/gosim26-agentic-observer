CREATE TABLE IF NOT EXISTS telemetry_events (
  id TEXT PRIMARY KEY NOT NULL,
  run_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'agent',
  client_time_ms INTEGER,
  sequence INTEGER,
  received_at_ms INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  payload_bytes INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS telemetry_events_run_time
  ON telemetry_events (run_id, received_at_ms DESC, id DESC);

CREATE INDEX IF NOT EXISTS telemetry_events_type_time
  ON telemetry_events (event_type, received_at_ms DESC, id DESC);

CREATE INDEX IF NOT EXISTS telemetry_events_received_time
  ON telemetry_events (received_at_ms DESC, id DESC);
