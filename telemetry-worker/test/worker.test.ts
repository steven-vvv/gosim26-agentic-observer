import { beforeAll, describe, expect, it, vi } from "vitest";
import { env } from "cloudflare:test";
import worker from "../src/index";
import type { Env } from "../src/types";

const runtimeEnv = {
  ...env,
  TELEMETRY_INGEST_TOKEN: "ingest-test-token",
  TELEMETRY_READ_TOKEN: "read-test-token",
  CONFIG_READ_TOKEN: "config-read-test-token",
  CONFIG_ADMIN_TOKEN: "config-admin-test-token",
  TELEMETRY_MAX_BATCH: "4",
  CORS_ORIGIN: "*",
} as unknown as Env;

async function call(path: string, init: RequestInit = {}): Promise<Response> {
  return worker.fetch(new Request(`https://telemetry.test${path}`, init), runtimeEnv);
}

function bearer(token: string): HeadersInit {
  return { authorization: `Bearer ${token}` };
}

beforeAll(async () => {
  await runtimeEnv.DB.prepare(`CREATE TABLE IF NOT EXISTS telemetry_events (
      id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      event_type TEXT NOT NULL,
      source TEXT NOT NULL DEFAULT 'agent',
      client_time_ms INTEGER,
      sequence INTEGER,
      received_at_ms INTEGER NOT NULL,
      payload_json TEXT NOT NULL,
      payload_bytes INTEGER NOT NULL
    )`).run();
  await runtimeEnv.DB.prepare("CREATE INDEX IF NOT EXISTS telemetry_events_run_time ON telemetry_events(run_id, received_at_ms DESC)").run();
  await runtimeEnv.DB.prepare("CREATE INDEX IF NOT EXISTS telemetry_events_type_time ON telemetry_events(event_type, received_at_ms DESC)").run();
  await runtimeEnv.DB.prepare("CREATE INDEX IF NOT EXISTS telemetry_events_received ON telemetry_events(received_at_ms DESC)").run();
  await runtimeEnv.DB.prepare(`CREATE TABLE IF NOT EXISTS agent_configs (
      config_id TEXT PRIMARY KEY,
      revision INTEGER NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      patch_json TEXT NOT NULL,
      expires_at_ms INTEGER,
      updated_at_ms INTEGER NOT NULL
    )`).run();
});

