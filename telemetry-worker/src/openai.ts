import { requireIngestAuth } from "./auth";
import { insertEvents } from "./storage";
import { jsonBytes, sanitize } from "./sanitize";
import { readJson, RequestError } from "./validation";
import type { Env } from "./types";

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
  const model = typeof value.model === "string" && value.model ? value.model.slice(0, 128) : "telemetry-ack";
  const messages = Array.isArray(value.messages) ? value.messages : [];
  const metadata = value.metadata && typeof value.metadata === "object" ? value.metadata : {};
  const explicitTelemetry = value.telemetry && typeof value.telemetry === "object" ? value.telemetry : {};
  const metadataObject = metadata as Record<string, unknown>;
  const runId = typeof metadataObject.run_id === "string" ? metadataObject.run_id.slice(0, 128) : "openai-compatible";
  const id = responseId();
  const created = Math.floor(Date.now() / 1000);
  const telemetryPayload = sanitize({
    model,
    stream: value.stream === true,
    message_count: messages.length,
    metadata,
    telemetry: explicitTelemetry,
  });
  const encodedPayload = jsonBytes(telemetryPayload);
  await insertEvents(env.DB, [{
    id: crypto.randomUUID(),
    run_id: runId,
    event_type: "openai_request",
    source: "openai-compatible",
    client_time_ms: null,
    sequence: null,
    received_at_ms: Date.now(),
    payload_json: encodedPayload.json,
    payload_bytes: encodedPayload.bytes,
  }]);

  if (value.stream === true) return streamBody(model, id, created);
  return jsonResponse(completionBody(model, id, created), 200, { "x-telemetry-accepted": "true" });
}

export function modelList(): Response {
  return jsonResponse({
    object: "list",
    data: [{ id: "telemetry-ack", object: "model", created: 0, owned_by: "gosim26-telemetry" }],
  });
}
