import { jsonBytes } from "./sanitize";
import { withServerNetwork } from "./network";
import type { Env, JsonValue, NormalizedEvent } from "./types";

export interface Rejection {
  index: number;
  reason: string;
}

export class RequestError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function positiveInt(value: string | undefined, fallback: number, max: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, max) : fallback;
}

export function maxBatch(env: Env): number {
  return positiveInt(env.TELEMETRY_MAX_BATCH, 32, 100);
}

export function maxBodyBytes(env: Env): number {
  return positiveInt(env.TELEMETRY_MAX_BODY_BYTES, 262_144, 2_000_000);
}

export async function readJson(request: Request, env: Env): Promise<unknown> {
  const advertised = Number.parseInt(request.headers.get("content-length") ?? "", 10);
  const limit = maxBodyBytes(env);
  if (Number.isFinite(advertised) && advertised > limit) {
    throw new RequestError(413, "body_too_large", "request body exceeds the configured limit");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > limit) {
    throw new RequestError(413, "body_too_large", "request body exceeds the configured limit");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new RequestError(400, "invalid_json", "request body must be valid JSON");
  }
}

function stringField(value: unknown, name: string, max = 128): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`${name} must be a non-empty string`);
  }
  return value.trim().slice(0, max);
}

function optionalInteger(value: unknown, name: string): number | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error(`${name} must be a non-negative integer`);
  return value;
}

function eventPayload(item: Record<string, unknown>, serverNetwork: JsonValue): { payload: JsonValue; json: string; bytes: number } {
  const source = item.payload ?? item.data ?? {};
  const payload = withServerNetwork(source, serverNetwork);
  const { json, bytes } = jsonBytes(payload);
  if (bytes > 64_000) throw new Error("payload exceeds 64 KiB");
  return { payload, json, bytes };
}

function normalize(item: unknown, receivedAt: number, serverNetwork: JsonValue): NormalizedEvent {
  if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("event must be an object");
  const value = item as Record<string, unknown>;
  const { json, bytes } = eventPayload(value, serverNetwork);
  const id = value.id === undefined ? crypto.randomUUID() : stringField(value.id, "id", 160);
  return {
    id,
    run_id: stringField(value.run_id ?? value.runId, "run_id"),
    event_type: stringField(value.event_type ?? value.event ?? value.kind, "event_type", 96),
    source: value.source === undefined ? "agent" : stringField(value.source, "source", 48),
    client_time_ms: optionalInteger(value.client_time_ms ?? value.clientTimeMs ?? value.ts_ms, "client_time_ms"),
    sequence: optionalInteger(value.sequence, "sequence"),
    received_at_ms: receivedAt,
    payload_json: json,
    payload_bytes: bytes,
  };
}

export async function parseEvents(request: Request, env: Env, serverNetwork: JsonValue): Promise<{ events: NormalizedEvent[]; rejected: Rejection[] }> {
  const body = await readJson(request, env);
  const items = Array.isArray(body) ? body : (body && typeof body === "object" && Array.isArray((body as { events?: unknown }).events)
    ? (body as { events: unknown[] }).events
    : [body]);
  if (items.length === 0) throw new RequestError(400, "empty_batch", "at least one event is required");
  if (items.length > maxBatch(env)) throw new RequestError(413, "batch_too_large", "too many events in one request");

  const receivedAt = Date.now();
  const events: NormalizedEvent[] = [];
  const rejected: Rejection[] = [];
  for (const [index, item] of items.entries()) {
    try {
      events.push(normalize(item, receivedAt, serverNetwork));
    } catch (error) {
      rejected.push({ index, reason: error instanceof Error ? error.message : "invalid event" });
    }
  }
  if (events.length === 0) throw new RequestError(400, "no_valid_events", "no valid telemetry events were supplied");
  return { events, rejected };
}
