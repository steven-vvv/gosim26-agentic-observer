# Observer v4

A `participant-agent-protocol-v4` survey agent (`jsonl-v4`). `main.py` reads one JSON
message per line and answers every `decision_request` with one action. The build step
installs the bundled Python 3.12 NumPy wheel offline. No scenario truth, generated card or
credential is included.

## Release profiles and startup configuration

The checked-in configuration uses the `full` profile for development and analysis. A
formal package can select the intentionally restrained profile without changing the
source review surface:

```bash
uv run python -m v4.evaluation.package --profile formal_conservative
```

`formal_conservative` keeps the feedback, quality, fault and two model stages enabled,
but uses 300--900 second exposures, disables adaptive duration and centre refinement,
uses a smaller anchor search, and caps model guidance at 12 calls per card (8 per stage).
The selected profile is recorded in the startup `runtime` event and in the package
configuration hash.

When `release.remote_config.enabled` is true, startup makes one bounded POST request to
the configured `/v1/config/resolve` endpoint. `OBSERVER_CONFIG_TOKEN` is preferred and
`TELEMETRY_TOKEN` is the supported formal-run variable for the same Worker deployment
(`OBSERVER_*` names are reserved by the upstream team-egress runtime; the former is a
local/dev override). A response must carry the
versioned schema, matching `config_id`, a non-negative revision and a non-expired patch;
the patch is merged and run through the same local validator. Network errors, expired
responses and invalid fields fall back to the packaged config and are recorded in the
runtime event. If neither explicit token is present, the client may reuse
`OPENAI_API_KEY` only when `OPENAI_BASE_URL` points to the same Worker host and port as
the config endpoint. This compatibility path sends the key only as the request
credential; the endpoint still receives configuration metadata, never prompts or model
requests. A model key is never reused for an unrelated config host.

## Large language model stages

The model path is enabled by default (`config.json`, section `model`). On the platform the
agent reads `OPENAI_BASE_URL` and `OPENAI_API_KEY` and calls the team model through the
OpenAI-compatible chat API in `observer4/llm/model_advisor.py`. Two stages hand the
decision to the model: the live state and the feasible candidates go to the model, and
the model's validated choice is executed. For the upstream Kimi Coding Plan (`k3` or
`kimi-for-coding`), the client omits `temperature`, which that endpoint rejects; regular
OpenAI-compatible endpoints retain the configured value. The advisor bounds its dynamic
request context at 60,000 bytes, leaving room below the upstream 64 KiB request cap; its
65,536-byte response cap is likewise deliberately below the upstream 192 KiB limit:

| Stage | Code | What the model decides |
| --- | --- | --- |
| Task planning / plan adaptation | `observer4/advice.py`, `ModelGuidance.weekly` | At each new weekly forecast, the survey strategy for the coming week: `steady` (the tuned season policy), `required_push` (required-target bonus +30 % and a safer completion factor) or `science_push` (bonus −25 %). The model weighs the forecast notices, the required-target deficit against the nights left, active requests and the wall clock; its choice sets the planner weights until the next forecast. |
| Action decision | `observer4/advice.py`, `ModelGuidance.action` | Once per night, when the planner's two best observations are within 3 % of each other, which one to execute, from their pointings, programs, durations and predicted targets (required flags, science weights, prior best scores). |

The model is told that the controller's merits and fallback are evidence, not a verdict.
Every call has a time-out, schema validation and a circuit breaker after consecutive
failures. The full profile allows 16 calls per stage and 24 in total per card; the
`formal_conservative` package default allows 8 per stage and 12 in total. A startup
remote profile can select either budget. If the endpoint is missing,
slow or returns an invalid answer, the tuned policy (`steady`) or the planner's best
observation is kept for that decision and the run continues.

## Deterministic core

- `observer4/planning.py`: joint search over pointing, fibre assignment, exposure time and
  program; greedy merit per second of science gain, required-target completion, live
  time-limited request progress and RA uniformity. Science gains are convex in the
  completion factor (`policy.science_exponent`), so partial exposures that a later, longer
  one would supersede count less; the convexity fades for targets with few public observing
  hours left (`policy.science_horizon_hours`), which get no later chance. A request exposure
  must end by its public deadline;
  progress is refreshed from each public snapshot and weighted by `policy.request_weight`.
- `observer4/instrument.py`: exact per-exposure quality levels from the returned scores,
  earthquake efficiency tracking and instrument-fault detection and reporting. Besides
  level steps, the detector reads the program-band statuses: bands follow the site weather
  alone, so with the measured level they bound the true instrument efficiency across
  weather changes, segment boundaries and earthquakes, and also calibrate each announced
  shock.
- `observer4/quality.py`, `observer4/terrain.py`, `observer4/sectors.py`: sky-quality
  prediction, program-band probabilities, announced weather sectors and posteriors over the
  terrain and weather sectors.
- `observer4/pointing.py`: pointing-offset estimate from hits and misses.
- `observer4/state.py`: best-score ledger, including `state_resync` handling.
- `observer4/opportunity.py`: expected later chances of each required target.

## Scale and observability

Cards whose estimated geometry work (target count multiplied by fibre count) exceeds the
configured threshold automatically use a smaller candidate search and longer starting
exposure durations. This keeps the 900-second card budget usable on high-fibre cards while
leaving the full search for smaller cards. Within a card the planner shrinks its search
only if the usable wall clock falls below 0.8 of its fair share (the starting budget in
proportion to the observing time left, `policy.pace_floor`); a normal machine never gets
there, so the actions do not depend on timing noise. The planner consumes the runner's
normalized `wallclock.remaining_seconds`; the runner's `speed_factor` and raw CPU fields
are accounting data and do not need to be reconstructed by the agent. Remote diagnostics
require dedicated `TELEMETRY_URL` and `TELEMETRY_TOKEN` credentials, or an injected model
endpoint whose hostname is the configured telemetry host (or
`observability.remote.model_proxy` when the team's model endpoint is the telemetry
Worker); the model proxy key is never sent to an unrelated telemetry host.
