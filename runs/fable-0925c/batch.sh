#!/bin/bash
# 用法：batch.sh <任务文件>；每行 "名字 agent路径 场景目录"，3 路并行，fast_runner + PyPy，输出 /tmp/fable-0925c/runs/<名字>/<场景>
cd /home/steven/code/GOSIM26
while read -r name agent scen; do
  out=/tmp/fable-0925c/runs/$name/$(basename $scen)
  [ -f $out/score_report.json ] && continue
  mkdir -p $out
  echo "pypy3.11 tools/fast_runner.py --scenario $scen --agent $agent --out $out --no-replay --quiet --python pypy3.11 > $out.stdout 2> $out.stderr; rm -f $out/workflow_result.json"
done < "$1" | xargs -P ${JOBS:-3} -I{} sh -c "{}"
echo "batch done $(date)" >> /tmp/fable-0925c/runs/batch.log
