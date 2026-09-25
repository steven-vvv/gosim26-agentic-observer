---
source: https://create.gosim.org/survey26/platform/resources?lang=en
fetched_utc: 2026-09-25T16:22:29+00:00
method: rendered main; all FAQ entries expanded
---

Resources

# Starter kit and data

Everything needed to run and score an agent locally: the starter kit and, per scenario, the configuration and reference files the platform uses.

1

## Step 1 · Grab the kit and run

The only download you need: unzip, double-click, a score in seconds.

01

### Starter kit (zip)

agent/ (minimal agent, optional LLM via .env), challenge/ (workflow, scorer, preview, simulators, replay), scenarios/dev-reference, local\_runner.py, score\_decisions.py, make\_scenario.py, pack\_agent.py, sac\_submit.py, SKILL.md.

[Download ↓](/survey26/platform/downloads/agent-observer-starter-kit.zip)

2

## Step 2 · Write your own strategy

Editing one function gets you on the board; these are the reference and the manual.

02

### Reference implementation (GitHub)

A complete, runnable example strategy, reference\_strategy.py: public snapshot fields and the standard library only — copy it to my\_strategy.py and it runs. It matches the greedy baseline; use it as a starting point.

[View →](https://github.com/gosimfoundation/hackathon-survey26/tree/main/challenge/participant_agent)

03

### scoring\_core.py

The frozen challenge-score-v3 scorer (with contracts.py and score\_config.json next to it). Standard library only, Python 3.12.

[Download ↓](/survey26/platform/downloads/scoring_core.py)

04

### SKILL.md

One-file instructions an AI coding agent can follow to build, test and submit.

[View →](/survey26/platform/skill.md)

05

### Documentation

Data formats with exact columns, the three protocol envelopes, the scoring formula and every penalty, packaging rules and limits.

[View →](/survey26/platform/docs)

3

## Step 3 · Fetch data when you need it

Public scenario files are listed below.

## Scenarios

Download the public configuration and data files for the current competition below.

### dev-fortnight

Development fortnight (14 nights, seed 2026)

A short public scenario for quick iteration: 14 nights from 2026-10-05, all files public.

weather public

Time budget
:   30 min

nights
:   14

slots
:   590

tiles
:   64

targets
:   16012

requests
:   1

Configuration

Seven JSON files: scenario, calendar, tile, weather, request, workflow and score configuration. Published in full in the initialize message as well.

scenario\_config.json ↓calendar\_config.json ↓tile\_config.json ↓weather\_config.json ↓request\_config.json ↓workflow\_config.json ↓score\_config.json ↓

Calendar · catalogue · requests

night\_calendar.csv and slots.csv (time axis), tiles.csv and targets.csv (catalogue and science weights), tile\_windows.csv (sample windows), observation\_requests.csv and observation\_request\_tiles.csv, manifest and metadata.

night\_calendar.csv ↓slots.csv ↓tiles.csv ↓targets.csv ↓tile\_windows.csv ↓observation\_requests.csv ↓observation\_request\_tiles.csv ↓scenario\_manifest.json ↓calendar\_metadata.json ↓catalog\_metadata.json ↓observation\_request\_metadata.json ↓

Weather truth

weather.csv: site baseline per slot. Public on practice scenarios so a local score\_decisions.py run reproduces the platform report.

weather.csv ↓weather\_metadata.json ↓

Forecasts

weather\_forecasts.csv: uncertain, daily-revised forecasts. The platform only shows revisions issued at or before the current cursor.

weather\_forecasts.csv ↓

Directional events

weather\_events.csv: the directional disruption truth behind active\_event\_ids. Hidden on competition scenarios.

weather\_events.csv ↓

contract: challenge-score-v3

### dev-reference

Development reference (180 nights, seed 20260909)

The published example3 reference scenario. Everything public, including weather events, so local scoring reproduces the platform.

weather public

Time budget
:   2 h

nights
:   180

slots
:   7928

tiles
:   64

targets
:   15833

requests
:   18

Configuration

Seven JSON files: scenario, calendar, tile, weather, request, workflow and score configuration. Published in full in the initialize message as well.

scenario\_config.json ↓calendar\_config.json ↓tile\_config.json ↓weather\_config.json ↓request\_config.json ↓workflow\_config.json ↓score\_config.json ↓

Calendar · catalogue · requests

night\_calendar.csv and slots.csv (time axis), tiles.csv and targets.csv (catalogue and science weights), tile\_windows.csv (sample windows), observation\_requests.csv and observation\_request\_tiles.csv, manifest and metadata.

night\_calendar.csv ↓slots.csv ↓tiles.csv ↓targets.csv ↓tile\_windows.csv ↓observation\_requests.csv ↓observation\_request\_tiles.csv ↓scenario\_manifest.json ↓calendar\_metadata.json ↓catalog\_metadata.json ↓observation\_request\_metadata.json ↓

Weather truth

weather.csv: site baseline per slot. Public on practice scenarios so a local score\_decisions.py run reproduces the platform report.

weather.csv ↓weather\_metadata.json ↓

Forecasts

weather\_forecasts.csv: uncertain, daily-revised forecasts. The platform only shows revisions issued at or before the current cursor.

weather\_forecasts.csv ↓

Directional events

weather\_events.csv: the directional disruption truth behind active\_event\_ids. Hidden on competition scenarios.

weather\_events.csv ↓

contract: challenge-score-v3

## Command line

```
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
# submit the decisions.csv your local run produced
python3 sac_submit.py --phase practice --kind results --scenario dev-reference --file run_output/decisions.csv --wait
```