#!/bin/bash
# 分解实验的剩余作业，按变体优先排序（先把现实可得的“持续性”与“知道每夜天气”跑满 14 场）
cd /home/steven/code/GOSIM26
# （旧版在这里等待前一批作业结束）
for v in 持续性 知道每夜天气 知道每格天气 知道关闭; do for s in scenarios/comp-*; do
  out="runs/forecast/decomp/$(basename $s)-$v.txt"; [ -s "$out" ] || echo "$s $v"; done; done |
  xargs -P "${JOBS:-3}" -L 1 sh -c 'uv run python tools/plan_experiment.py "$0" --variants "$1" > "runs/forecast/decomp/$(basename $0)-$1.txt" 2>&1'
echo exit=done >> runs/forecast/decomp/status2.txt
