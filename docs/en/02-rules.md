---
title: "Competition rules and scoring"
section: "Rules"
source: https://create.gosim.org/survey26/platform/rules?lang=en
lang: en
fetched: 2026-09-23
notes:
  - "The \"Phase configuration (live)\" table comes from the platform database and reflects its state at fetch time."
  - "Section 1 says \"A team has 1 to 8 members\"; the Chinese rules, the FAQ and the team form say 1–3."
  - "Section 3 item 2 says the platform runs every scenario of the phase through participant-agent-protocol-v2; the Chinese rules say practice scenarios stay on participant-agent-protocol-v1 and the anomaly mechanics (v2) apply only to competition scenarios, as does section 8 of the Docs page in both languages."
---

# Competition rules and scoring

Rules version 1.0 · 2026-09-09

## Phase configuration (live)

| Phase | Status | UTC | Results file | Agent run | submissions/day | board |
| --- | --- | --- | --- | --- | --- | --- |
| Online Competition | upcoming | 2026-10-04 16:00 → 2026-10-07 15:59 | Yes | Yes | 10 | live |
| Playground | open | — → — | Yes | Yes | 50 | live |

## 1. Eligibility and teams

1. Participation is open worldwide to individuals and teams. One account per person.
2. A team has 1 to 8 members. A person belongs to at most one team. Submissions are made on behalf of a team.
3. Organizers, evaluation-platform maintainers, and their immediate collaborators may participate in the Playground phase but are excluded from awards. Their teams are marked hidden on the boards.
4. Team names and content must follow the code of conduct (section 8).

## 2. Phases

| Phase | Dates (UTC) | Submissions | Board |
|---|---|---|---|
| Practice | from registration until Awards Day | decisions.csv or agent package, 50 per team per day | informational |
| Online Competition | 2026-10-04 16:00 to 2026-10-07 15:59 (Oct 5–7 in UTC+8) | decisions.csv or agent package, 10 per team per day | decides the awards |
| Awards Day | 2026-10-17 at GOSIM Shenzhen | none | final results announced |

The live phase configuration table above this document is authoritative if the two differ.

## 3. What you submit

1. **Results file.** A `decisions.csv` with the columns `decision_id, slot_id, action, tile_id, program, request_id, reason`, produced by running your agent locally. Accepted in both phases and scored immediately by the frozen scorer. Competition weather is not published; the platform scores your decision sequence against the weather truth it holds.
2. **Agent package.** A `.zip` whose root (or single top-level folder) contains the entry script `minimal_agent.py`, `agent.py` or `main.py`, optionally `requirements.txt` and `.env`, and any other files the script imports. A bare `.py` file is accepted when it needs nothing else. The platform runs the package against every scenario of the phase through `participant-agent-protocol-v2` and scores the committed decisions. The competition weather, forecasts and events are never downloadable.
3. Files are limited to 20 MB. Archives are limited to 2,000 files and 50 MB uncompressed. Symbolic links and paths outside the archive root are rejected.

## 4. Platform runs

1. Runtime: Python 3.12 in a sandbox with the package directory as working directory. `requirements.txt` is installed into a per-run virtual environment before the clock starts; `.env` is loaded into the agent's environment only. Network access is allowed so that agents may call model APIs with their own keys or the sponsor credits.
2. Timing: one global wall clock per scenario (`global_wallclock_seconds`, published per scenario) that starts after the initial publication and covers every decision; there is no per-decision timeout. Initialization has a separate 30-second budget. When the clock expires the process is terminated and the actions committed so far are scored, including the terminal penalties.
3. A process that exits, answers with anything other than a `decision_response` whose `action` is `observe` or `wait`, or fails to initialize ends the run with `agent_error` / `agent_initialization_error`. The committed actions are still scored; a run with no committed actions carries the full terminal penalties.
4. The agent receives only the published snapshots: the current slot's weather, the candidate tiles with their effective conditions, the issued forecasts and requests, and its own progress. It never receives future weather or the event list.
5. Attempts to read other teams' data, to escape the sandbox, to reach the scenario files, to tamper with the scorer or with score files, or to exhaust platform resources deliberately lead to disqualification.

## 5. Scoring

The score is computed by the published `scoring_core.py` (schema `challenge-score-v3`) with the constants in `config/score_config.json`. In summary:

1. Time is a calendar of 900-second slots over real nights. `observe` runs from the cursor for the tile's `nominal_exptime_seconds`, may cross slots and is split into segments; `wait` consumes the rest of the current slot.
2. For each segment of a **completed** exposure, at the segment midpoint:
   `A = min(instrument_efficiency · transparency · sky_quality / (seeing_arcsec · airmass), 3.0)`, `combined = A · lunar_quality_factor`,
   `base = V_tile · (segment_seconds / nominal_exptime_seconds) · combined`, where `V_tile` is the sum of `science_weight` over the tile's targets;
   `bonus = base · B[program]` with `B = {DARK 0.25, BRIGHT 0.15, BACKUP 0.08}`, paid only when the decision program equals the band implied by `combined` (DARK ≥ 0.65, BRIGHT ≥ 0.40, else BACKUP).
