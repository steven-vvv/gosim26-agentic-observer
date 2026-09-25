#!/bin/bash
cd /home/steven/code/GOSIM26
CHECK="--scenario scenarios/comp-a-s101 --scenario scenarios/comp-b-s201 --scenario scenarios/mc-a-9104 --scenario scenarios/mc-b-9204"
uv run python tools/evaluate.py --agent obs10=agents/obs10/main.py --agent obs11-off=agents/obs11-off/main.py --agent obs11t=agents/obs11t/main.py $CHECK --jobs 4 > runs/eval-obs11-check.txt 2>&1
uv run python tools/evaluate.py --agent obs10=agents/obs10/main.py --agent obs11=agents/obs11/main.py --agent obs11p=agents/obs11p/main.py $(cat runs/readlog/scenarios.args) --jobs 4 > runs/eval-obs11.txt 2>&1
echo exit=done >> runs/eval-obs11.txt
