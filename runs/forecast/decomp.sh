#!/bin/bash
# 天气信息价值分解：每个 (场景, 变体) 一个进程，3 路并行；另起 1 路跑 obs10 并保留完整评分报告
cd /home/steven/code/GOSIM26
mkdir -p runs/forecast/decomp
rm -f runs/forecast/decomp/status.txt
(uv run python tools/evaluate.py --agent diag-obs10=agents/obs10/main.py --scenario scenarios/comp-a-s101 \
    --scenario scenarios/comp-b-s201 --jobs 1 --keep > runs/forecast/decomp/diag-obs10.txt 2>&1) &
for s in scenarios/comp-*; do for v in 持续性 知道每夜天气 知道每格天气 知道关闭; do echo "$s $v"; done; done |
  xargs -P 3 -L 1 sh -c 'uv run python tools/plan_experiment.py "$0" --variants "$1" > "runs/forecast/decomp/$(basename $0)-$1.txt" 2>&1'
wait
echo exit=done >> runs/forecast/decomp/status.txt
