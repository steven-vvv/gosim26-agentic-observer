import { requireConfigAdminAuth, requireConfigReadAuth } from "./auth";
import { readJson, RequestError } from "./validation";
import type { Env } from "./types";

const RESPONSE_SCHEMA = "gosim26.v4.config-response.v1";
const REQUEST_SCHEMA = "gosim26.v4.config-request.v1";
const CONFIG_ID = /^[A-Za-z0-9._:-]{1,96}$/;
const MAX_PATCH_BYTES = 64 * 1024;

interface ConfigRow {
  config_id: string;
  revision: number;
  enabled: number;
  patch_json: string;
  expires_at_ms: number | null;
  updated_at_ms: number;
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function configId(value: unknown): string {
  if (typeof value !== "string" || !CONFIG_ID.test(value.trim())) {
    throw new RequestError(400, "invalid_config_id", "config_id must contain only letters, numbers, '.', '_' , ':' or '-'");
  }
  return value.trim();
}

function nonNegativeInteger(value: unknown, name: string): number | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new RequestError(400, `invalid_${name}`, `${name} must be a non-negative integer`);
  }
  return value;
}

function patchJson(value: unknown): string {
  const patch = objectValue(value);
  if (!patch) throw new RequestError(400, "invalid_config_patch", "patch must be a JSON object");
  const encoded = JSON.stringify(patch);
  if (new TextEncoder().encode(encoded).byteLength > MAX_PATCH_BYTES) {
    throw new RequestError(413, "config_patch_too_large", "patch exceeds 64 KiB");
  }
  return encoded;
}

function response(row: ConfigRow): Response {
  let patch: unknown;
  try {
    patch = JSON.parse(row.patch_json);
  } catch {
    throw new Error("stored configuration patch is invalid JSON");
  }
  return new Response(JSON.stringify({
    schema: RESPONSE_SCHEMA,
    config_id: row.config_id,
    revision: row.revision,
    patch,
    expires_at_ms: row.expires_at_ms,
    updated_at_ms: row.updated_at_ms,
  }), { headers: { "content-type": "application/json; charset=utf-8" } });
}

export async function resolveConfig(request: Request, env: Env): Promise<Response> {
  if (!(await requireConfigReadAuth(request, env))) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  const body = objectValue(await readJson(request, env));
  if (!body) throw new RequestError(400, "invalid_request", "config request must be an object");
  if (body.schema !== REQUEST_SCHEMA) throw new RequestError(400, "unsupported_schema", "unsupported configuration request schema");
  const id = configId(body.config_id ?? "observer4");
  const row = await env.DB.prepare(
    "SELECT config_id, revision, enabled, patch_json, expires_at_ms, updated_at_ms FROM agent_configs WHERE config_id = ?",
  ).bind(id).first<ConfigRow>();
  if (!row || row.enabled !== 1) throw new RequestError(404, "config_not_found", "configuration is not available");
  if (row.expires_at_ms !== null && row.expires_at_ms <= Date.now()) {
    throw new RequestError(410, "config_expired", "configuration has expired");
  }
  return response(row);
}

export async function putConfig(request: Request, env: Env): Promise<Response> {
  if (!(await requireConfigAdminAuth(request, env))) return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  const body = objectValue(await readJson(request, env));
  if (!body) throw new RequestError(400, "invalid_request", "config request must be an object");
  const id = configId(body.config_id);
  const patch = patchJson(body.patch);
  const expected = nonNegativeInteger(body.expected_revision, "expected_revision");
  const enabledValue = body.enabled === undefined ? true : body.enabled;
  if (typeof enabledValue !== "boolean") throw new RequestError(400, "invalid_enabled", "enabled must be boolean");
  const expiresAt = nonNegativeInteger(body.expires_at_ms, "expires_at_ms");
  const current = await env.DB.prepare("SELECT revision FROM agent_configs WHERE config_id = ?").bind(id).first<{ revision: number }>();
  const currentRevision = current?.revision ?? 0;
  if (expected !== null && expected !== currentRevision) {
    throw new RequestError(409, "revision_conflict", `expected revision ${expected}, current revision is ${currentRevision}`);
  }
  const row: ConfigRow = {
    config_id: id,
    revision: currentRevision + 1,
    enabled: enabledValue ? 1 : 0,
    patch_json: patch,
    expires_at_ms: expiresAt,
    updated_at_ms: Date.now(),
  };
  const sql = `INSERT INTO agent_configs
      (config_id, revision, enabled, patch_json, expires_at_ms, updated_at_ms)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(config_id) DO UPDATE SET
        revision = excluded.revision,
        enabled = excluded.enabled,
        patch_json = excluded.patch_json,
        expires_at_ms = excluded.expires_at_ms,
        updated_at_ms = excluded.updated_at_ms`;
  const statement = expected === null
    ? env.DB.prepare(sql).bind(row.config_id, row.revision, row.enabled, row.patch_json, row.expires_at_ms, row.updated_at_ms)
    : env.DB.prepare(`${sql} WHERE agent_configs.revision = ?`).bind(
      row.config_id, row.revision, row.enabled, row.patch_json, row.expires_at_ms, row.updated_at_ms, expected,
    );
  const result = await statement.run();
  if (expected !== null && Number(result.meta?.changes ?? 0) === 0) {
    throw new RequestError(409, "revision_conflict", "configuration changed while publishing");
  }
  return response(row);
}
