# GOSIM26 telemetry worker

This is a small Cloudflare Worker for observer telemetry. It stores bounded,
sanitized event records in D1 and exposes a read API for later analysis. The
complete-project packer includes a non-secret default endpoint and enables the
client by default. When `OPENAI_BASE_URL` points to this Worker,
`OPENAI_API_KEY` authenticates both the optional model advisor and marked
telemetry messages; a dedicated `TELEMETRY_TOKEN` remains available for a
separate ingestion route.
The observer never depends on the service for a
decision: it uses a bounded queue, short timeouts, bounded retries and event
drops, and remains offline when no key is present.

The service intentionally records measurements rather than analysis inputs.
The packaged client lives in `../agents/observer/telemetry.py`; it sends
interpreter and host metrics, initialization sizes, decision counts, planner
timing, process RSS and disk usage. Payload sanitization drops common secret
fields and message/prompt content on both sides. The Worker adds a
`server_network` object containing selected edge metadata (including the
source IP when Cloudflare supplies it, forwarding metadata, country/colo,
protocol and TLS fields). Do not put raw credentials or prompts into telemetry
fields.

The Wrangler configuration also enables Cloudflare Workers Observability Logs
with persisted invocation logs and 100% head sampling. This is a second,
independent signal path for Worker requests, `console` output, errors and
runtime metadata; D1 remains the source of truth for structured telemetry.
Tracing is intentionally left disabled until there is a measured need for
span-level dependency timing.

## API

All protected routes accept `Authorization: Bearer <token>` or
`x-telemetry-token: <token>`.

* `GET /healthz` is unauthenticated and returns the service version.
* `POST /v1/telemetry` and `/v1/telemetry/batch` accept one event, an array,
  or `{ "events": [...] }`. Ingest uses `TELEMETRY_INGEST_TOKEN`.
* `GET /v1/telemetry?run_id=...&event_type=...&limit=100&cursor=...` reads
  newest-first events with decoded `payload` objects. Read access uses
  `TELEMETRY_READ_TOKEN`.
* `POST /v1/chat/completions` (also `/chat/completions` for relays that omit
  `/v1`) is the shared OpenAI-compatible endpoint. A message with
  `role=tool`, `tool_call_id=gosim26.telemetry` and `name=gosim26.telemetry`
  is consumed as telemetry and never reaches the model provider. The Worker
  stores normalized events and returns a valid non-streaming completion or an
  SSE completion when `stream` is true. Ordinary Chat Completions requests
  are forwarded to `LLM_UPSTREAM_URL` with `LLM_UPSTREAM_API_KEY`. When
  `LLM_MODEL_NAME` is set, the Worker uses that model ID for every forwarded
  request; the current deployment uses `ds/deepseek-flash` only. Prompts and
  provider responses are not written to D1; each forwarded request stores one
  `model_request` event with metadata only (requested and forwarded model,
  message count, upstream status and latency). `LLM_DISABLE_THINKING=true` adds
  `thinking: {"type": "disabled"}` so a reasoning model answers within
  `max_tokens`; `LLM_FORWARD_UNMARKED=true` also forwards prompts that arrive
  with model `telemetry-ack` but carry no telemetry (a platform proxy that
  substitutes that saved model name). Direct Worker testing may use a
  base URL ending in `/v1` or the Worker root, with the ingest token as the
  bearer credential.
* `GET /v1/models` returns `telemetry-ack` and, when configured, the upstream
  model name; it uses ingest access.

* `POST /v1/config/resolve` returns the current non-expired patch for an agent startup
  request. It requires `CONFIG_READ_TOKEN`, or `TELEMETRY_INGEST_TOKEN` when the
  separate read secret is not configured. The request schema is
  `gosim26.v4.config-request.v1`; the response is
  `gosim26.v4.config-response.v1` and includes a monotonically increasing revision.
* `POST /v1/config/public/resolve` is an optional unauthenticated startup route enabled by
  `CONFIG_PUBLIC_READ=true`. It is restricted to `CONFIG_PUBLIC_ID` (default `observer4`)
  and returns only the approved `release.profile` selector (`full` or
  `formal_conservative`). It never exposes arbitrary stored patch fields; the authenticated
  route remains the one to use for complete patches.
