import type { D1Database } from "@cloudflare/workers-types";
import type { NormalizedEvent, QueryEvents, StoredEvent } from "./types";

const INSERT = `INSERT OR IGNORE INTO telemetry_events
  (id, run_id, event_type, source, client_time_ms, sequence, received_at_ms, payload_json, payload_bytes)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`;

export async function insertEvents(db: D1Database, events: NormalizedEvent[]): Promise<number> {
  if (events.length === 0) return 0;
  const statements = events.map((event) => db.prepare(INSERT).bind(
    event.id,
    event.run_id,
    event.event_type,
    event.source,
    event.client_time_ms,
    event.sequence,
    event.received_at_ms,
    event.payload_json,
    event.payload_bytes,
  ));
  const results = await db.batch(statements);
  return results.reduce((count, result) => count + Number(result.meta?.changes ?? 0), 0);
}

function encodeCursor(event: Pick<StoredEvent, "received_at_ms" | "id">): string {
  const bytes = new TextEncoder().encode(JSON.stringify([event.received_at_ms, event.id]));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function decodeCursor(value: string | undefined): [number, string] | null {
  if (!value) return null;
  try {
    const binary = atob(value);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const decoded = JSON.parse(new TextDecoder().decode(bytes));
    if (Array.isArray(decoded) && typeof decoded[0] === "number" && typeof decoded[1] === "string") {
      return [decoded[0], decoded[1]];
    }
  } catch {
    // The caller gets a clean empty page for a malformed cursor.
  }
  return null;
}

export async function queryEvents(db: D1Database, query: QueryEvents): Promise<{ events: StoredEvent[]; next_cursor: string | null }> {
  const clauses = ["1 = 1"];
  const values: Array<string | number> = [];
  if (query.run_id) {
    clauses.push("run_id = ?");
    values.push(query.run_id);
  }
  if (query.event_type) {
    clauses.push("event_type = ?");
    values.push(query.event_type);
  }
  if (query.source) {
    clauses.push("source = ?");
    values.push(query.source);
  }
  if (query.since_ms !== undefined) {
    clauses.push("received_at_ms >= ?");
    values.push(query.since_ms);
  }
  if (query.until_ms !== undefined) {
    clauses.push("received_at_ms <= ?");
    values.push(query.until_ms);
  }
  const cursor = decodeCursor(query.cursor);
  if (query.cursor && !cursor) throw new Error("invalid cursor");
  if (cursor) {
    clauses.push("(received_at_ms < ? OR (received_at_ms = ? AND id < ?))");
    values.push(cursor[0], cursor[0], cursor[1]);
  }
  const sql = `SELECT id, run_id, event_type, source, client_time_ms, sequence,
      received_at_ms, payload_json, payload_bytes
    FROM telemetry_events
    WHERE ${clauses.join(" AND ")}
    ORDER BY received_at_ms DESC, id DESC
    LIMIT ?`;
  // Read one extra row so the client gets a cursor only when another page exists.
  values.push(query.limit + 1);
  const result = await db.prepare(sql).bind(...values).all<StoredEvent>();
  const rows = result.results ?? [];
  const hasMore = rows.length > query.limit;
  const events = rows.slice(0, query.limit);
  return {
    events,
    next_cursor: hasMore && events.length > 0 ? encodeCursor(events[events.length - 1]) : null,
  };
}

export async function deleteExpired(db: D1Database, retentionDays: number): Promise<number> {
  const cutoff = Date.now() - retentionDays * 86_400_000;
  const result = await db.prepare("DELETE FROM telemetry_events WHERE received_at_ms < ?").bind(cutoff).run();
  return Number(result.meta?.changes ?? 0);
}
