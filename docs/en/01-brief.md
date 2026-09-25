---
title: "Agentic Observer"
section: "AGENT OBSERVER / PARTICIPANT BRIEFING / 01—08"
source: https://create.gosim.org/survey26/platform/brief?lang=en
lang: en
fetched: 2026-09-23
notes:
  - "This page has 9 sections; 1 of them (finals mechanics) is gated by the site switch mechanics_public and was not public at fetch time, so it is not included."
---

# Agentic Observer

**A GOSIM Hackathon for Intelligent Survey Operations**

This participant briefing explains why survey operations need agent observers, what a human leading observer does, how the challenge works across three planning horizons, what you submit, how scoring works, the astronomy you need, and the event schedule.

## 01 The observing plan shapes the science sample

*Why*

A survey telescope maps large areas of sky systematically. It does not spend a whole night on a single object; it visits thousands of tiles across many nights, building up statistically powerful samples of galaxies, quasars, and stars.

Cosmology runs on samples like these: large, uniform, well calibrated. Wide surveys reveal the large-scale structure of the universe, constrain its expansion history, underpin dark energy measurements, and tie galaxy evolution to cosmic time. Spectroscopic surveys add a third dimension — redshift. Position on the sky plus redshift gives you a three-dimensional map of cosmic structure. DESI is one concrete, public example: spectra for large samples of galaxies and quasars, used to map the expanding universe and study dark energy.

But survey operations are hard. Weather changes what a 900-second exposure is worth through seeing, transparency, and sky brightness. Target visibility shifts with airmass and position on the sky. Observing programs prefer different conditions: DARK, BRIGHT, and BACKUP. The footprint has to be covered broadly and evenly for the science to hold up. High-priority tiles, nearly finished tiles, and under-covered regions all compete for the same block of time. One bad tactical choice spends real observing time and changes every plan that follows.

Modern AI systems open a path: an assistant that reads state, calls computational tools, reasons under physical constraints, proposes a plan, monitors quality, and explains every choice. The long-term goal is an observing agent that can genuinely take part in real survey operations. This hackathon narrows that long-term vision into one focused benchmark.

> You build the observer’s logic. We provide the mission definition, tile table, observing constraints, weather scenarios, simulator, and evaluation protocol.

## 02 What a human lead observer actually does

*The Observer*

A seasoned lead observer makes a full set of decisions every 900-second slot: understand the science goal and decide tonight's tactical priority; read seeing, transparency, sky brightness, and the short-term forecast; judge whether tonight suits DARK, BRIGHT, or BACKUP observing; keep the tiles that are visible now, legal, and worth the time; trade off science yield, priority, airmass, footprint balance, and wasted time; finish a tile when completing it beats over-exposure; replan around weather systems, forest-fire smoke, scheduled rocket launches, and changing efficiency; use fast validation signals to calibrate later decisions; and explain why this action was chosen in this slot.

These duties are what the observing agent is taking over. We do not expect an agent to outperform a human immediately. We expect it to be evaluated objectively against a fixed interface, fixed data, and fixed scoring — so it can become a viable component of real survey operations.

### 01 Night strategy

Understand the science goal and decide tonight’s tactical priority.

### 02 Reading the weather

Seeing, transparency, sky brightness, and the short-term forecast.

### 03 Program selection

Judge whether tonight suits DARK, BRIGHT, or BACKUP observing.

### 04 Candidate filtering

Keep the tiles that are visible now, legal, and worth the time.

### 05 Tile ranking

Trade off science yield, priority, airmass, footprint balance, and wasted time.

### 06 Completion management

Finish a tile when the value of completing it beats the cost of over-exposure.

### 07 Replanning

React to forecast disruptions, inserted requests, and changing observing efficiency.

### 08 Quality monitoring

Use fast validation signals to calibrate later decisions.

### 09 Logging the reason

Explain why the observer chose this action in this slot.

## 03 The Survey Mission Card and the challenge loop

*Challenge Format*

Every challenge instance starts from a Survey Mission Card: the science goal, footprint area, time budget, telescope site, available observing programs, target classes, observing constraints, and scoring rules — everything a round holds you to. On this platform the mission card takes the form of published evaluation scenarios: tile catalogue, observing constraints, weather and forecasts, ad-hoc requests, simulator, and scoring configuration. The public development scenarios (dev-reference, dev-fortnight) are on the Resources page; competition scenarios are provided by the platform when the competition starts.

Every participant faces the same evaluation scenario: the overall survey plan, per-night seeing and weather replay, ad-hoc observing requests, predictable mid-term disruptions, simulator, and scoring rule are fixed. The agent receives the current state and returns the next observing action every 900 seconds.

### The decision loop

#### Fixed inputs

- Science goal
- Footprint tiles
- Weather replay

#### Agent state

- Weather + forecast
- Survey progress
- Available tiles

#### Reasoning

- Rank
- Explain
- Decide

#### Action

- Observe
- or wait

#### Metrics

- Science yield
- Uniformity + waste
- Violations

