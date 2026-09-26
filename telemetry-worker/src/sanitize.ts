import type { JsonValue } from "./types";

const MAX_DEPTH = 6;
const MAX_KEYS = 96;
const MAX_ITEMS = 128;
const MAX_STRING = 4096;

function blockedKey(key: string): boolean {
  const normalized = key.toLowerCase().replace(/-/g, "_");
  if (new Set([
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "cookie",
    "prompt",
    "messages",
  ]).has(normalized)) return true;
  return ["private", "bearer", "credential", "passphrase"].some((marker) => normalized.includes(marker));
}

/** Recursively bound telemetry payloads so an accidental secret or huge object is not retained. */
export function sanitize(value: unknown, depth = 0): JsonValue {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return value.slice(0, MAX_STRING);
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (depth >= MAX_DEPTH) return "[depth-limit]";
  if (Array.isArray(value)) return value.slice(0, MAX_ITEMS).map((item) => sanitize(item, depth + 1));
  if (typeof value !== "object") return null;

  const output: { [key: string]: JsonValue } = {};
  for (const [key, child] of Object.entries(value).slice(0, MAX_KEYS)) {
    if (blockedKey(key)) continue;
    output[key.slice(0, 128)] = sanitize(child, depth + 1);
  }
  return output;
}

export function jsonBytes(value: JsonValue): { json: string; bytes: number } {
  const json = JSON.stringify(value);
  return { json, bytes: new TextEncoder().encode(json).byteLength };
}