describe("telemetry worker", () => {
  it("serves health and rejects missing ingest credentials", async () => {
    const health = await call("/healthz");
    expect(health.status).toBe(200);
    expect((await health.json() as { ok: boolean }).ok).toBe(true);

    const rejected = await call("/v1/telemetry", {
      method: "POST",
      body: JSON.stringify({ run_id: "unauthorized", event_type: "test", payload: {} }),
      headers: { "content-type": "application/json" },
    });
    expect(rejected.status).toBe(401);

    const invalidCursor = await call("/v1/telemetry?cursor=not-a-cursor", {
      headers: bearer("read-test-token"),
    });
    expect(invalidCursor.status).toBe(400);
  });

  it("publishes versioned startup configuration with separate read and admin access", async () => {
    await runtimeEnv.DB.prepare("DELETE FROM agent_configs WHERE config_id = ?").bind("observer4").run();
    const unauthorized = await call("/v1/config", {
      method: "PUT",
      headers: { ...bearer("config-read-test-token"), "content-type": "application/json" },
      body: JSON.stringify({ config_id: "observer4", patch: { release: { profile: "formal_conservative" } } }),
    });
    expect(unauthorized.status).toBe(401);

    const published = await call("/v1/config", {
      method: "PUT",
      headers: { ...bearer("config-admin-test-token"), "content-type": "application/json" },
      body: JSON.stringify({ config_id: "observer4", patch: { release: { profile: "formal_conservative" } }, expected_revision: 0 }),
    });
    expect(published.status).toBe(200);
    expect((await published.json() as { revision: number }).revision).toBe(1);

    const resolved = await call("/v1/config/resolve", {
      method: "POST",
      headers: { ...bearer("config-read-test-token"), "content-type": "application/json" },
      body: JSON.stringify({ schema: "gosim26.v4.config-request.v1", config_id: "observer4" }),
    });
    expect(resolved.status).toBe(200);
    const payload = await resolved.json() as { schema: string; revision: number; patch: { release: { profile: string } } };
    expect(payload.schema).toBe("gosim26.v4.config-response.v1");
    expect(payload.revision).toBe(1);
    expect(payload.patch.release.profile).toBe("formal_conservative");

    const conflict = await call("/v1/config", {
      method: "PUT",
      headers: { ...bearer("config-admin-test-token"), "content-type": "application/json" },
      body: JSON.stringify({ config_id: "observer4", patch: {}, expected_revision: 0 }),
    });
    expect(conflict.status).toBe(409);
  });

  it("stores sanitized batches and pages reads with a cursor", async () => {
    const runId = `run-${crypto.randomUUID()}`;
    const response = await call("/v1/telemetry/batch", {
      method: "POST",
      headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
      body: JSON.stringify({
        events: [
          {
            id: `${runId}-one`,
            run_id: runId,
            event_type: "runtime",
            source: "test",
            sequence: 1,
            payload: {
              ok: true,
              api_key: "should-not-persist",
              privateKey: "should-not-persist-either",
              nested: { value: 3 },
            },
          },
          { run_id: runId, event_type: "checkpoint", payload: { decisions: 100 } },
          { run_id: runId, payload: {} },
        ],
      }),
    });
    expect(response.status).toBe(202);
    const accepted = await response.json() as { accepted: number; rejected: Array<{ index: number }> };
    expect(accepted.accepted).toBe(2);
    expect(accepted.rejected.map((item) => item.index)).toEqual([2]);

    const page = await call(`/v1/telemetry?run_id=${encodeURIComponent(runId)}&limit=1`, {
      headers: bearer("read-test-token"),
    });
    expect(page.status).toBe(200);
    const first = await page.json() as { events: Array<{ payload: unknown }>; next_cursor: string | null };
    expect(first.events).toHaveLength(1);
    expect(JSON.stringify(first.events[0].payload)).not.toContain("should-not-persist");
    expect(JSON.stringify(first.events[0].payload)).not.toContain("should-not-persist-either");
    expect(first.next_cursor).toEqual(expect.any(String));

    const next = await call(`/v1/telemetry?run_id=${encodeURIComponent(runId)}&limit=1&cursor=${encodeURIComponent(first.next_cursor!)}`, {
      headers: bearer("read-test-token"),
    });
    expect(next.status).toBe(200);
    const secondPage = await next.json() as { events: unknown[]; next_cursor: string | null };
    expect(secondPage.events).toHaveLength(1);
    expect(secondPage.next_cursor).toBeNull();
  });

  it("returns compliant model and streaming completion responses", async () => {
    const models = await call("/v1/models", { headers: bearer("ingest-test-token") });
    expect(models.status).toBe(200);
    expect((await models.json() as { data: Array<{ id: string }> }).data[0].id).toBe("telemetry-ack");

    const oldForward = runtimeEnv.LLM_FORWARD_UNMARKED;
    runtimeEnv.LLM_FORWARD_UNMARKED = "false";
    try {
      const completion = await call("/v1/chat/completions", {
        method: "POST",
        headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
        body: JSON.stringify({ model: "telemetry-ack", messages: [{ role: "user", content: "redacted" }], stream: true }),
      });
      expect(completion.status).toBe(200);
      expect(completion.headers.get("content-type")).toContain("text/event-stream");
      const body = await completion.text();
      expect(body).toContain("data: [DONE]");
      expect(body).not.toContain("\n\n data:");
    } finally {
      runtimeEnv.LLM_FORWARD_UNMARKED = oldForward;
    }
  });

  it("forwards an unmarked telemetry-ack prompt and records only its metadata", async () => {
    const oldUrl = runtimeEnv.LLM_UPSTREAM_URL;
    const oldForward = runtimeEnv.LLM_FORWARD_UNMARKED;
    const runId = `proxy-${crypto.randomUUID()}`;
    runtimeEnv.LLM_UPSTREAM_URL = "https://provider.test/v1";
    runtimeEnv.LLM_FORWARD_UNMARKED = "true";
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const forwarded = JSON.parse(String(init?.body)) as { model: string };
      expect(forwarded.model).toBe("ds/deepseek-flash");
      return new Response(JSON.stringify({ id: "upstream-2", choices: [] }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      const response = await call("/v1/chat/completions", {
        method: "POST",
        headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
        body: JSON.stringify({
          model: "telemetry-ack",
          metadata: { run_id: runId },
          messages: [{ role: "system", content: "advisor" }, { role: "user", content: "secret context" }],
        }),
      });
      expect(response.status).toBe(200);
      expect(response.headers.get("x-gosim26-route")).toBe("llm-proxy");
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
      runtimeEnv.LLM_UPSTREAM_URL = oldUrl;
      runtimeEnv.LLM_FORWARD_UNMARKED = oldForward;
    }
    const page = await call(`/v1/telemetry?run_id=${encodeURIComponent(runId)}`, { headers: bearer("read-test-token") });
    const result = await page.json() as { events: Array<{ event_type: string; payload: Record<string, unknown> }> };
    expect(result.events).toHaveLength(1);
    expect(result.events[0].event_type).toBe("model_request");
    expect(result.events[0].payload.status).toBe(200);
    expect(result.events[0].payload.requested_model).toBe("telemetry-ack");
    expect(result.events[0].payload.message_count).toBe(2);
    expect(JSON.stringify(result.events[0].payload)).not.toContain("secret context");
  });

  it("consumes the marked tool telemetry message without forwarding it", async () => {
    const runId = `tool-${crypto.randomUUID()}`;
    const response = await call("/v1/chat/completions", {
      method: "POST",
      headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
      body: JSON.stringify({
        model: "team-model",
        messages: [{
          role: "tool",
          tool_call_id: "gosim26.telemetry",
          name: "gosim26.telemetry",
          content: JSON.stringify({ schema: "gosim26.telemetry.v1", event: {
            run_id: runId, event_type: "decision_trace", payload: { action: "observe" },
          } }),
        }],
      }),
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("x-telemetry-accepted")).toBe("true");
    const page = await call(`/v1/telemetry?run_id=${encodeURIComponent(runId)}`, {
      headers: bearer("read-test-token"),
    });
    const result = await page.json() as { events: Array<{ event_type: string }> };
    expect(result.events).toHaveLength(1);
    expect(result.events[0].event_type).toBe("decision_trace");
  });

  it("proxies ordinary model traffic and does not persist its prompt", async () => {
    const oldUrl = runtimeEnv.LLM_UPSTREAM_URL;
    const oldKey = runtimeEnv.LLM_UPSTREAM_API_KEY;
    const oldModel = runtimeEnv.LLM_MODEL_NAME;
    const oldThinking = runtimeEnv.LLM_DISABLE_THINKING;
    runtimeEnv.LLM_UPSTREAM_URL = "https://provider.test/v1";
    runtimeEnv.LLM_UPSTREAM_API_KEY = "provider-secret";
    runtimeEnv.LLM_MODEL_NAME = "ds/deepseek-flash";
    runtimeEnv.LLM_DISABLE_THINKING = "true";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("https://provider.test/v1/chat/completions");
      expect(new Headers(init?.headers).get("authorization")).toBe("Bearer provider-secret");
      const forwarded = JSON.parse(String(init?.body)) as {
        model: string; messages: Array<{ content: string }>; thinking: { type: string };
      };
      expect(forwarded.model).toBe("ds/deepseek-flash");
      expect(forwarded.thinking).toEqual({ type: "disabled" });
      expect(forwarded.messages[0].content).toBe("private prompt");
      return new Response(JSON.stringify({ id: "upstream-1", choices: [] }), {
        status: 200,
        headers: { "content-type": "application/json", "content-encoding": "gzip", "content-length": "900" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      const response = await call("/v1/chat/completions", {
        method: "POST",
        headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
        body: JSON.stringify({ model: "team-model", messages: [{ role: "user", content: "private prompt" }] }),
      });
      expect(response.status).toBe(200);
      expect(response.headers.get("x-gosim26-route")).toBe("llm-proxy");
      expect(response.headers.get("content-encoding")).toBeNull();
      expect(response.headers.get("content-length")).toBeNull();
      expect(await response.json()).toEqual({ id: "upstream-1", choices: [] });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
      runtimeEnv.LLM_UPSTREAM_URL = oldUrl;
      runtimeEnv.LLM_UPSTREAM_API_KEY = oldKey;
      runtimeEnv.LLM_MODEL_NAME = oldModel;
      runtimeEnv.LLM_DISABLE_THINKING = oldThinking;
    }
  });

  it("keeps the upstream deadline active until a streamed body finishes", async () => {
    const oldUrl = runtimeEnv.LLM_UPSTREAM_URL;
    const oldTimeout = runtimeEnv.LLM_UPSTREAM_TIMEOUT_MS;
    runtimeEnv.LLM_UPSTREAM_URL = "https://provider.test/v1";
    runtimeEnv.LLM_UPSTREAM_TIMEOUT_MS = "500";
    let aborted = false;
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = new ReadableStream<Uint8Array>({
        start(stream) {
          stream.enqueue(new TextEncoder().encode("data: first\n\n"));
          init?.signal?.addEventListener("abort", () => {
            aborted = true;
            stream.error(new Error("upstream aborted"));
          });
        },
      });
      return new Response(body, { headers: { "content-type": "text/event-stream" } });
    }));
    try {
      const response = await call("/v1/chat/completions", {
        method: "POST",
        headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
        body: JSON.stringify({ model: "team-model", messages: [{ role: "user", content: "hello" }], stream: true }),
      });
      await expect(response.text()).rejects.toThrow();
      expect(aborted).toBe(true);
    } finally {
      vi.unstubAllGlobals();
      runtimeEnv.LLM_UPSTREAM_URL = oldUrl;
      runtimeEnv.LLM_UPSTREAM_TIMEOUT_MS = oldTimeout;
    }
  });

  it("rejects an unadvertised oversized chat body before completing the stream", async () => {
    const request = new Request("https://telemetry.test/v1/chat/completions", {
      method: "POST",
      headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
      body: JSON.stringify({ model: "team-model", messages: [{ role: "user", content: "x".repeat(270_000) }] }),
    });
    expect(request.headers.get("content-length")).toBeNull();
    const response = await worker.fetch(request, runtimeEnv);
    expect(response.status).toBe(413);
    expect((await response.json() as { error: { code: string } }).error.code).toBe("body_too_large");
  });

  it("passes provider failures through and converts connection failures to a bounded error", async () => {
    const oldUrl = runtimeEnv.LLM_UPSTREAM_URL;
    runtimeEnv.LLM_UPSTREAM_URL = "https://provider.test/v1";
    const request = () => call("/v1/chat/completions", {
      method: "POST",
      headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
      body: JSON.stringify({ model: "team-model", messages: [{ role: "user", content: "hello" }] }),
    });
    try {
      vi.stubGlobal("fetch", vi.fn(async () => new Response('{"error":"temporary"}', { status: 502 })));
      const providerError = await request();
      expect(providerError.status).toBe(502);
      expect(providerError.headers.get("x-gosim26-route")).toBe("llm-proxy");

      vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("connection failed"); }));
      const connectionError = await request();
      expect(connectionError.status).toBe(502);
      expect((await connectionError.json() as { error: { code: string } }).error.code).toBe("llm_upstream_error");
    } finally {
      vi.unstubAllGlobals();
      runtimeEnv.LLM_UPSTREAM_URL = oldUrl;
    }
  });

  it("rejects malformed telemetry markers without calling the provider", async () => {
    const oldUrl = runtimeEnv.LLM_UPSTREAM_URL;
    const fetchMock = vi.fn(async () => new Response("should not be called", { status: 200 }));
    runtimeEnv.LLM_UPSTREAM_URL = "https://provider.test/v1";
    vi.stubGlobal("fetch", fetchMock);
    try {
      const response = await call("/v1/chat/completions", {
        method: "POST",
        headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
        body: JSON.stringify({
          model: "team-model",
          messages: [{ role: "tool", tool_call_id: "gosim26.telemetry", content: "not-json" }],
        }),
      });
      expect(response.status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
      runtimeEnv.LLM_UPSTREAM_URL = oldUrl;
    }
  });

  it("accepts structured OpenAI telemetry and records edge network metadata", async () => {
    const runId = `openai-${crypto.randomUUID()}`;
    const event = {
      id: `${runId}-runtime`,
      run_id: runId,
      event_type: "runtime",
      source: "observer",
      client_time_ms: 123,
      sequence: 0,
      payload: { python: "3.14", api_key: "must-not-persist" },
    };
    const response = await call("/v1/chat/completions", {
      method: "POST",
      headers: {
        ...bearer("ingest-test-token"),
        "content-type": "application/json",
        "cf-connecting-ip": "203.0.113.9",
        "x-forwarded-for": "203.0.113.9, 198.51.100.4",
        "cf-ray": "test-ray",
        "cf-ipcountry": "CN",
        "user-agent": "telemetry-client-test",
      },
      body: JSON.stringify({
        model: "telemetry-ack",
        messages: [{ role: "user", content: JSON.stringify({ schema: "gosim26.telemetry.v1", event }) }],
        stream: false,
      }),
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("x-telemetry-accepted")).toBe("true");

    const page = await call(`/v1/telemetry?run_id=${encodeURIComponent(runId)}`, {
      headers: bearer("read-test-token"),
    });
    const result = await page.json() as { events: Array<{ event_type: string; payload: Record<string, unknown> }> };
    expect(result.events).toHaveLength(1);
    expect(result.events[0].event_type).toBe("runtime");
    expect(result.events[0].payload.python).toBe("3.14");
    expect(result.events[0].payload.api_key).toBeUndefined();
    const network = result.events[0].payload.server_network as Record<string, unknown>;
    expect(network.source_ip).toBe("203.0.113.9");
    expect(network.forwarded_for).toContain("198.51.100.4");
    expect(network.cf_ray).toBe("test-ray");
    expect(network.country).toBe("CN");
  });

  it("stores OpenAI message batches and enforces the batch limit", async () => {
    const runId = `batch-${crypto.randomUUID()}`;
    const events = [1, 2].map((sequence) => ({
      id: `${runId}-${sequence}`, run_id: runId, event_type: "decision_trace", sequence,
      payload: { action: sequence === 1 ? "observe" : "wait" },
    }));
    const request = (items: unknown[]) => call("/v1/chat/completions", {
      method: "POST",
      headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
      body: JSON.stringify({ model: "telemetry-ack", messages: [
        { role: "user", content: JSON.stringify({ schema: "gosim26.telemetry.v1", events: items }) },
      ] }),
    });
    expect((await request(events)).status).toBe(200);
    const page = await call(`/v1/telemetry?run_id=${encodeURIComponent(runId)}`, {
      headers: bearer("read-test-token"),
    });
    const result = await page.json() as { events: Array<{ sequence: number }> };
    expect(result.events.map((event) => event.sequence).sort()).toEqual([1, 2]);
    expect((await request([...events, ...events, ...events])).status).toBe(413);
  });

  it("accepts the unversioned chat path used by upstream model relays", async () => {
    const runId = `relay-${crypto.randomUUID()}`;
    const event = { id: `${runId}-runtime`, run_id: runId, event_type: "runtime", payload: { python: "3.12" } };
    const response = await call("/chat/completions", {
      method: "POST",
      headers: { ...bearer("ingest-test-token"), "content-type": "application/json" },
      body: JSON.stringify({
        model: "telemetry-ack",
        messages: [{ role: "user", content: JSON.stringify({ schema: "gosim26.telemetry.v1", event }) }],
      }),
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("x-telemetry-accepted")).toBe("true");

    const page = await call(`/v1/telemetry?run_id=${encodeURIComponent(runId)}`, {
      headers: bearer("read-test-token"),
    });
    const result = await page.json() as { events: Array<{ event_type: string; payload: Record<string, unknown> }> };
    expect(result.events).toHaveLength(1);
    expect(result.events[0].event_type).toBe("runtime");
    expect(result.events[0].payload.python).toBe("3.12");
  });
});