## 04 Plan across the survey, the week, and the next 900 seconds

*Planning Horizons*

Long term — the organizers provide the overall survey plan in the mission card, while the agent manages that plan in flight. Final scoring includes survey completeness and completion quality.

Middle term — the agent builds a flexible month, week, and day plan from progress and forecast conditions, absorbing weather systems, forest-fire smoke, scheduled launches, and newly inserted observing requests. This layer may be enabled as mission cards evolve.

Short term — every 900-second slot, the agent makes an immediate tactical choice using the middle-term plan and real-time seeing and transparency. This is the focus of the scored benchmark.

## 05 Build a reproducibly runnable observer project

*Participant Job*

Running locally is language-agnostic: run your agent and submit the generated decisions.csv — accepted in both phases. An agent package runs on the platform with a Python 3.12 entry script (minimal_agent.py, agent.py or main.py) exchanging JSON messages over participant-agent-protocol-v1/v2; the authoritative contract is on the Docs page.

The platform runs your agent against the evaluation scenarios and scores it with the same frozen scorer.

Download the starter kit, study the public development scenarios and visual reports, prototype against the protocol, evaluate on development scenarios, inspect the replays; the practice phase accepts decisions.csv, the online competition accepts agent packages.

The platform keeps the full record of every evaluation — the decisions file, the run log, and a slot-by-slot replay — for organizer review, and for you to inspect why each step went the way it did.

```python
return {
    "action": "observe",
    "tile_id": "T00037",
    "program": "DARK",
    "request_id": "",
    "reason": "highest preview estimate",
}
```

## 06 How scoring and the simulated environment work

*Scoring*

What a completed exposure earns depends on the atmosphere and the moon at the time; matching the night's programme and answering a request add more on top. Six things cost you: unsafe observations, invalid actions, avoidable waiting, unfinished must-do patches (REQUIRED), optional shortfall per region (FLEXIBLE), and expired requests. A good agent keeps trading immediate output against coverage, weather risk, request deadlines and the time budget.

Evaluation follows open-science principles: fixed data and fixed scoring let every submitted observation result be evaluated consistently; readable result artifacts make outcomes inspectable; separate development and final-evaluation weather compare generalization instead of tuning to a known scenario.

Visualization turns completed tile fractions into an observed-target map — a 3D spatial sample and a 2D butterfly plot of sky position plus simulated redshift, in the style DESI uses — so different strategies produce visibly different maps.

The same observer interface can support future mission cards for real survey-log replay, z > 5 quasar planning, LBG planning, and target-of-opportunity inserts; the community can propose new fixed datasets for follow-up tracks.

```text
total = base_science + program_bonus + request_reward
      - unsafe_observation - invalid_action - avoidable_wait
      - required_miss - flexible_shortfall - request_miss
```

## 07 A little astronomy is enough

*Glossary*

You do not need an astronomy background to read the state dictionary and reason about it. A Tile is a patch of sky observed in one pointing; the Footprint is the full survey area; Targets are the object counts inside each tile. High-priority tiles carry extra scientific weight, nearly complete tiles are one exposure from completion, and under-covered regions are lagging behind the rest of the footprint.

Seeing describes atmospheric blur — lower is better. Transparency describes how clear the atmosphere is — higher is better. Sky brightness is background glow; darker is better for faint targets. Airmass measures how much atmosphere the light crosses and changes with sky position — lower is better. DARK, BRIGHT, and BACKUP are programs matched to dark, bright, and poor conditions. donefrac is tile completion: 0 means just started, 1 means finished.

The decision snapshot contains current_site_weather (observability, seeing, transparency, sky quality, instrument efficiency, active events), candidate_tiles (the legal options right now, each with geometry, airmass, lunar factor, effective weather, science value and window), active_requests, the forecasts issued so far, and progress (completed tiles, FLEXIBLE completions per region).

```python
return {
    "action": "observe",
    "tile_id": "T00037",
    "program": "DARK",
    "request_id": "",
    "reason": "highest preview estimate",
}
```

## 08 Three stages from training to awards

*Timeline*

The event has three stages: online training from October 1–4, online competition from October 5–7, and Awards Day at GOSIM Shenzhen on October 17.

Training introduces CosmosBench, the shared simulator and the participant protocol (details will be announced and emailed). During the online competition, registered teams submit agent packages; CosmosBench powers evaluation and live standings.

### 01 Online Training

Oct 1–4 · Online

**Survey task**: Learn CosmosBench, the shared simulator, mission cards, and the observer interface, then prepare with public development scenarios.

**Who participates**: Open worldwide to individuals and teams.

### 02 Online Competition

Oct 5–7 · Online

**Survey task**: Submit an agent package; the platform runs it, scores it, and publishes the standings.

**Who participates**: All registered teams.

### 03 Awards Day

Oct 17 · GOSIM Shenzhen

**Survey task**: Final results are announced and prizes are presented.

**Who participates**: Winning teams and invited participants.

---

**Next: build your observer.**

[Register now](https://create.gosim.org/survey26/platform/register)
