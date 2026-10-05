export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface Env {
  DB: D1Database;
  TELEMETRY_INGEST_TOKEN?: string;
  TELEMETRY_READ_TOKEN?: string;
  /** Optional separate credential for startup config reads; ingest is the fallback. */
  CONFIG_READ_TOKEN?: string;
  /** Opt-in public, profile-only startup configuration reads. */
  CONFIG_PUBLIC_READ?: string;
  /** Config id exposed by the public profile-only endpoint. */
  CONFIG_PUBLIC_ID?: string;
  /** Required for changing a config revision. Never expose this to an agent. */
  CONFIG_ADMIN_TOKEN?: string;
  TELEMETRY_MAX_BATCH?: string;
  TELEMETRY_MAX_BODY_BYTES?: string;
  TELEMETRY_RETENTION_DAYS?: string;
  CORS_ORIGIN?: string;
  /** OpenAI-compatible upstream used for ordinary chat completions. */
  LLM_UPSTREAM_URL?: string;
  LLM_UPSTREAM_API_KEY?: string;
  LLM_UPSTREAM_TIMEOUT_MS?: string;
  LLM_MODEL_NAME?: string;
  /** "true" switches provider reasoning off for every forwarded request. */
  LLM_DISABLE_THINKING?: string;
  /** "true" forwards prompts that arrive with model "telemetry-ack" but carry no telemetry. */
  LLM_FORWARD_UNMARKED?: string;
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
