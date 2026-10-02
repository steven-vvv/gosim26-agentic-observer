#!/bin/bash
# 用法：run.sh <agent main.py> <名字> <场景目录>...；fast_runner + PyPy，输出到 /tmp/robust/<名字>/<场景>
cd /home/steven/code/GOSIM26
AGENT=$1; NAME=$2; shift 2
for s in "$@"; do
  out=/tmp/robust/$NAME/$(basename $s)
  mkdir -p $out
  echo "pypy3.11 tools/fast_runner.py --scenario $s --agent $AGENT --out $out --no-replay --quiet --python pypy3.11 > $out.stdout 2> $out.stderr"
done | xargs -P 3 -I{} sh -c "{}"
