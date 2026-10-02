# SKILL: build, test and submit an Agent Observer entry (challenge v3)

Platform website: https://create.gosim.org/survey26/platform
Backend (Supabase) URL: https://vdiemcofukuxglqsmlyz.supabase.co
Public anon key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkaWVtY29mdWt1eGdscXNtbHl6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5NTU3OTcsImV4cCI6MjEwNDUzMTc5N30.qMbZ_C3iKq70SdGDYQPPUD0LMBst-xBspZ-zj_gn75w

Follow the steps in order. Commands assume Python 3.9+ (`python3`; the macOS system python3 works; on Windows use `py -3`);
the kit itself needs no extra packages. `python3 --version` first.
Use absolute paths when running from another directory.

## 1. Get the kit

1. Download `https://create.gosim.org/survey26/platform/downloads/agent-observer-starter-kit.zip` (the "Starter kit" button on https://create.gosim.org/survey26/platform/resources) and unzip it.
2. `cd agent-observer-starter-kit`. Layout: `agent/` (the submission), `challenge/` (environment, read-only),
   `scenarios/dev-reference/` (public 180-night scenario), `local_runner.py`, `score_decisions.py`,
   `make_scenario.py`, `fetch_scenario.py`, `pack_agent.py`, `sac_submit.py`, `README.md`.

## 2. Run the baseline

```
python3 local_runner.py --scenario scenarios/dev-reference --agent agent/minimal_agent.py --wallclock 600 --out run_output
```

Standard output ends with a JSON summary (`--quiet` prints only that). Expected for the unmodified kit: `"termination_reason": "survey_complete"`,
`"total"` ≈ 12287.48, `"completed_tiles": 64`, `"required_missing": 0`, `wall_seconds` ≈ 10-20. Anything else
means the environment is broken; read `run_output/agent.log` first. Exit code 2 means the agent crashed
(`agent_error`) or failed to start.

## 3. Understand the task

1. Protocol `participant-agent-protocol-v2`, one JSON object per line on stdin/stdout. The platform sends one
   `initialize` (no reply), then repeats `decision_request` → your `decision_response` with the same
   `decision_sequence`. Full envelope shapes are in `README.md`; `agent/protocol.py` validates them.
2. Each `decision_request.payload` (`decision-snapshot-v3`) gives `cursor` (slot, time, offset),
   `current_site_weather`, `candidate_tiles` (each legal start now, with `tile_science_value`, window,
   `geometry.airmass`, `geometry.lunar_quality_factor`, `effective_weather`, `already_completed`),
   `active_requests` with visit progress, `progress`, `tile_last_finished` (the realized score of your last
   finished exposure, `null` before the first), and on the first decision of a night `night_start`
   (that night's tile windows), every 7th night `weekly` (weather forecast revisions, multi-night windows,
   requests), and — only once a fault report of yours was correct — `fault_status` (fault scope, effective
   efficiency multiplier, repair completion time, re-published nightly until repaired). Snapshot weather never
   carries `instrument_efficiency`: the preview baseline is efficiency-free by design, so the deviation of a
   realized score from its baseline isolates the hidden instrument side (jitter × fault × tag).
3. Response: `{"action":"observe","tile_id":...,"program":"DARK|BRIGHT|BACKUP","request_id":"","reason":"..."}`
   or `{"action":"wait","reason":"..."}`, optionally with a `"reports"` array — entries
   `{"kind":"Instrument_Failure"}` or `{"kind":"NOVA"|"Reddening","tile_id":"..."}` that report anomalies you
   detected. Reports never consume slot time; malformed entries are dropped (the action still counts);
   accepted reports land in `decisions.csv` as `report_*` action rows right after their carrier decision.
   `observe` runs `nominal_exptime_seconds` from the cursor and may cross
   slot boundaries; `wait` consumes the rest of the current slot.
4. Time: one global wall clock per scenario (`initialize.global_wallclock_seconds`, also `SAC_WALLCLOCK_SECONDS`
   in the environment; 7200 s on the reference scenario). No per-decision limit. When it expires the process is
   killed and everything not yet observed scores nothing — a slow agent that only reaches night 40 of 180 loses
   1000 per unfinished REQUIRED tile. Budget roughly `wallclock / expected_decisions` per decision; the
   reference scenario has about 7,900 slots.
5. Score (`challenge-score-v3`): per exposure `V_tile * A_used * (1 + bonus)` where
   `A_used = instrument_efficiency*transparency*sky_quality/(seeing*airmass) * lunar_quality_factor`; the bonus
   (0.25/0.15/0.08) only when `program` matches the band of `A_used` (DARK ≥ 0.65, BRIGHT ≥ 0.40, else BACKUP) —
   bands are computed on the efficiency-free quality, so the preview and the scorer always agree on them while
   efficiency still scales the score.
   Repeat observations are legal: a tile banks the **maximum** over its observations, and completion still banks
   on the first legal one. Hidden tags multiply a tile's score (nova ×1.5, reddening ×0.8, stacking); a
   region-scoped instrument fault collapses its efficiency (multiplier down to 0.10), is never forecast, and
   does not end on its own — only a correct report's two-day repair ends it, otherwise it runs to the survey end.
   Penalties: 2000 unsafe observe (`is_observable` false at start), 100 invalid action, 0.001/s avoidable wait,
   1000 per missed REQUIRED tile, 100 per FLEXIBLE tile short of 4 per region, and each expired request's
   `miss_penalty`. Completed requests add their `completion_reward`. On top of that,
   `coverage_bonus = coverage_bonus_weight × base_science × Jain_evenness` over finished tiles per region
   (1.0 when evenly spread): the weight is 0 in practice scenarios, 0.35 in the competition ones — there,
   which regions you observe changes the score.
6. Reporting: compare `tile_last_finished.score` against the public-formula estimate of that exposure.
   Baselines are efficiency-free, so reads sit at ≈ 0.90–1.00 from jitter alone; ≈ 1.35–1.5 → NOVA,
   ≈ 0.72–0.80 → Reddening, persistently below 0.70 → Instrument_Failure. These bands are a heuristic for
   spotting anomalies, not a rule the scorer applies. Reads taken under a forecasted
   cold_wave carry a legitimate efficiency dip — skip them. Correct tags earn +100,
   wrong ones cost −150 (first report per tile and tag counts; both tags may be reported on one tile), so only
   report with solid evidence — the shipped `anomaly_detection.py` shows one conservative way (a tag must
   dominate the tile's read history; a fault needs repeated collapses). A correct fault report publishes
   `fault_status` one simulated day later and starts a two-day repair; with no active fault the same report is
   a misreport (one free per correct report, then 100 each) and gets a one-night `"status":"normal"` answer.
   The short `demo-week` scenario (see `QUICKSTART.md`) is only seven nights long, so the shipped detector
   resolves just part of its hidden tags there — it can miss tags and file a wrong report. That is expected:
   it is a demonstration detector, not a calibrated solution; `scenarios/dev-reference` is the 180-night
   scenario where it reports all four tags.
7. Scenario directory (`scenarios/<name>/`): `config/*.json` (calendar, tiles, weather, requests, workflow,
   score) and `outputs/reference/*.csv` (`night_calendar`, `slots`, `tiles`, `targets`, `tile_windows`,
   `observation_requests`, `observation_request_tiles`, `weather`, `weather_forecasts`, `weather_events`,
   and `tile_anomalies` when the scenario ships hidden tags).
   `README.md` lists every file. Weather events are directional (`REGION_SET`, `SKY_CAP_ICRS`, `HORIZON_SECTOR`
   or `ALL`) and some force a closure, so a candidate's `effective_weather` can differ from
   `current_site_weather`; forecasts are uncertain and revised daily. Requests have deadline classes
   `ONE_WEEK` / `TWO_WEEKS` / `ONE_MONTH` and completion modes `ALL` / `AT_LEAST_N`.
8. `agent/scoring_preview.py` (`preview_actions(snapshot, scoring_contract)`) computes the public estimate for every
   legal candidate, including terminal-penalty avoidance and request value; it is what the baseline ranks by.
   Pass a `tile_best_scores` map to value repeat observations by their real marginal gain.

## 4. Generate more scenarios

```
python3 make_scenario.py --out scenarios/s7 --seed 7 --days 30
python3 make_scenario.py --out scenarios/s21 --seed 21 --days 60 --start-date 2026-12-01
python3 local_runner.py --scenario scenarios/s7 --agent agent/minimal_agent.py --out run_s7 --quiet
```

`python3 fetch_scenario.py --list` shows the scenarios the platform publishes and `python3 fetch_scenario.py dev-fortnight`
downloads one into `scenarios/dev-fortnight/` (weather files included only for public-weather practice scenarios).
Short scenarios (fewer than 10 nights) automatically get a shorter forecast horizon (`forecast_horizon_days` in the output). Hidden platform
scenarios come from the same generator with undisclosed seeds, sizes and wall clocks; test on several seeds
and at least one long (≥ 90-night) scenario before submitting.

## 5. Edit the agent

0. Simplest path: `agent/my_strategy.py` → `choose_action(candidates, snapshot, memory)` receives the legal candidates
   ranked best-first (dicts with tile_id, program, request_id, region_id, scheduling_class, nominal_exptime_seconds,
   combined_quality, estimated_science_score, terminal_penalty_avoidance, request_policy_value, estimated_total_gain,
   estimated_gain_per_second) and returns one of them (optionally with a `reason`) or `None` to wait; `memory` is a
   dict that persists for the run. Exceptions or illegal picks fall back to the default ranking (logged to stderr).
   This single file can be submitted on its own: the platform wraps it with the rest of the minimal agent.
1. Decision logic lives in `agent/decision_graph.py`: `_prepare` builds the ranked previews, `_model_node`
   optionally asks an LLM to pick among the top-K, `_finalize` validates and falls back to the deterministic
   best. Change the ranking, add lookahead over `night_start` / `weekly` windows, add memory across decisions
   (the `MinimalDecisionAgent` instance persists for the whole run), or filter candidates by request deadlines.
   Never return a `(tile_id, program, request_id)` that is not a current candidate.
2. To add an LLM: `cp agent/.env.example agent/.env`, set `MODEL_PROVIDER` (`openai`, `anthropic`, `deepseek`,
   `xai`, `zai`, `moonshot`, `dashscope`, `minimax`), `MODEL_NAME`, the matching `*_API_KEY`, and
   `MODEL_BASE_URL` for OpenAI-compatible providers; then `python3 -m pip install -r agent/requirements.txt` and
   re-run step 2. `agent.log` prints `minimal-agent provider=<name>`; `deterministic fallback (...)` means the
   configuration is incomplete. The platform allows network access to LLM APIs and installs
   `agent/requirements.txt` into a fresh virtualenv before the run, so keep it to installable package names.
   An LLM call per decision multiplies wall-clock use: cap it with `LLM_TOP_K_CANDIDATES`,
   `LLM_TIMEOUT_SECONDS`, or by only consulting the model at night starts.
3. Rules: only stdout carries protocol lines (log to stderr); every file you import must be inside `agent/`
   (the `challenge/` package is not present on the platform); do not read scenario files or anything outside
   the package; `.env` values reach only your process.
4. Re-run step 2 after every change and compare `total`, `required_missing`, `penalties` and `wall_seconds`.
   `run_output/decision_replay.html` shows each night's choices next to weather and windows.

## 6. Pack and submit

1. `python3 pack_agent.py --agent agent --out my-agent.zip` — validates the entry script and `requirements.txt`,
   excludes caches, includes `.env` (add `--no-env` to leave keys out).
2. Ask the user for the email and password of their platform account (the account must already be on a team)
   and the phase slug (`practice` for public scenarios, `online` for the competition).
3. Agent package, evaluated by the platform on the phase's scenarios (a bare `agent/my_strategy.py` may be sent
   instead of the zip when nothing else changed):
   `python3 sac_submit.py --url https://vdiemcofukuxglqsmlyz.supabase.co --key eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkaWVtY29mdWt1eGdscXNtbHl6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5NTU3OTcsImV4cCI6MjEwNDUzMTc5N30.qMbZ_C3iKq70SdGDYQPPUD0LMBst-xBspZ-zj_gn75w --email EMAIL --password PASSWORD --phase PHASE --kind agent --file my-agent.zip --wait`
4. Results file, scored against a public scenario:
   `python3 sac_submit.py --url https://vdiemcofukuxglqsmlyz.supabase.co --key eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkaWVtY29mdWt1eGdscXNtbHl6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5NTU3OTcsImV4cCI6MjEwNDUzMTc5N30.qMbZ_C3iKq70SdGDYQPPUD0LMBst-xBspZ-zj_gn75w --email EMAIL --password PASSWORD --phase practice --kind results --scenario dev-reference --file run_output/decisions.csv --wait`
5. The command prints the submission id and, with `--wait`, the final status, score and per-scenario results.
   Report them to the user together with the local `total` for comparison.

## 7. Read the score report

`run_output/score_report.json` (and the platform's report) is `score-report-v3`:

* `score.total / base_science / program_bonus / request_reward / report_reward / penalties{...}` — the decomposition.
* `completion.required_missing`, `completion.flexible_shortfall` — what the terminal penalties came from.
* `requests[]` — `completed`, `missed`, `excused_unobservable` or `active_incomplete` per request.
* `reports` — the settlement of your reports: per-(tile, tag) outcomes (+100 / −150), fault correct/misreport counts.
* `actions[]` — every committed decision with its `outcome` (`completed`, `wait`, `weather_interrupted`,
  `geometry_or_night_interrupted`, `unsafe_observation`, `invalid_observe`,
  `outside_tile_window`, `invalid_request_tag`, `unknown_slot`, `stale_decision`) and exposure `segments`
  with the quality used. Interrupted exposures score nothing but carry no penalty; the `invalid_*`,
  `outside_tile_window`, `invalid_request_tag` and `unknown_slot` outcomes cost 100 each,
  `unsafe_observation` 2000. Re-observing a completed tile is legal and scores (the tile banks the max).
* `termination_reason` — `survey_complete`, `global_wallclock_expired`, `agent_error`, `agent_initialization_error`.

`python3 score_decisions.py --scenario DIR --decisions run/decisions.csv` re-scores a decisions file (including
its `report_*` rows); the result
is identical to the runner's report and to the platform's for the same public scenario.

## 8. If the platform reports failure

1. `agent_initialization_error` / `agent exited before responding`: the script crashed on start or on the first
   request; read the agent log linked from the submission page and run step 2 locally with `--show-agent-stderr`.
2. `requirements.txt could not be installed`: a line is not an installable package; `pack_agent.py` rejects the
   common mistakes, otherwise pin known versions.
3. `global_wallclock_expired` with a low score: the agent is too slow; remove per-decision LLM calls or shrink
   the prompt, and profile with `--wallclock 60` locally.
4. `invalid_action` penalties: the response referenced a tile that was not a current candidate, a tile outside
   its availability window, or a bad program / request id; keep the validation in `_finalize`.
