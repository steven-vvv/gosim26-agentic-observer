#!/bin/bash
cd /home/steven/code/GOSIM26
while ! grep -q "exit=done" runs/eval-obs12.txt 2>/dev/null; do sleep 30; done
uv run python tools/evaluate.py --agent obs11=agents/obs11/main.py --agent obs12-ro3600=agents/obs12-ro3600/main.py \
    --agent obs12-rok8=agents/obs12-rok8/main.py --agent obs12-ev1800=agents/obs12-ev1800/main.py $(cat runs/readlog/scenarios.args) --jobs 4 > runs/eval-obs12b.txt 2>&1
echo exit=done >> runs/eval-obs12b.txt