* `PUT /v1/config` publishes a patch and requires the separate
  `CONFIG_ADMIN_TOKEN`. Include `config_id`, an object `patch`, and optionally
  `expected_revision`, `enabled`, and `expires_at_ms`. A stale expected revision is
  rejected with `409`, so two operators cannot silently overwrite each other.

The config service stores only bounded JSON patches in the `agent_configs` D1 table. The
agent applies them to its checked-in configuration and runs the same schema validator;
the Worker does not receive model keys, prompts or decision data through this route.

An event has `run_id`, `event_type`, `source`, optional client timestamp and
sequence, and a `payload` object. The worker limits request size, batch size,
payload depth and payload bytes. D1 rows older than the configured retention
period (30 days by default) are removed by the scheduled handler.

## Local test

The commands below use local Wrangler state and never require Cloudflare login.

```bash
pnpm install
cp .dev.vars.example .dev.vars
pnpm db:migrate:local
pnpm typecheck
pnpm test
python -m unittest discover -s client -p 'test_*.py'
pnpm dev -- --port 8790
```

With the local server running, send a test event (use the token from
`.dev.vars`):

```bash
curl -i http://127.0.0.1:8790/healthz
curl -i -X POST http://127.0.0.1:8790/v1/telemetry \
  -H 'authorization: Bearer local-ingest-token' \
  -H 'content-type: application/json' \
  --data '{"run_id":"local","event_type":"smoke","payload":{"ok":true}}'
curl -s 'http://127.0.0.1:8790/v1/telemetry?run_id=local' \
  -H 'authorization: Bearer local-read-token'
```

For an offline client check, omit all telemetry variables; `TelemetryReporter`
then performs no network or disk I/O. The explicit event transport uses
`TELEMETRY_URL` and `TELEMETRY_TOKEN`. To use the OpenAI-compatible transport,
set `OPENAI_BASE_URL=http://127.0.0.1:8790`,
`OPENAI_API_KEY=local-ingest-token`, and optionally
`TELEMETRY_MODEL=telemetry-ack` (the default). The client normalizes the base
URL to `/v1/chat/completions` and sends the marked tool message. The same
variables are used by the optional observer model advisor; configure the
Worker's `LLM_UPSTREAM_*` values when ordinary model traffic should be
forwarded. `TELEMETRY_URL`/`TELEMETRY_TOKEN` take priority; set
`TELEMETRY_DISABLE_OPENAI=1` to prevent the OpenAI fallback.

## First real deployment

For a new deployment, create a D1 database and set its `database_id` in
`wrangler.jsonc`. The existing deployment already has this database. Set the
telemetry tokens once, then configure the upstream from the private local file:

```bash
pnpm exec wrangler d1 create gosim26-telemetry
pnpm run db:migrate:remote
pnpm exec wrangler secret put TELEMETRY_INGEST_TOKEN
pnpm exec wrangler secret put TELEMETRY_READ_TOKEN
pnpm exec wrangler secret put CONFIG_ADMIN_TOKEN
# Optional: otherwise config reads use TELEMETRY_INGEST_TOKEN.
pnpm exec wrangler secret put CONFIG_READ_TOKEN
set +x
set -a
source ~/.config/gosim26-telemetry.env
set +a
printf '%s' "$OBSERVER_MODEL_BASE_URL" | pnpm exec wrangler secret put LLM_UPSTREAM_URL
printf '%s' "$OBSERVER_MODEL_API_KEY" | pnpm exec wrangler secret put LLM_UPSTREAM_API_KEY
pnpm exec wrangler deploy
```

The upstream secrets use the same provider endpoint and key as the local
advisor test; do not paste their values into the repository or terminal
output. The model ID is the non-secret `LLM_MODEL_NAME` variable in
`wrangler.jsonc`. A provider failure returns a bounded error to the agent,
whose optional advisor falls back to the deterministic choice.

Keep tokens outside the repository. Set `CORS_ORIGIN` to a specific origin if
the read API will be called by a browser; the default `*` is suitable for
token-authenticated command-line use. The generated D1 database is the source
of truth, while the read endpoint is intentionally simple so analysis scripts
can page through it with `next_cursor`.
