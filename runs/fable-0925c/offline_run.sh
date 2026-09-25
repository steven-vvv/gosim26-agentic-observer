#!/bin/bash
cd /home/steven/code/GOSIM26
for s in comp-a-s101 comp-b-s201 comp-a-s104 comp-b-s205 comp-a-s107 comp-b-s203; do
  .venv/bin/python /tmp/fable-0925c/offline_driver.py scenarios/$s base_1h,iters4x,bin30_it4x,replan30,effblind,effblind_replan30,rollout >> /tmp/fable-0925c/offline/log.txt 2>&1
done
for s in comp-a-s101 comp-b-s201; do
  .venv/bin/python /tmp/fable-0925c/offline_driver.py scenarios/$s exact_lp,base_1h >> /tmp/fable-0925c/offline/log_exact.txt 2>&1
  mv /tmp/fable-0925c/offline/$s.json /tmp/fable-0925c/offline/$s.exact.json
done
echo done >> /tmp/fable-0925c/offline/log.txt
