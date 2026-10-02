# Quick start (no tooling required)

Three steps: run it → edit one file → upload it. No command line and no third-party packages needed.

## Step 1 · Run the baseline (see a score and a replay)

| Your computer | What to do |
|---|---|
| macOS | double-click `run_baseline.command` (the Python that ships with macOS is enough; if it "cannot be opened", right-click → Open) |
| Windows | install Python 3.12 from https://www.python.org/downloads/ (tick **Add python.exe to PATH**), then double-click `run_baseline.bat` |
| Linux | run `./run_baseline.sh` in a terminal |

After about 15 seconds a web page opens: the replay of the baseline agent over 180 nights of the public scenario.
The terminal ends with the score, about **12287** for the unmodified kit, with `termination_reason = survey_complete`.

For a first look, use `run_demo_week` (`.command` / `.bat` / `.sh`) instead: same pipeline and same scorer over a
seven-night scenario. It finishes in about two seconds and the replay is short enough to follow night by night.
Its results go to `demo_week_output/`.
Note: seven nights is short, so the shipped anomaly detector only gets part of the hidden tags right there — it can
miss some and file a wrong tag report. That is expected: it is a demonstration detector, not a calibrated solution,
and the 180-night scenario is the one where it reports all four tags.

## Step 2 · Edit one file

Open `agent/my_strategy.py`. The whole competition fits in its `choose_action` function:

- each decision the platform hands you the candidates that can be observed right now, ranked (index 0 = highest estimated gain);
- return the candidate you want to observe, or `None` to wait for this slot.

The file contains ideas you can uncomment (REQUIRED tiles first, serve observation requests, wait in poor
conditions, remember things in `memory`) and documents every field of a candidate.

Save, double-click `run_baseline` again, and compare the score. When the agent fails, the terminal prints the
last lines of `agent.log` for you.

## Step 3 · Upload

1. Open the competition website → register → create a team (a team of one is fine).
2. "Submit" page → choose **Agent run** → drop the single file `agent/my_strategy.py` into the upload box
   (or drop the whole `agent` folder: the website packages it for you).
3. The page shows your queue position and progress; a score normally arrives within a couple of minutes,
   with the breakdown, the night-by-night replay and the logs.

The platform wraps your `my_strategy.py` with the kit's standard files: you never need to build a zip.

## Going further

- More weather to practise on: `python3 make_scenario.py --out scenarios/mine --seed 7 --days 30`, then
  `python3 local_runner.py --scenario scenarios/mine --agent agent/minimal_agent.py`.
- Let a language model take part: copy `agent/.env.example` to `agent/.env`, set `MODEL_PROVIDER` and the
  matching API key (sponsor credits are on the website's dashboard), and upload the whole `agent` folder.
- Data formats, the protocol and the scoring formula are on the website's Docs page; `README.md` is the
  engineer's version of this guide.

> Practice scenarios keep the pre-anomaly rules (no tags, no repeats, no reports). To rehearse the finals mechanics, run `run_finals_preview` / `scenarios/finals-preview` (baseline about **8214**; the sample agent detects and reports the instrument fault by itself).
