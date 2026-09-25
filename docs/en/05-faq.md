---
title: "Questions before you register"
section: "FAQ"
source: https://create.gosim.org/survey26/platform/faq?lang=en
lang: en
fetched: 2026-09-23
notes:
  - "There are 17 Q&As; 2 of them (finals anomaly mechanics) are gated by the site switch mechanics_public and were not public at fetch time, so they are not included."
---

# Questions before you register

## I am not a strong programmer. What is the minimum?

Download the kit, double-click run_baseline, and edit one function in agent/my_strategy.py: it receives the candidates that can be observed right now, already ranked, and returns the one to observe or None to wait. Upload that single file on the Submit page; the platform adds the rest of the starter agent. macOS's built-in Python is enough; Windows needs Python 3.12 from python.org.

## Do I need an astronomy background?

No. Your agent receives JSON snapshots: the current slot's weather, the candidate tiles with altitude, airmass, lunar factor, effective conditions and tile_science_value, the forecasts and requests issued so far, and your progress. The docs explain the few terms that appear, and scoring_preview.py already turns a snapshot into a ranked list of candidates.

## Can I compete alone?

Yes. Create a team with a size of 1. Teams may have up to 3 members.

## Which languages can I use?

Locally, any language: run your agent yourself and upload decisions.csv — accepted in both phases. For platform runs the entry script is Python 3.12 (minimal_agent.py, agent.py or main.py) speaking participant-agent-protocol-v1/v2 over stdin/stdout; it may install packages from requirements.txt and spawn whatever it needs inside the sandbox.

## Can I use an LLM or a commercial API?

Yes, at decision time too. Platform runs have network access: put the provider key into the .env of your package (MODEL_PROVIDER, MODEL_NAME, \<PROVIDER>\_API_KEY) and the minimal agent calls the model through LangChain. Keep the global wall clock in mind — a model call for every one of the \~8,000 decisions of a 180-night scenario does not fit; let deterministic code answer the obvious waits.

## What is the global wall clock?

Each scenario publishes global_wallclock_seconds (7200 s on the reference scenario, less on short ones). It starts after the initialize message and covers every decision — snapshot serialisation, your think time and parsing included. There is no per-decision limit. When it expires the process is stopped and the actions committed so far are scored, with the terminal penalties for unfinished REQUIRED tiles, FLEXIBLE shortfalls and expired requests.

## What is hidden on the competition scenarios?

weather.csv, weather_forecasts.csv and weather_events.csv. Your agent still sees the current slot's weather, each candidate's effective conditions (directional events applied), the forecasts issued so far and the active requests — everything the snapshots publish — but never future weather. On the practice scenarios all three files are public so that a local score_decisions.py run reproduces the platform report.

## How many submissions can I make?

Practice: 50 per team per day. Online competition: 10 per team per day. A team's best scored submission counts.

## Is the leaderboard score the final result?

The online competition board (October 5–7) determines the ranking. Organizers verify the top entries for rule compliance before awards are confirmed on October 17.

## Do I have to attend in Shenzhen?

No. The competition is online. Winning teams are invited to Awards Day at GOSIM Shenzhen on October 17; remote acceptance is possible.

## Is the platform free?

Yes. Registration, data, the starter kit and platform runs are free.

## Must I open-source my agent?

Not required. Organizers may ask top teams for their agent package to reproduce the result before awards are confirmed.

## What happens if my agent crashes or times out?

A run that fails to initialize within 30 seconds, exits, or answers with an invalid message ends with termination_reason agent_error or agent_initialization_error. The actions committed before that are still scored, with the terminal penalties applied, and the agent's stderr tail is shown on the submission page. If the global wall clock expires, the committed actions are scored the same way (global_wallclock_expired). Fix and resubmit within the daily limit.

## Are prizes taxed?

Prize amounts are gross. Payment details are confirmed with winning teams after Awards Day.

## Are there API credits for development?

Registered teams can claim sponsor API codes from the dashboard, one code per provider per team. Platform runs have network access, so the same key works in the .env of your package for hidden-weather runs as well as for local development.

Other questions: [hackathon@gosim.org](mailto:hackathon@gosim.org)
