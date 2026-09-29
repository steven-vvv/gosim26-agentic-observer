import { RequestError } from "./validation";
import type { Env } from "./types";

function upstreamEndpoint(value: string): string {
  const base = value.trim().replace(/\/+$/, "");
  if (base.endsWith("/chat/completions")) return base;
  if (base.endsWith("/v1")) return `${base}/chat/completions`;
  return `${base}/v1/chat/completions`;
}

function timeoutMs(env: Env): number {
  const value = Number.parseInt(env.LLM_UPSTREAM_TIMEOUT_MS ?? "15000", 10);
  return Number.isFinite(value) ? Math.max(500, Math.min(value, 120000)) : 15000;
}

/** Forward an ordinary OpenAI request without persisting its prompt. */
export async function proxyChatCompletion(
  request: Request,
  body: Record<string, unknown>,
  env: Env,
): Promise<Response> {
  if (!env.LLM_UPSTREAM_URL?.trim()) {
    throw new RequestError(503, "llm_upstream_unavailable", "LLM upstream is not configured");
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs(env));
  try {
    const headers = new Headers({
      "content-type": "application/json",
      accept: request.headers.get("accept") ?? "application/json",
      "user-agent": "gosim26-telemetry-proxy/1",
    });
    if (env.LLM_UPSTREAM_API_KEY?.trim()) {
      headers.set("authorization", `Bearer ${env.LLM_UPSTREAM_API_KEY.trim()}`);
    }
    const upstream = await fetch(upstreamEndpoint(env.LLM_UPSTREAM_URL), {
      method: "POST",
      headers,
      body: JSON.stringify({ ...body, model: env.LLM_MODEL_NAME?.trim() || body.model }),
      signal: controller.signal,
    });
    const responseHeaders = new Headers();
    for (const name of ["content-type", "cache-control", "retry-after"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    responseHeaders.set("x-gosim26-route", "llm-proxy");
    if (!upstream.body) {
      clearTimeout(timer);
      return new Response(null, { status: upstream.status, statusText: upstream.statusText, headers: responseHeaders });
    }
    const reader = upstream.body.getReader();
    const boundedBody = new ReadableStream<Uint8Array>({
      async pull(stream) {
        try {
          const chunk = await reader.read();
          if (chunk.done) {
            clearTimeout(timer);
            stream.close();
          } else {
            stream.enqueue(chunk.value);
          }
        } catch (error) {
          clearTimeout(timer);
          stream.error(error);
        }
      },
      async cancel(reason) {
        clearTimeout(timer);
        controller.abort();
        await reader.cancel(reason);
      },
    });
    return new Response(boundedBody, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    clearTimeout(timer);
    if (error instanceof Error && error.name === "AbortError") {
      throw new RequestError(504, "llm_upstream_timeout", "LLM upstream request timed out");
    }
    throw new RequestError(502, "llm_upstream_error", "LLM upstream request failed");
  }
}
