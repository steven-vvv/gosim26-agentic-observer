#!/bin/bash
cd /home/steven/code/GOSIM26
(uv run python tools/evaluate.py --agent readlog3=runs/readlog3/agent/main.py $(cat runs/all46.args) --jobs 2 --keep > runs/readlog3/eval.txt 2>&1; echo exit=done >> runs/readlog3/eval.txt) &
uv run python tools/evaluate.py --agent obs12-roev=agents/obs12-roev/main.py --agent obs13-req=agents/obs13-req/main.py \
    --agent obs13-b90=agents/obs13-b90/main.py --agent obs13-lam=agents/obs13-lam/main.py --agent obs13-fault=agents/obs13-fault/main.py \
    $(cat runs/all46.args) --jobs 2 > runs/eval-obs13.txt 2>&1
echo exit=done >> runs/eval-obs13.txt
wait
