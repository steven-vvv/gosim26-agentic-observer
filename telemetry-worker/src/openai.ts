import { requireIngestAuth } from "./auth";
import { insertEvents } from "./storage";
import { requestNetworkMetadata, withServerNetwork } from "./network";
import { jsonBytes } from "./sanitize";
import { proxyChatCompletion } from "./proxy";
import { maxBatch, readJson, RequestError } from "./validation";
import type { Env, JsonValue, NormalizedEvent } from "./types";

function jsonResponse(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

function responseId(): string {
  return `chatcmpl_${crypto.randomUUID().replaceAll("-", "")}`;
}

function completionBody(model: string, id: string, created: number): Record<string, unknown> {
  return {
    id,
    object: "chat.completion",
    created,
    model,
    choices: [{
      index: 0,
      message: { role: "assistant", content: "telemetry accepted" },
      finish_reason: "stop",
    }],
    usage: { prompt_tokens: 0, completion_tokens: 2, total_tokens: 2 },
  };
}

function streamBody(model: string, id: string, created: number): Response {
  const chunk = {
    id,
    object: "chat.completion.chunk",
    created,
    model,
    choices: [{ index: 0, delta: { role: "assistant", content: "telemetry accepted" }, finish_reason: null }],
  };
  const done = {
    id,
    object: "chat.completion.chunk",
    created,
    model,
    choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
  };
  const body = `data: ${JSON.stringify(chunk)}\n\ndata: ${JSON.stringify(done)}\n\ndata: [DONE]\n\n`;
  return new Response(body, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "keep-alive",
    },
  });
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown, max = 128): string | null {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, max) : null;
}

function integerValue(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/** Find structured events in the custom envelope or in a standard message. */
function eventsFromEnvelope(value: unknown): Record<string, unknown>[] | null {
  const envelope = objectValue(value);
  if (!envelope) return null;
  if (Array.isArray(envelope.events)) {
    if (envelope.events.length === 0) throw new RequestError(400, "empty_batch", "at least one event is required");
    return envelope.events.map((event) => {
      const item = objectValue(event);
      if (!item) throw new RequestError(400, "invalid_event", "telemetry event must be an object");
      return item;
    });
  }
  const nested = objectValue(envelope.event);
  if (nested) return [nested];
  if (envelope.event_type || envelope.event || envelope.kind) return [envelope];
  return null;
}

function eventsFromRequest(value: Record<string, unknown>, messages: unknown[]): Record<string, unknown>[] | null {
  const direct = eventsFromEnvelope(value.telemetry);
  if (direct) return direct;
  for (const message of [...messages].reverse()) {
    const item = objectValue(message);
    const marked = item?.role === "tool" && (
      item.tool_call_id === "gosim26.telemetry" || item.name === "gosim26.telemetry"
    );
    // A marked tool message is the current protocol. It is consumed here and
    // never forwarded to the real model, so it costs no provider tokens.
    if (!marked && value.model !== "telemetry-ack") continue;
    const content = item?.content;
    if (marked && typeof content !== "string") {
      throw new RequestError(400, "invalid_telemetry", "marked telemetry content must be a JSON string");
    }
    if (typeof content !== "string") continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(content) as unknown;
    } catch {
      // Ordinary model prompts are not telemetry envelopes.
      if (marked) throw new RequestError(400, "invalid_telemetry", "marked telemetry content is not valid JSON");
      continue;
    }
    const candidate = eventsFromEnvelope(parsed);
    if (candidate) return candidate;
    if (marked) throw new RequestError(400, "invalid_telemetry", "marked telemetry envelope is invalid");
  }
  return null;
}

function payloadBytes(payload: JsonValue): { json: string; bytes: number } {
  const encoded = jsonBytes(payload);
  if (encoded.bytes > 64_000) throw new RequestError(413, "payload_too_large", "telemetry payload exceeds 64 KiB");
  return encoded;
}

function eventFromTelemetry(
  candidate: Record<string, unknown>,
  metadata: Record<string, unknown>,
  network: JsonValue,
  receivedAt: number,
): NormalizedEvent {
  const payload = withServerNetwork(candidate.payload ?? candidate.data ?? {}, network);
  const encoded = payloadBytes(payload);
  return {
    id: stringValue(candidate.id, 160) ?? crypto.randomUUID(),
    run_id: stringValue(candidate.run_id ?? candidate.runId) ?? stringValue(metadata.run_id) ?? "openai-compatible",
    event_type: stringValue(candidate.event_type ?? candidate.event ?? candidate.kind, 96) ?? "openai_request",
    source: stringValue(candidate.source, 48) ?? "openai-compatible",
    client_time_ms: integerValue(candidate.client_time_ms ?? candidate.clientTimeMs ?? candidate.ts_ms),
    sequence: integerValue(candidate.sequence),
    received_at_ms: receivedAt,
    payload_json: encoded.json,
    payload_bytes: encoded.bytes,
  };
}

export async function handleChatCompletions(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") return jsonResponse({ error: { message: "method not allowed", type: "invalid_request_error" } }, 405);
  if (!(await requireIngestAuth(request, env))) {
    return jsonResponse({ error: { message: "invalid or missing bearer token", type: "authentication_error" } }, 401);
  }
  const body = await readJson(request, env);
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new RequestError(400, "invalid_request", "chat completion body must be an object");
  }
  const value = body as Record<string, unknown>;
  const model = stringValue(value.model) ?? "telemetry-ack";
  const messages = Array.isArray(value.messages) ? value.messages : [];
  const metadata = objectValue(value.metadata) ?? {};
  const network = requestNetworkMetadata(request);
  const receivedAt = Date.now();
  const candidates = eventsFromRequest(value, messages);
  if (candidates && candidates.length > maxBatch(env)) {
    throw new RequestError(413, "batch_too_large", "too many events in one request");
  }
  if (!candidates && model !== "telemetry-ack") {
    // Ordinary model traffic shares this endpoint but is never written to D1.
    return proxyChatCompletion(request, value, env);
  }

  const events = candidates
    ? candidates.map((candidate) => eventFromTelemetry(candidate, metadata, network, receivedAt))
    : (() => {
        const payload = withServerNetwork({
          model,
          stream: value.stream === true,
          message_count: messages.length,
          metadata,
        }, network);
        const encoded = payloadBytes(payload);
        return [{
          id: crypto.randomUUID(),
          run_id: stringValue(metadata.run_id) ?? "openai-compatible",
          event_type: "openai_request",
          source: "openai-compatible",
          client_time_ms: null,
          sequence: null,
          received_at_ms: receivedAt,
          payload_json: encoded.json,
          payload_bytes: encoded.bytes,
        } satisfies NormalizedEvent];
      })();
  await insertEvents(env.DB, events);

  const id = responseId();
  const created = Math.floor(Date.now() / 1000);
  if (value.stream === true) return streamBody(model, id, created);
  return jsonResponse(completionBody(model, id, created), 200, { "x-telemetry-accepted": "true" });
}

export function modelList(env?: Env): Response {
  const data = [{ id: "telemetry-ack", object: "model", created: 0, owned_by: "gosim26-telemetry" }];
  if (env?.LLM_MODEL_NAME?.trim()) {
    data.push({ id: env.LLM_MODEL_NAME.trim().slice(0, 128), object: "model", created: 0, owned_by: "llm-upstream" });
  }
  return jsonResponse({
    object: "list",
    data,
  });
}
