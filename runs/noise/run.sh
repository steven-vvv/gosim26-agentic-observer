#!/bin/bash
# obs11 的中性扰动：只轻微改变轨迹、预期得分不变，用来测单场分数的离散度（择优提交的收益）
cd /home/steven/code/GOSIM26
BASE='"exact_reads": true, "exact_faults": true, "single_fault": true'
declare -A V=( [n-it140]='"plan_iterations": 140' [n-it160]='"plan_iterations": 160' [n-ev3300]='"plan_every_seconds": 3300'
               [n-ev3900]='"plan_every_seconds": 3900' [n-ro2250]='"rollout_seconds": 2250' [n-ro2550]='"rollout_seconds": 2550'
               [n-k11]='"refine_top_k": 11' [n-k13]='"refine_top_k": 13' )
ARGS=""
for name in "${!V[@]}"; do
  rm -rf runs/noise/$name && rsync -a --exclude __pycache__ agents/obs11/ runs/noise/$name/
  echo "{$BASE, ${V[$name]}}" > runs/noise/$name/params.json
  ARGS="$ARGS --agent $name=runs/noise/$name/main.py"
done
uv run python tools/evaluate.py --agent obs11-frozen=agents/obs11/main.py $ARGS --scenario scenarios/comp-a-s102 --scenario scenarios/comp-b-s205 \
    --scenario scenarios/mc-a-9106 --scenario scenarios/mc-b-9203 --jobs 3 > runs/noise/eval.txt 2>&1
echo exit=done >> runs/noise/eval.txt
