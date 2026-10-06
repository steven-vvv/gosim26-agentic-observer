#!/bin/bash
# 部署到一台 AutoDL 容器：与本机同一解释器（uv 管理的 CPython 3.14.7）、同一份 site-packages（逐字节复制）、
# 同一份代码（HEAD 的 v4）、锁定的上游引擎 d293dea、与阿里云相同的 env.sh。不传任何密钥或 token。
H="$1"; LOG=/tmp/deploy-$H.log
exec > "$LOG" 2>&1
set -e
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
t0=$(date +%s); step(){ echo "[$(( $(date +%s)-t0 ))s] $*"; }
step "mkdir"
$SSH $H 'mkdir -p /root/.uvpy /root/.local/bin /root/gosim/GOSIM26' < /dev/null
step "ensure git"
$SSH $H 'command -v git >/dev/null || (command -v dnf >/dev/null && dnf -y -q install git || (apt-get update -qq && apt-get install -y -qq git))' < /dev/null
step "python 3.14.7"
rsync -az -e "$SSH" ~/.local/share/uv/python/cpython-3.14.7-linux-x86_64-gnu/ $H:/root/.uvpy/cpython-3.14.7-linux-x86_64-gnu/
step "uv + code + engine + cards + env"
rsync -az -e "$SSH" ~/.local/bin/uv $H:/root/.local/bin/uv
rsync -az -e "$SSH" /tmp/gosim-push/ $H:/root/gosim/GOSIM26/
rsync -az -e "$SSH" /tmp/up.git/ $H:/root/gosim/up.git/
rsync -az -e "$SSH" /tmp/parity-cards.tar /tmp/gosim_env.sh $H:/root/gosim/
$SSH $H 'set -e; mv /root/gosim/gosim_env.sh /root/gosim/env.sh
  git config --global --add safe.directory "*"
  cd /root/gosim && rm -rf hackathon-survey26 && git clone -q up.git hackathon-survey26 && git -C hackathon-survey26 checkout -q d293dea69b78d15fa8549b73ba242af301abe2b4 && echo "engine HEAD $(git -C hackathon-survey26 rev-parse HEAD)"
  cd /root/gosim/GOSIM26 && UV_PYTHON_DOWNLOADS=never /root/.local/bin/uv venv -q --allow-existing .venv --python /root/.uvpy/cpython-3.14.7-linux-x86_64-gnu/bin/python3.14
  cd v4/cards && tar -xf /root/gosim/parity-cards.tar && ls' < /dev/null
step "site-packages (byte copy)"
rsync -az --checksum --exclude '__pycache__' -e "$SSH" /home/steven/code/GOSIM26/.venv/lib/python3.14/site-packages/ $H:/root/gosim/GOSIM26/.venv/lib/python3.14/site-packages/
step "verify"
$SSH $H 'cd /root/gosim/GOSIM26 && . /root/gosim/env.sh && .venv/bin/python -c "import sys,numpy,pandas,scipy,orjson,highspy; print(sys.version.split()[0], numpy.__version__, pandas.__version__, scipy.__version__, orjson.__version__)" && .venv/bin/python -c "import numpy; numpy.show_runtime()" 2>&1 | grep -A3 -E "simd_extensions" | head -4; .venv/bin/python -m pytest -q v4/tests 2>&1 | tail -1' < /dev/null
step "parity runs start"
$SSH $H 'cd /root/gosim/GOSIM26 && . /root/gosim/env.sh && for c in kind-validation-r3-011 trial-a; do setsid nohup .venv/bin/python -m v4.evaluation.run --card v4/cards/$c --out /root/gosim/parity/$c --fast > /root/gosim/parity-$c.log 2>&1 < /dev/null & done; sleep 2; pgrep -fc "[e]valuation.run"' < /dev/null
step "DONE"
