# GOSIM26 telemetry worker

This is a small Cloudflare Worker for optional observer telemetry. It stores
bounded, sanitized event records in D1 and exposes a read API for later
analysis. The observer never depends on the service for a decision: without
both `TELEMETRY_URL` and `TELEMETRY_TOKEN` it does not start a network thread,
and when enabled it uses a bounded queue, short timeouts, bounded retries and
event drops.

The service intentionally records measurements rather than analysis inputs.
The packaged client lives in `../agents/observer/telemetry.py`; it sends
interpreter and host metrics, initialization sizes, decision counts, planner
timing, process RSS and disk usage. Payload sanitization drops
common secret fields and message/prompt content on both sides. Do not put raw
credentials or prompts into telemetry fields.

## API

All protected routes accept `Authorization: Bearer <token>` or
`x-telemetry-token: <token>`.

* `GET /healthz` is unauthenticated and returns the service version.
* `POST /v1/telemetry` and `/v1/telemetry/batch` accept one event, an array,
  or `{ "events": [...] }`. Ingest uses `TELEMETRY_INGEST_TOKEN`.
* `GET /v1/telemetry?run_id=...&event_type=...&limit=100&cursor=...` reads
  newest-first events with decoded `payload` objects. Read access uses
  `TELEMETRY_READ_TOKEN`.
* `POST /v1/chat/completions` is a minimal OpenAI-compatible acknowledgement
  endpoint. It records request metadata and returns a valid non-streaming
  completion or an SSE completion when `stream` is true.
* `GET /v1/models` returns the `telemetry-ack` model and uses ingest access.

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

For an offline client check, omit `TELEMETRY_URL`; `TelemetryReporter` then
performs no network or disk I/O. To exercise the client against local Wrangler,
set `TELEMETRY_URL=http://127.0.0.1:8790` and
`TELEMETRY_TOKEN=local-ingest-token` when launching the observer.

## First real deployment

After logging in, create a D1 database and replace the placeholder
`database_id` in `wrangler.jsonc`:

```bash
wrangler d1 create gosim26-telemetry
pnpm db:migrate:remote
wrangler secret put TELEMETRY_INGEST_TOKEN
wrangler secret put TELEMETRY_READ_TOKEN
wrangler deploy
```

Keep tokens outside the repository. Set `CORS_ORIGIN` to a specific origin if
the read API will be called by a browser; the default `*` is suitable for
token-authenticated command-line use. The generated D1 database is the source
of truth, while the read endpoint is intentionally simple so analysis scripts
can page through it with `next_cursor`.
