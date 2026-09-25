---
title: "Starter kit and data"
section: "Resources"
source: https://create.gosim.org/survey26/platform/resources?lang=en
lang: en
fetched: 2026-09-23
notes:
  - "The scenario list comes from the platform database and reflects its state at fetch time; scenario files are downloaded via page buttons (JS), so only file names are listed here — use fetch_scenario.py from the starter kit."
---

# Starter kit and data

Everything needed to run and score an agent locally: the starter kit and, per scenario, the configuration and reference files the platform uses.

## Step 1 · Grab the kit and run

The only download you need: unzip, double-click, a score in seconds.

### 01 Starter kit (zip)

agent/ (minimal agent, optional LLM via .env), challenge/ (workflow, scorer, preview, simulators, replay), scenarios/dev-reference, local_runner.py, score_decisions.py, make_scenario.py, pack_agent.py, sac_submit.py, SKILL.md.

[Download](https://create.gosim.org/survey26/platform/downloads/agent-observer-starter-kit.zip)

## Step 2 · Write your own strategy

Editing one function gets you on the board; these are the reference and the manual.

### 02 Reference implementation (GitHub)

A complete, runnable example strategy, reference_strategy.py: public snapshot fields and the standard library only — copy it to my_strategy.py and it runs. It matches the greedy baseline; use it as a starting point.

[View](https://github.com/gosimfoundation/hackathon-survey26/tree/main/challenge/participant_agent)

### 03 scoring_core.py

The frozen challenge-score-v3 scorer (with contracts.py and score_config.json next to it). Standard library only, Python 3.12.

[Download](https://create.gosim.org/survey26/platform/downloads/scoring_core.py)

### 04 SKILL.md

One-file instructions an AI coding agent can follow to build, test and submit.

[View](https://create.gosim.org/survey26/platform/skill.md)

### 05 Documentation

Data formats with exact columns, the three protocol envelopes, the scoring formula and every penalty, packaging rules and limits.

[View](https://create.gosim.org/survey26/platform/docs)

## Step 3 · Fetch data when you need it

What each scenario publishes or hides, at a glance; competition material is never public.

## Scenarios

A scenario is a directory: config/\*.json plus outputs/reference/\*.csv. Configuration, calendar, catalogue and requests are always downloadable; weather, forecasts and events follow the scenario's visibility flags. Hidden files are shown crossed out. Competition seeds are not published, so hidden weather cannot be reconstructed from the public configuration.

### dev-fortnight

Development fortnight (14 nights, seed 2026)

A short public scenario for quick iteration: 14 nights from 2026-10-05, all files public.

**weather public**

| Time budget | nights | slots | tiles | targets | requests |
|---|---|---|---|---|---|
| 30 min | 14 | 590 | 64 | 16012 | 1 |

#### Configuration

Seven JSON files: scenario, calendar, tile, weather, request, workflow and score configuration. Published in full in the initialize message as well.

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### Calendar · catalogue · requests

night_calendar.csv and slots.csv (time axis), tiles.csv and targets.csv (catalogue and science weights), tile_windows.csv (sample windows), observation_requests.csv and observation_request_tiles.csv, manifest and metadata.

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### Weather truth

weather.csv: site baseline per slot. Public on practice scenarios so a local score_decisions.py run reproduces the platform report.

`weather.csv` · `weather_metadata.json`

#### Forecasts

weather_forecasts.csv: uncertain, daily-revised forecasts. The platform only shows revisions issued at or before the current cursor.

`weather_forecasts.csv`

#### Directional events

weather_events.csv: the directional disruption truth behind active_event_ids. Hidden on competition scenarios.

`weather_events.csv`

contract: challenge-score-v3

### dev-reference

Development reference (180 nights, seed 20260909)

The published example3 reference scenario. Everything public, including weather events, so local scoring reproduces the platform.

**weather public**

| Time budget | nights | slots | tiles | targets | requests |
|---|---|---|---|---|---|
| 2 h | 180 | 7928 | 64 | 15833 | 18 |

#### Configuration

Seven JSON files: scenario, calendar, tile, weather, request, workflow and score configuration. Published in full in the initialize message as well.

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### Calendar · catalogue · requests

night_calendar.csv and slots.csv (time axis), tiles.csv and targets.csv (catalogue and science weights), tile_windows.csv (sample windows), observation_requests.csv and observation_request_tiles.csv, manifest and metadata.

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### Weather truth

weather.csv: site baseline per slot. Public on practice scenarios so a local score_decisions.py run reproduces the platform report.

`weather.csv` · `weather_metadata.json`

#### Forecasts

weather_forecasts.csv: uncertain, daily-revised forecasts. The platform only shows revisions issued at or before the current cursor.

`weather_forecasts.csv`

#### Directional events

weather_events.csv: the directional disruption truth behind active_event_ids. Hidden on competition scenarios.

`weather_events.csv`

contract: challenge-score-v3

### eval-a

Competition scenario A (hidden weather)

Online competition replay A: 30 nights from 2026-10-05, 1600 tiles. Weather, forecasts and events are hidden; coverage evenness is scored.

**weather hidden**

| Time budget | nights | slots | tiles | targets | requests |
|---|---|---|---|---|---|
| 1 h | 30 | 1289 | 1600 | 403975 | 2 |

#### Configuration

Seven JSON files: scenario, calendar, tile, weather, request, workflow and score configuration. Published in full in the initialize message as well.

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### Calendar · catalogue · requests

night_calendar.csv and slots.csv (time axis), tiles.csv and targets.csv (catalogue and science weights), tile_windows.csv (sample windows), observation_requests.csv and observation_request_tiles.csv, manifest and metadata.

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### Weather truth

*hidden (platform runs only)*

weather.csv: site baseline per slot. Public on practice scenarios so a local score_decisions.py run reproduces the platform report.

~~`weather.csv`~~ · ~~`weather_metadata.json`~~

#### Forecasts

*hidden (platform runs only)*

weather_forecasts.csv: uncertain, daily-revised forecasts. The platform only shows revisions issued at or before the current cursor.

~~`weather_forecasts.csv`~~

#### Directional events

*hidden (platform runs only)*

weather_events.csv: the directional disruption truth behind active_event_ids. Hidden on competition scenarios.

~~`weather_events.csv`~~

contract: challenge-score-v3 · seed not published (prevents reconstructing hidden weather)

### eval-b

Competition scenario B (hidden weather)

Online competition replay B: 30 nights from 2026-11-01, 1600 tiles, different seed. Weather, forecasts and events are hidden; coverage evenness is scored.

**weather hidden**

| Time budget | nights | slots | tiles | targets | requests |
|---|---|---|---|---|---|
| 1 h | 30 | 1365 | 1600 | 404120 | 1 |

#### Configuration

Seven JSON files: scenario, calendar, tile, weather, request, workflow and score configuration. Published in full in the initialize message as well.

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### Calendar · catalogue · requests

night_calendar.csv and slots.csv (time axis), tiles.csv and targets.csv (catalogue and science weights), tile_windows.csv (sample windows), observation_requests.csv and observation_request_tiles.csv, manifest and metadata.

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### Weather truth

*hidden (platform runs only)*

weather.csv: site baseline per slot. Public on practice scenarios so a local score_decisions.py run reproduces the platform report.

~~`weather.csv`~~ · ~~`weather_metadata.json`~~

#### Forecasts

*hidden (platform runs only)*

weather_forecasts.csv: uncertain, daily-revised forecasts. The platform only shows revisions issued at or before the current cursor.

~~`weather_forecasts.csv`~~

#### Directional events

*hidden (platform runs only)*

weather_events.csv: the directional disruption truth behind active_event_ids. Hidden on competition scenarios.

~~`weather_events.csv`~~

contract: challenge-score-v3 · seed not published (prevents reconstructing hidden weather)

## Command line

```bash
# environment for sac_submit.py (also printed in SKILL.md inside the kit)
export SAC_URL=https://vdiemcofukuxglqsmlyz.supabase.co
export SAC_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkaWVtY29mdWt1eGdscXNtbHl6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5NTU3OTcsImV4cCI6MjEwNDUzMTc5N30.qMbZ_C3iKq70SdGDYQPPUD0LMBst-xBspZ-zj_gn75w
export SAC_EMAIL=you@example.org SAC_PASSWORD='...'

unzip agent-observer-starter-kit.zip && cd agent-observer-starter-kit
# run the minimal agent locally on the bundled public scenario, then re-score the trace (details: SKILL.md)
python3 local_runner.py --scenario scenarios/dev-reference --agent agent/minimal_agent.py --wallclock 600 --out run_output
python3 score_decisions.py --scenario scenarios/dev-reference --decisions run_output/decisions.csv
python3 make_scenario.py --out scenarios/mine --seed 7 --days 30      # more practice scenarios
python3 fetch_scenario.py dev-fortnight                                # any published scenario -> scenarios/dev-fortnight/
python3 pack_agent.py --agent agent --out my_agent.zip                 # the package you submit
# submit: a results file for a public-weather practice scenario, or the agent package (zip) for platform runs
python3 sac_submit.py --phase practice --kind results --scenario dev-reference --file run_output/decisions.csv --wait
python3 sac_submit.py --phase online --kind agent --file my_agent.zip --wait
```
