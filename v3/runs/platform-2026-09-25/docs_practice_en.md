## 1. Current competition

The current competition is Playground. Run an algorithm, upload results and inspect your score. The platform chooses the competition automatically.

## 2. Registration and teams

Register, then create a team in Find teammates or click a team to request membership. The captain accepts or declines. Team members may also invite participants. Both sides can track progress through Team notifications in the top right. Teams have up to 3 members.

## 3. Run the starter kit

Download and extract the kit from [Resources](/resources). Double-click `run_baseline`, or run:

```sh
python3 local_runner.py --scenario scenarios/dev-reference --agent agent/minimal_agent.py --wallclock 600 --out run_output
python3 score_decisions.py --scenario scenarios/dev-reference --decisions run_output/decisions.csv
```

Use `scenarios/demo-week` for a shorter demonstration. Edit `choose_action(candidates, snapshot, memory)` in `agent/my_strategy.py`, rerun and compare scores. Any implementation language is allowed. Custom programs exchange JSON messages over standard input/output following the kit's protocol; write ordinary logs to standard error.

Model calls are optional. The deterministic baseline needs no key. Never put model credentials in results or public code.

## 4. Submit and inspect results

Open [Submit](/compete), choose the scenario you used locally and upload `run_output/decisions.csv`. Maximum file size is 20 MB. Each team can submit up to 50 times per day, subject to the displayed quota.

The columns are `decision_id, slot_id, action, tile_id, program, request_id, reason`. After submission, inspect evaluation status, score components, completion and replay. Each scenario has separate standings using the team's best score.

## 5. Data and scoring

`config/` contains rules. `outputs/reference/` contains tiles, targets, calendar, slots, weather, forecasts, events and requests. Public files are downloadable from Resources. Current practice scenarios retain the original `participant-agent-protocol-v1` contract; existing scores and replays are preserved.

Snapshots expose available candidates, weather and progress. `observe` exposes a tile; `wait` advances time. The platform and kit use the same scorer. See [Rules](/rules) for science points, program bonuses, request rewards and penalties; the published scorer and scenario configuration define the exact formulas and constants.

## 6. Troubleshooting

Read `agent.log` after a program error. Check the scenario, CSV columns, file size and daily quota. The scoring command above independently reproduces the score. The kit's `SKILL.md`, `QUICKSTART.md` and `README.md` contain full command and field references.
