#!/bin/bash
cd /home/steven/code/GOSIM26
while ! grep -q "exit=done" runs/noise/eval.txt 2>/dev/null; do sleep 30; done
uv run python tools/evaluate.py --agent obs11=agents/obs11/main.py --agent obs12-off=agents/obs12-off/main.py \
    --scenario scenarios/comp-a-s101 --scenario scenarios/comp-b-s201 --scenario scenarios/mc-a-9104 --scenario scenarios/mc-b-9204 --jobs 4 > runs/eval-obs12-check.txt 2>&1
uv run python tools/evaluate.py --agent obs11=agents/obs11/main.py --agent obs12r=agents/obs12r/main.py $(cat runs/readlog/scenarios.args) --jobs 4 > runs/eval-obs12.txt 2>&1
echo exit=done >> runs/eval-obs12.txt
