#!/bin/bash
cd /home/steven/code/GOSIM26
while ! grep -q "exit=done" runs/eval-base-new16.txt 2>/dev/null; do sleep 30; done
for s in $(cat runs/new16.args | tr ' ' '\n' | grep scenarios/); do
  uv run python tools/upper_bound.py $s > /dev/null 2>&1 || echo "failed $s"
done
echo exit=done > runs/ub-new16.done
