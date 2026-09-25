---
source: https://create.gosim.org/survey26/platform/brief?lang=en
fetched_utc: 2026-09-25T16:22:29+00:00
method: rendered main; all FAQ entries expanded
---

![](/survey26/platform/assets/cosmos-dome-D1P_eEvz.jpg)

[Back to home](/survey26/platform/)

AGENT OBSERVER / PARTICIPANT BRIEFING / 01—08

# Agentic Observer

A GOSIM Hackathon for Intelligent Survey Operations

This participant briefing explains why survey operations need agent observers, what a human leading observer does, how the challenge works across three planning horizons, what you submit, how scoring works, the astronomy you need, and the event schedule.

01

Why

## The observing plan shapes the science sample

A survey telescope maps large areas of sky systematically. It does not spend a whole night on a single object; it visits thousands of tiles across many nights, building up statistically powerful samples of galaxies, quasars, and stars.

Cosmology runs on samples like these: large, uniform, well calibrated. Wide surveys reveal the large-scale structure of the universe, constrain its expansion history, underpin dark energy measurements, and tie galaxy evolution to cosmic time. Spectroscopic surveys add a third dimension — redshift. Position on the sky plus redshift gives you a three-dimensional map of cosmic structure. DESI is one concrete, public example: spectra for large samples of galaxies and quasars, used to map the expanding universe and study dark energy.

But survey operations are hard. Weather changes what a 900-second exposure is worth through seeing, transparency, and sky brightness. Target visibility shifts with airmass and position on the sky. Observing programs prefer different conditions: DARK, BRIGHT, and BACKUP. The footprint has to be covered broadly and evenly for the science to hold up. High-priority tiles, nearly finished tiles, and under-covered regions all compete for the same block of time. One bad tactical choice spends real observing time and changes every plan that follows.

Modern AI systems open a path: an assistant that reads state, calls computational tools, reasons under physical constraints, proposes a plan, monitors quality, and explains every choice. The long-term goal is an observing agent that can genuinely take part in real survey operations. This hackathon narrows that long-term vision into one focused benchmark.

> You build the observer’s logic. We provide the mission definition, tile table, observing constraints, weather scenarios, simulator, and evaluation protocol.

02

The Observer

## What a human lead observer actually does

A seasoned lead observer makes a full set of decisions every 900-second slot: understand the science goal and decide tonight's tactical priority; read seeing, transparency, sky brightness, and the short-term forecast; judge whether tonight suits DARK, BRIGHT, or BACKUP observing; keep the tiles that are visible now, legal, and worth the time; trade off science yield, priority, airmass, footprint balance, and wasted time; finish a tile when completing it beats over-exposure; replan around weather systems, forest-fire smoke, scheduled rocket launches, and changing efficiency; use fast validation signals to calibrate later decisions; and explain why this action was chosen in this slot.

These duties are what the observing agent is taking over. We do not expect an agent to outperform a human immediately. We expect it to be evaluated objectively against a fixed interface, fixed data, and fixed scoring — so it can become a viable component of real survey operations.

01

### Night strategy

Understand the science goal and decide tonight’s tactical priority.

02

### Reading the weather

Seeing, transparency, sky brightness, and the short-term forecast.

03

### Program selection

Judge whether tonight suits DARK, BRIGHT, or BACKUP observing.

04

### Candidate filtering

Keep the tiles that are visible now, legal, and worth the time.

05

### Tile ranking

Trade off science yield, priority, airmass, footprint balance, and wasted time.

06

### Completion management

Finish a tile when the value of completing it beats the cost of over-exposure.

07

### Replanning

React to forecast disruptions, inserted requests, and changing observing efficiency.

08

### Quality monitoring

Use fast validation signals to calibrate later decisions.

09

### Logging the reason

Explain why the observer chose this action in this slot.

03

Challenge Format

## The Survey Mission Card and the challenge loop

The mission card defines the science goal, tiles, observing constraints, weather, requests and scoring. Download the published scenarios on Resources.

Every participant faces the same evaluation scenario: the overall survey plan, per-night seeing and weather replay, ad-hoc observing requests, predictable mid-term disruptions, simulator, and scoring rule are fixed. The agent receives the current state and returns the next observing action every 900 seconds.

