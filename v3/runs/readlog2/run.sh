#!/bin/bash
cd /home/steven/code/GOSIM26
while ! grep -q "exit=done" runs/eval-obs12c.txt 2>/dev/null; do sleep 30; done
uv run python tools/evaluate.py --agent readlog2=runs/readlog2/agent/main.py $(cat runs/all46.args) --jobs 4 --keep > runs/readlog2/eval.txt 2>&1
echo exit=done >> runs/readlog2/eval.txt
