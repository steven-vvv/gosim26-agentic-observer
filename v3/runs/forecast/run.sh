#!/bin/bash
cd /home/steven/code/GOSIM26
ls -d scenarios/comp-* | xargs -P 4 -I{} sh -c 'uv run python tools/plan_experiment.py {} > runs/forecast/$(basename {}).txt 2>&1'
echo exit=done >> runs/forecast/status.txt
