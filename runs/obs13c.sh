#!/bin/bash
cd /home/steven/code/GOSIM26
while ! grep -q "exit=done" runs/eval-obs13.txt 2>/dev/null || ! grep -q "exit=done" runs/eval-obs13b.txt 2>/dev/null; do sleep 30; done
uv run python tools/evaluate.py --agent obs12-roev=agents/obs12-roev/main.py --agent obs13=agents/obs13/main.py \
    --agent obs13-cf=agents/obs13-cf/main.py $(cat runs/all46.args) --jobs 4 > runs/eval-obs13c.txt 2>&1
echo exit=done >> runs/eval-obs13c.txt
