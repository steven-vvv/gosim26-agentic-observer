import { RequestError } from "./validation";
import type { Env } from "./types";

function upstreamEndpoint(value: string): string {
  const base = value.trim().replace(/\/+$/, "");
  if (base.endsWith("/chat/completions")) return base;
  if (base.endsWith("/v1")) return `${base}/chat/completions`;
  return `${base}/v1/chat/completions`;
}

/**
 * A request asks to keep provider reasoning with `chat_template_kwargs: {"thinking": true}`,
 * the one field of its kind that the platform proxy forwards. Reading an encoded duty log
 * needs reasoning; the short planning advice does not.
 */
export function wantsThinking(body: Record<string, unknown>): boolean {
  const kwargs = body.chat_template_kwargs;
  return typeof kwargs === "object" && kwargs !== null && (kwargs as Record<string, unknown>).thinking === true;
}

function timeoutMs(env: Env, thinking: boolean): number {
  const raw = thinking ? env.LLM_THINKING_TIMEOUT_MS ?? "110000" : env.LLM_UPSTREAM_TIMEOUT_MS ?? "15000";
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) ? Math.max(500, Math.min(value, 120000)) : 15000;
}

/**
 * The provider body: the configured model ID and, when `LLM_DISABLE_THINKING` is
 * "true", reasoning switched off unless the request asked to keep it (the marker is
 * not forwarded). A reasoning model otherwise spends the whole `max_tokens` on hidden
 * reasoning, and the platform proxy forwards no provider field that could turn it off.
 */
function forwardedBody(body: Record<string, unknown>, env: Env): Record<string, unknown> {
  const thinking = wantsThinking(body);
  const { chat_template_kwargs: _marker, ...rest } = body;
  const forwarded: Record<string, unknown> = { ...(thinking ? rest : body), model: env.LLM_MODEL_NAME?.trim() || body.model };
  if (!thinking && env.LLM_DISABLE_THINKING?.trim().toLowerCase() === "true") {
    forwarded.thinking = { type: "disabled" };
  }
  return forwarded;
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
  const timer = setTimeout(() => controller.abort(), timeoutMs(env, wantsThinking(body)));
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
      body: JSON.stringify(forwardedBody(body, env)),
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
