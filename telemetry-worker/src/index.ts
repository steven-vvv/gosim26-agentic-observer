import { requireIngestAuth, requireReadAuth } from "./auth";
import { handleChatCompletions, modelList } from "./openai";
import { deleteExpired, insertEvents, queryEvents } from "./storage";
import { requestNetworkMetadata } from "./network";
import { parseEvents, RequestError } from "./validation";
import type { Env, QueryEvents } from "./types";

const VERSION = "2026-09-27.telemetry-v2";

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

function requestId(): string {
  return crypto.randomUUID();
}

function corsHeaders(request: Request, env: Env): Headers {
  const origin = env.CORS_ORIGIN ?? "*";
  const requested = request.headers.get("origin");
  const allowOrigin = origin === "*" || requested === origin ? origin : "null";
  return new Headers({
    "access-control-allow-origin": allowOrigin,
    "access-control-allow-headers": "authorization, content-type, x-telemetry-token",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-max-age": "600",
    vary: "Origin",
  });
}

function withCors(response: Response, request: Request, env: Env, id: string): Response {
  const headers = corsHeaders(request, env);
  headers.set("x-request-id", id);
  response.headers.forEach((value, key) => headers.set(key, value));
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

function queryNumber(url: URL, name: string): number | undefined {
  const raw = url.searchParams.get(name);
  if (raw === null || raw === "") return undefined;
  const value = Number(raw);
  return Number.isSafeInteger(value) ? value : undefined;
}

function queryLimit(url: URL): number {
  const value = queryNumber(url, "limit") ?? 100;
  return Math.max(1, Math.min(200, value));
}

async function ingest(request: Request, env: Env): Promise<Response> {
  if (!(await requireIngestAuth(request, env))) return json({ error: "unauthorized" }, 401);
  const { events, rejected } = await parseEvents(request, env, requestNetworkMetadata(request));
  const accepted = await insertEvents(env.DB, events);
  return json({ accepted, received: events.length, rejected, server_time_ms: Date.now() }, 202);
}

async function readEvents(request: Request, env: Env): Promise<Response> {
  if (!(await requireReadAuth(request, env))) return json({ error: "unauthorized" }, 401);
  const url = new URL(request.url);
  const query: QueryEvents = {
    run_id: url.searchParams.get("run_id") ?? undefined,
    event_type: url.searchParams.get("event_type") ?? undefined,
    source: url.searchParams.get("source") ?? undefined,
    since_ms: queryNumber(url, "since_ms"),
    until_ms: queryNumber(url, "until_ms"),
    limit: queryLimit(url),
    cursor: url.searchParams.get("cursor") ?? undefined,
  };
  let result;
  try {
    result = await queryEvents(env.DB, query);
  } catch (error) {
    if (error instanceof Error && error.message === "invalid cursor") {
      throw new RequestError(400, "invalid_cursor", "cursor is invalid");
    }
    throw error;
  }
  const events = result.events.map(({ payload_json, ...event }) => ({
    ...event,
    payload: JSON.parse(payload_json),
  }));
  return json({ events, next_cursor: result.next_cursor, count: events.length, server_time_ms: Date.now() });
}

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "OPTIONS") return new Response(null, { status: 204 });
  if (url.pathname === "/healthz" && request.method === "GET") {
    return json({ ok: true, service: "gosim26-telemetry", version: VERSION, server_time_ms: Date.now() });
  }
  if (url.pathname === "/v1/telemetry" || url.pathname === "/v1/telemetry/batch") {
    if (request.method === "POST") return ingest(request, env);
    if (request.method === "GET") return readEvents(request, env);
    return json({ error: "method_not_allowed" }, 405);
  }
  if (url.pathname === "/v1/chat/completions" || url.pathname === "/chat/completions") {
    return handleChatCompletions(request, env);
  }
  if (url.pathname === "/v1/models" && request.method === "GET") {
    if (!(await requireIngestAuth(request, env))) return json({ error: "unauthorized" }, 401);
    return modelList(env);
  }
  return json({ error: "not_found" }, 404);
}

async function handle(request: Request, env: Env): Promise<Response> {
  const id = requestId();
  try {
    return withCors(await route(request, env), request, env, id);
  } catch (error) {
    if (error instanceof RequestError) {
      return withCors(json({ error: { code: error.code, message: error.message }, request_id: id }, error.status), request, env, id);
    }
    console.error(JSON.stringify({ event: "request_error", request_id: id, method: request.method,
      path: new URL(request.url).pathname, error: error instanceof Error ? error.message : String(error) }));
    return withCors(json({ error: { code: "internal_error", message: "internal server error" }, request_id: id }, 500), request, env, id);
  }
}

function retentionDays(env: Env): number {
  const parsed = Number.parseInt(env.TELEMETRY_RETENTION_DAYS ?? "30", 10);
  return Number.isFinite(parsed) ? Math.max(1, Math.min(parsed, 3650)) : 30;
}

export default {
  fetch: handle,
  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    const deleted = await deleteExpired(env.DB, retentionDays(env));
    console.log(JSON.stringify({ event: "retention_cleanup", deleted, retention_days: retentionDays(env) }));
  },
};
