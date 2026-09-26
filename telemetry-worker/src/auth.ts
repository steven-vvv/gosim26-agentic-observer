import type { Env } from "./types";

function presentedToken(request: Request): string | null {
  const authorization = request.headers.get("authorization")?.trim();
  if (authorization) {
    const match = /^Bearer\s+(.+)$/i.exec(authorization);
    if (match?.[1]) return match[1].trim();
  }
  const header = request.headers.get("x-telemetry-token")?.trim();
  return header || null;
}

async function digest(value: string): Promise<Uint8Array> {
  const bytes = new TextEncoder().encode(value);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
}

async function constantTimeEqual(left: string, right: string): Promise<boolean> {
  if (!left || !right || left.length > 512 || right.length > 512) return false;
  const [a, b] = await Promise.all([digest(left), digest(right)]);
  let mismatch = a.length ^ b.length;
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    mismatch |= (a[index] ?? 0) ^ (b[index] ?? 0);
  }
  return mismatch === 0;
}

export async function hasToken(request: Request, expected: string | undefined): Promise<boolean> {
  const provided = presentedToken(request);
  return expected ? await constantTimeEqual(provided ?? "", expected) : false;
}

export async function requireIngestAuth(request: Request, env: Env): Promise<boolean> {
  return hasToken(request, env.TELEMETRY_INGEST_TOKEN);
}

export async function requireReadAuth(request: Request, env: Env): Promise<boolean> {
  return hasToken(request, env.TELEMETRY_READ_TOKEN);
}
