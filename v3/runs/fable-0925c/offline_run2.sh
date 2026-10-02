#!/bin/bash
cd /home/steven/code/GOSIM26
for s in comp-a-s101 comp-b-s201 comp-a-s104 comp-b-s205 comp-a-s107 comp-b-s203; do
  .venv/bin/python /tmp/fable-0925c/offline_driver.py scenarios/$s base_1h,effblind,effcold,effcold_plancold,plancold .cold >> /tmp/fable-0925c/offline/log_cold.txt 2>&1
done
echo done >> /tmp/fable-0925c/offline/log_cold.txt
