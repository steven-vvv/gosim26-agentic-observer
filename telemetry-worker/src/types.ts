export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface Env {
  DB: D1Database;
  TELEMETRY_INGEST_TOKEN?: string;
  TELEMETRY_READ_TOKEN?: string;
  TELEMETRY_MAX_BATCH?: string;
  TELEMETRY_MAX_BODY_BYTES?: string;
  TELEMETRY_RETENTION_DAYS?: string;
  CORS_ORIGIN?: string;
}

export interface NormalizedEvent {
  id: string;
  run_id: string;
  event_type: string;
  source: string;
  client_time_ms: number | null;
  sequence: number | null;
  received_at_ms: number;
  payload_json: string;
  payload_bytes: number;
}

export interface StoredEvent {
  id: string;
  run_id: string;
  event_type: string;
  source: string;
  client_time_ms: number | null;
  sequence: number | null;
  received_at_ms: number;
  payload_json: string;
  payload_bytes: number;
}

export interface QueryEvents {
  run_id?: string;
  event_type?: string;
  source?: string;
  since_ms?: number;
  until_ms?: number;
  limit: number;
  cursor?: string;
}
