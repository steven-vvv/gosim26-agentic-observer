#!/bin/bash
# macOS: double-click to run the baseline agent on the finals-preview scenario (anomaly mechanics on) and open the replay.
# Seven nights with the finals mechanics on: hidden tile tags, an instrument fault, score feedback and the report channel.
cd "$(dirname "$0")"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "No Python 3.9+ found. Install Python 3.12 from https://www.python.org/downloads/ and run this again."
  read -r -p "Press Enter to close." _; exit 1
fi
echo "Using $PY ($("$PY" --version 2>&1))"
"$PY" local_runner.py --scenario scenarios/finals-preview --agent agent/minimal_agent.py --wallclock 900 --out demo_week_output
status=$?
if [ -f demo_week_output/decision_replay.html ]; then open demo_week_output/decision_replay.html; fi
echo; echo "Done (exit $status). Score report: demo_week_output/score_report.json"
read -r -p "Press Enter to close." _
