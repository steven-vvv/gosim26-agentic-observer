---
source: https://create.gosim.org/survey26/platform/faq?lang=en
fetched_utc: 2026-09-25T16:22:29+00:00
method: rendered main; all FAQ entries expanded
---

FAQ

# Questions before you register

I am not a strong programmer. What is the minimum?

Download the kit, double-click run\_baseline, and edit one function in agent/my\_strategy.py: it receives the candidates that can be observed right now, already ranked, and returns the one to observe or None to wait. Double-click run\_baseline again and drop the run\_output/decisions.csv it writes onto the Submit page. macOS's built-in Python is enough; Windows needs Python 3.12 from python.org.

Do I need an astronomy background?

No. Your agent receives JSON snapshots: the current slot's weather, the candidate tiles with altitude, airmass, lunar factor, effective conditions and tile\_science\_value, the forecasts and requests issued so far, and your progress. The docs explain the few terms that appear, and scoring\_preview.py already turns a snapshot into a ranked list of candidates.

Can I compete alone?

Yes. Create a team with a size of 1. Teams may have up to 3 members.

Which languages can I use?

Any implementation language is allowed. Use the starter kit locally and submit decisions.csv.

Can I use an LLM or a commercial API?

Yes. Your agent runs on your own machine and may call a model at decision time too, with your own key or a sponsor code from the dashboard. A 180-night scenario has about 8,000 decisions, and a model call for each is slow; let deterministic code answer the obvious waits.

What is the global wall clock?

Each scenario publishes global\_wallclock\_seconds (7200 s on the reference scenario, less on short ones). It starts after the initialize message and covers every decision — snapshot serialisation, your think time and parsing included. There is no per-decision limit. When it expires the process is stopped and the actions committed so far are scored, with the terminal penalties for unfinished REQUIRED tiles, FLEXIBLE shortfalls and expired requests.

What scenario data can I download?

Current scenario weather, forecasts and events are public. Use score\_decisions.py to reproduce the score.

How many submissions can I make?

Each team can submit 50 times a day. Each scenario ranks the team’s best score independently.

Is the leaderboard score the final result?

Playground standings are for practice and do not determine awards.

Do I have to attend in Shenzhen?

No. The competition is online. Winning teams are invited to Awards Day at GOSIM Shenzhen on October 17; remote acceptance is possible.

Is the platform free?

Yes. Registration, data, the starter kit and scoring are free.

Must I open-source my agent?

Not required. Before awards are confirmed, organizers may ask top teams for their agent code and regenerate the decisions.csv with it to reproduce the result.

What happens if my agent crashes or times out?

A run that fails to initialize within 30 seconds, exits, or answers with an invalid message ends with termination\_reason agent\_error or agent\_initialization\_error. The actions committed before that are still scored, with the terminal penalties applied, and the agent's stderr tail is shown on the submission page. If the global wall clock expires, the committed actions are scored the same way (global\_wallclock\_expired). Fix and resubmit within the daily limit.

Are prizes taxed?

Prize amounts are gross. Payment details are confirmed with winning teams after Awards Day.

Are there API credits for development?

Registered teams can claim sponsor API codes from the dashboard, one code per provider per team, for calling models while running the agent locally.

Other questions: [hackathon@gosim.org](mailto:hackathon@gosim.org)