### The decision loop

#### Fixed inputs

* Science goal
* Footprint tiles
* Weather replay

#### Agent state

* Weather + forecast
* Survey progress
* Available tiles

#### Reasoning

* Rank
* Explain
* Decide

#### Action

* Observe
* or wait

#### Metrics

* Science yield
* Uniformity + waste
* Violations

04

Planning Horizons

## Plan across the survey, the week, and the next 900 seconds

Long term — the organizers provide the overall survey plan in the mission card, while the agent manages that plan in flight. Final scoring includes survey completeness and completion quality.

Middle term — the agent builds a flexible month, week, and day plan from progress and forecast conditions, absorbing weather systems, forest-fire smoke, scheduled launches, and newly inserted observing requests. This layer may be enabled as mission cards evolve.

Short term — every 900-second slot, the agent makes an immediate tactical choice using the middle-term plan and real-time seeing and transparency. This is the focus of the scored benchmark.

05

Participant Job

## Build a reproducibly runnable observer project

Use any algorithm to produce decisions.csv. Submit that file for evaluation.

Run the public scenario locally. The platform scores your submitted decisions using the same published scorer.

Download the starter kit, run public scenarios, study the replay, and improve your decisions.

The platform keeps the full record of every evaluation — the decisions file, the run log, and a slot-by-slot replay — for organizer review, and for you to inspect why each step went the way it did.

```
return {
    "action": "observe",
    "tile_id": "T00037",
    "program": "DARK",
    "request_id": "",
    "reason": "highest preview estimate",
}
```

06

Scoring

## How scoring and the simulated environment work

What a completed exposure earns depends on the atmosphere and the moon at the time; matching the night's programme and answering a request add more on top. Six things cost you: unsafe observations, invalid actions, avoidable waiting, unfinished must-do patches (REQUIRED), optional shortfall per region (FLEXIBLE), and expired requests. A good agent keeps trading immediate output against coverage, weather risk, request deadlines and the time budget.

The published data and scoring rules let you reproduce your results and inspect each observation.

Visualization turns completed tile fractions into an observed-target map — a 3D spatial sample and a 2D butterfly plot of sky position plus simulated redshift, in the style DESI uses — so different strategies produce visibly different maps.

The same observer interface can support future mission cards for real survey-log replay, z > 5 quasar planning, LBG planning, and target-of-opportunity inserts; the community can propose new fixed datasets for follow-up tracks.

```
total = base_science + program_bonus + request_reward
      - unsafe_observation - invalid_action - avoidable_wait
      - required_miss - flexible_shortfall - request_miss
```

07

Glossary

## A little astronomy is enough

You do not need an astronomy background to read the state dictionary and reason about it. A Tile is a patch of sky observed in one pointing; the Footprint is the full survey area; Targets are the object counts inside each tile. High-priority tiles carry extra scientific weight, nearly complete tiles are one exposure from completion, and under-covered regions are lagging behind the rest of the footprint.

Seeing describes atmospheric blur — lower is better. Transparency describes how clear the atmosphere is — higher is better. Sky brightness is background glow; darker is better for faint targets. Airmass measures how much atmosphere the light crosses and changes with sky position — lower is better. DARK, BRIGHT, and BACKUP are programs matched to dark, bright, and poor conditions. donefrac is tile completion: 0 means just started, 1 means finished.

The decision snapshot contains current\_site\_weather (observability, seeing, transparency, sky quality, instrument efficiency, active events), candidate\_tiles (the legal options right now, each with geometry, airmass, lunar factor, effective weather, science value and window), active\_requests, the forecasts issued so far, and progress (completed tiles, FLEXIBLE completions per region).

```
return {
    "action": "observe",
    "tile_id": "T00037",
    "program": "DARK",
    "request_id": "",
    "reason": "highest preview estimate",
}
```

08

Timeline

## Take part online

The Playground is available online. Registration, teams, submissions and scores are managed on this website.

The platform selects the current competition for you. Use Submit whenever you are ready.

Next: build your observer.

[Register now →](/survey26/platform/register)