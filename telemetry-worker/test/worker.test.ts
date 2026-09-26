import { beforeAll, describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import worker from "../src/index";
import type { Env } from "../src/types";

const runtimeEnv = {
  ...env,
  TELEMETRY_INGEST_TOKEN: "ingest-test-token",
  TELEMETRY_READ_TOKEN: "read-test-token",
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
  });
});
