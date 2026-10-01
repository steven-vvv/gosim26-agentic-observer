# Observer v4

A `participant-agent-protocol-v4` survey agent (`jsonl-v4`). `main.py` reads one JSON
message per line and answers every `decision_request` with one action. The build step
installs the bundled Python 3.12 NumPy wheel offline. No scenario truth, generated card or
credential is included.

## Large language model stages

The model path is enabled by default (`config.json`, section `model`). On the platform the
agent reads `OPENAI_BASE_URL` and `OPENAI_API_KEY` and calls the team model through the
OpenAI-compatible chat API in `observer4/llm/model_advisor.py`. Two stages send the live
state and the candidate decisions to the model, and the model's validated choice is
executed:

| Stage | Code | What the model decides |
| --- | --- | --- |
| Task planning / plan adaptation | `observer4/advice.py`, `ModelGuidance.weekly` | At each new weekly forecast, the survey strategy for the coming nights (`balanced` or `coverage_focus`). The choice sets the planner's required-target bonus and safety factor. |
| Action decision | `observer4/advice.py`, `ModelGuidance.action` | Which of the two best observations (pointing, fibre assignment, exposure and program) to execute when their estimated merits are close. |

Every call has a time-out, a per-card call budget and schema validation. If the endpoint
is missing, slow or returns an invalid answer, the agent keeps the deterministic choice
for that decision and continues.

## Deterministic core

- `observer4/planning.py`: joint search over pointing, fibre assignment, exposure time and
  program; greedy merit per second of science gain, required-target completion, live
  time-limited request progress and RA uniformity. A request exposure must end by its
  public deadline; progress is refreshed from each public snapshot.
- `observer4/instrument.py`: exact per-exposure quality levels from the returned scores,
  earthquake efficiency tracking and instrument-fault detection and reporting.
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
leaving the full search for smaller cards. Remote diagnostics require dedicated
`TELEMETRY_URL` and `TELEMETRY_TOKEN` credentials, or an injected model endpoint whose
hostname is the configured telemetry host; the model proxy key is never sent to telemetry
by default.
