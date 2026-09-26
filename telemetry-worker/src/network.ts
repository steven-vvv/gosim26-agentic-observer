import { sanitize } from "./sanitize";
import type { JsonValue } from "./types";

type CloudflareRequest = Request & { cf?: Record<string, unknown> };

function header(request: Request, name: string, max = 512): string | null {
  const value = request.headers.get(name)?.trim();
  return value ? value.slice(0, max) : null;
}

function cfValue(cf: Record<string, unknown>, name: string): unknown {
  const value = cf[name];
  return value === undefined ? null : value;
}

/**
 * Return deliberately selected edge/network metadata.  Authentication
 * headers and arbitrary request headers are never copied into telemetry.
 */
export function requestNetworkMetadata(request: Request): JsonValue {
  const url = new URL(request.url);
  const cf = (request as CloudflareRequest).cf ?? {};
  return sanitize({
    source_ip: header(request, "cf-connecting-ip"),
    forwarded_for: header(request, "x-forwarded-for"),
    cf_ray: header(request, "cf-ray"),
    user_agent: header(request, "user-agent"),
    method: request.method,
    scheme: url.protocol.replace(":", ""),
    host: url.host,
    path: url.pathname,
    country: cfValue(cf, "country") ?? header(request, "cf-ipcountry", 16),
    continent: cfValue(cf, "continent"),
    region: cfValue(cf, "region"),
    city: cfValue(cf, "city"),
    timezone: cfValue(cf, "timezone"),
    colo: cfValue(cf, "colo"),
    asn: cfValue(cf, "asn"),
    as_organization: cfValue(cf, "asOrganization"),
    http_protocol: cfValue(cf, "httpProtocol"),
    tls_version: cfValue(cf, "tlsVersion"),
    tls_cipher: cfValue(cf, "tlsCipher"),
    client_accept_encoding: cfValue(cf, "clientAcceptEncoding"),
  });
}

/** Add authoritative server-side metadata after client payload sanitization. */
export function withServerNetwork(payload: unknown, network: JsonValue): JsonValue {
  const merged = payload && typeof payload === "object" && !Array.isArray(payload)
    ? { ...(payload as Record<string, unknown>), server_network: network }
    : { value: payload, server_network: network };
  return sanitize(merged);
}
