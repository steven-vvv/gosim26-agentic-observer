#!/bin/bash
# 用法：runs/verify-pkg.sh dist/observer-obs13.zip obs13 —— 平台式环境（3.12 + numpy）与系统 3.12（无 numpy）各跑一遍官方 runner
cd /home/steven/code/GOSIM26
PKG=$1; NAME=$2; DIR=runs/pkg-$NAME
rm -rf $DIR results/pkg-$NAME-np results/pkg-$NAME-py runs/eval/pkg-$NAME-np runs/eval/pkg-$NAME-py && mkdir -p $DIR/agent && (cd $DIR/agent && unzip -q ../../../$PKG)
(uv run python tools/evaluate.py --agent pkg-$NAME-np=$DIR/agent/main.py --scenario scenarios/comp-a-s101 --official --python /tmp/py312np/bin/python --jobs 1 --keep > $DIR/np.txt 2>&1) &
(uv run python tools/evaluate.py --agent pkg-$NAME-py=$DIR/agent/main.py --scenario scenarios/comp-a-s101 --official --python /usr/bin/python3.12 --jobs 1 --keep > $DIR/py.txt 2>&1) &
wait
for f in decisions.csv score_report.json; do cmp runs/eval/pkg-$NAME-np/comp-a-s101/$f runs/eval/pkg-$NAME-py/comp-a-s101/$f && echo "$f：numpy 与纯 Python 回退逐字节相同"; done
python3 -c "
import json
for v in ('np', 'py'):
    d = json.load(open('results/pkg-$NAME-' + v + '/comp-a-s101.json')); print(v, d['total'], '耗时', d['elapsed'], 's')"
grep -h "plan #" runs/eval/pkg-$NAME-np/comp-a-s101/agent.log | tail -1; grep -h "plan #" runs/eval/pkg-$NAME-py/comp-a-s101/agent.log | tail -1
grep -h "projected" runs/eval/pkg-$NAME-*/comp-a-s101/agent.log
echo exit=done > $DIR/done
