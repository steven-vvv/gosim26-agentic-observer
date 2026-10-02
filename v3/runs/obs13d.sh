#!/bin/bash
cd /home/steven/code/GOSIM26
while ! grep -q "exit=done" runs/eval-obs13c.txt 2>/dev/null; do sleep 30; done
uv run python tools/evaluate.py --agent obs13=agents/obs13/main.py --agent obs13-ro5400=agents/obs13-ro5400/main.py \
    --agent obs13-ev1200=agents/obs13-ev1200/main.py $(cat runs/all46.args) --jobs 4 > runs/eval-obs13d.txt 2>&1
echo exit=done >> runs/eval-obs13d.txt