3. `total = base_science + program_bonus + request_reward + report_reward + coverage_bonus − unsafe_observation − invalid_action − avoidable_wait − required_miss − flexible_shortfall − request_miss − fault_misreport − wrong_tag_report`, with 2000 per unsafe observation (observing into a closed dome), 100 per invalid action (unknown tile/slot/program, outside the availability window, bad request tag, exposure that cannot finish before the tile sets or the night ends, stale decision), 0.001 per avoidable waiting second, 1000 per uncompleted REQUIRED tile, 100 per FLEXIBLE tile below the quota of 4 per region, and 190 per required tile of an expired request (140 rewarded per required tile when completed).
4. **Repeat observations and anomaly reports**: re-observation is legal — a tile banks the maximum over its observations (a worse repeat never lowers it), and completion still banks on the first legal observation. Scenarios hide per-tile tags (nova ×1.5, reddening ×0.8, applied silently by the scorer) and region-scoped instrument faults (a sharp efficiency collapse that is never forecast). A `decision_response` may carry a `reports` array: tag reports settle at final scoring, first report per (tile, tag), +100 correct / −150 wrong; a correct fault report publishes the fault status one simulated day later and completes the repair after two, while a misreport gets one free allowance per correct report and costs 100 each beyond it. See the `reporting` / `anomaly_tags` / `fault_response` sections of `score_config.json`.
5. **Coverage evenness** (`coverage_bonus`): `coverage_bonus = W · base_science · E`, where `E` is how evenly the finished tiles are spread over the regions (Jain's fairness index, `(Σx)² / (n·Σx²)`: 1.0 when all eight regions get the same number, 1/8 when one region takes everything). The weight `W` lives in each scenario's `config/score_config.json`: **0 for the practice scenarios** (so local scores are unaffected) and **0.35 for the competition scenarios**. A wide survey needs even coverage to support its statistics, so crowding the easy regions now costs something.
6. Only completed exposures score. An exposure interrupted by closed weather earns nothing and is not penalised; an exposure interrupted by geometry or the end of the night earns nothing and is an invalid action.
7. Terminal penalties are applied to every run, including runs cut short by the wall clock or an agent error. An expired request with fewer feasible opportunities than required tiles is excused.
8. Completion (completed tiles ÷ tiles) and the FLEXIBLE shortfall per region are reported on the board; they are part of the score through the terminal penalties.
9. When a phase has several scenarios, a submission's score is the arithmetic mean of its per-scenario scores. A submission counts only if every scenario scored.

## 6. Ranking, ties, and verification

1. A team's best scored, non-excluded submission in the phase counts. Ranking is by score, descending; on an exact tie the earlier submission ranks first.
2. The Online Competition board is live. Organizers may freeze the board during the final hours and publish the final standings after verification.
3. Before awards are confirmed, organizers rerun the top submissions and may request the agent package and a short description of the approach from the top teams. Results that cannot be reproduced on the platform are removed. Because platform runs are wall-clock bound, reruns use the same wall clock and the same scenarios; small differences in the number of committed actions are expected and are not grounds for appeal.
4. Organizers may re-score submissions if a scorer defect is found. Any change to the scorer or the constants is announced with a version number and applies to every submission of the phase. The current constants are provisional organizer calibration values until the online competition opens.

## 7. Awards

| Award | Prize | Count |
|---|---|---|
| First Prize | $2,000 | 1 |
| Second Prize | $1,000 | 2 |
| Third Prize | $500 | 3 |

Amounts are gross. Winning teams are invited to Awards Day at GOSIM Shenzhen on October 17, 2026; attendance is not required to receive a prize.

## 8. Code of conduct

1. Be respectful. Harassment, discrimination, and abusive content in team names, notes, or agent output are not tolerated.
2. Do not share accounts, do not submit another team's work, and do not create multiple teams to multiply the daily limit.
3. Report platform defects to the organizers instead of exploiting them. Reports of scorer or sandbox issues are welcome and credited.
4. Decisions of the organizers on eligibility, disqualification, and awards are final.

## 9. Data and privacy

1. Registration data (name, email, affiliation, GitHub handle) is used only to run the event and to contact winners.
2. Uploaded packages, `.env` files, run logs, and score reports are stored on the platform until 90 days after Awards Day and are visible to the submitting team and to organizers. Values from `.env` are never written to logs.
3. Team names, scores, and ranks are public.

Contact: hackathon@gosim.org
