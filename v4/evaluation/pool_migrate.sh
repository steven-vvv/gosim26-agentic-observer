#!/bin/bash
# 把一台 AutoDL 主机的工作目录 /root/gosim 迁到数据盘 /root/autodl-tmp/gosim，原位留符号链接（pool.py、
# pool_deploy.sh 里写死的 /root/gosim 路径全部不用改）。绝不在有评测进程运行时切换。
#
#   pool_migrate.sh <host> precopy          任何时候都可跑（含主机繁忙时）：低优先级整体拷贝 + 静态部分校验和核对
#   pool_migrate.sh <host> swap [WAIT_SEC]  等到主机空闲（无 v4.evaluation 进程、无进程 cwd 在目录内）的瞬间：
#                                           补拷增量 -> 改名旧目录并建链接（毫秒级）-> 复核；失败自动回滚。WAIT_SEC 默认 0
#   pool_migrate.sh <host> check            迁移后验证：链接指向、源码哈希、磁盘占用
#   pool_migrate.sh <host> cleanup          验证通过后删除旧目录 gosim.old（仍有进程占用或旧目录有更新内容则拒绝）
#   pool_migrate.sh <host> rollback         撤销：删链接，把 gosim.old 改回
#
# 环境变量：GOSIM_ROOT（默认 /root/gosim）、GOSIM_DATA（默认 /root/autodl-tmp/gosim）、GOSIM_BUSY_PATTERN
# （默认 [v]4.evaluation）。演练时可把前两者指向临时目录、第三者设成永不匹配的模式，在忙碌主机上安全地走一遍全流程。
# 退出码：0 成功；3 主机忙；4 清理被拒；5 切换窗口内有进程误入旧目录（已回滚）；6 复核失败；7 无预拷贝；
#         8 旧目录已存在；9 已是符号链接；1 其他失败。
H="$1"; ACTION="$2"; WAIT="${3:-0}"
ROOT="${GOSIM_ROOT:-/root/gosim}"; DATA="${GOSIM_DATA:-/root/autodl-tmp/gosim}"
BUSYPAT="${GOSIM_BUSY_PATTERN:-[v]4.evaluation}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15)
say() { echo "[$(date +%H:%M:%S)] $H: $*"; }

# 远端公共函数：busy = 评测进程数；holders DIR = cwd 落在 DIR 内的进程数；pyhash DIR = 用 DIR 里的解释器算源码哈希
read -r -d '' PRELUDE <<EOF
ROOT='$ROOT'; DATA='$DATA'
busy() { pgrep -fc '$BUSYPAT' || true; }
holders() { local n=0 p l; for p in /proc/[0-9]*; do l=\$(readlink "\$p/cwd" 2>/dev/null) || continue
  case "\$l" in "\$1"|"\$1"/*) n=\$((n+1));; esac; done; echo \$n; }
pyhash() { (cd "\$1/GOSIM26" && . "\$1/env.sh" && .venv/bin/python -c 'import sys, numpy, pandas, scipy, orjson, highspy
from v4.evaluation.run import source_sha256
print(source_sha256()[:16])' 2>&1 | tail -1); }
EOF

# 把脚本体（PRELUDE + stdin）作为文件送到目标机再执行：脚本文本不进入 pgrep -f 的匹配范围，命令也吞不掉脚本 stdin。
# 主机名 local 表示就在本机执行（仅用于演练）。
run_on() { if [ "$H" = local ]; then bash -c "$1"; else "${SSH[@]}" "$H" "$1"; fi; }
remote() {
  local body tmp="/tmp/gosim-migrate.$$.sh"
  body="$PRELUDE"$'\n'"$(cat)"
  printf '%s\n' "$body" | run_on "cat > $tmp" || return 1
  run_on "bash $tmp < /dev/null; rc=\$?; rm -f $tmp; exit \$rc"
}

case "$ACTION" in
precopy)
  remote <<'EOF'
[ -L "$ROOT" ] && { echo "ALREADY-LINK"; exit 9; }
[ -d "$ROOT" ] || { echo "NO-ROOT"; exit 1; }
[ -d "$(dirname "$DATA")" ] || { echo "NO-DATADISK"; exit 1; }
mkdir -p "$DATA"
echo "拷贝 $ROOT -> $DATA（nice/ionice 低优先级，评测进程不受影响）"
RS="rsync -aH"; ionice -c3 true 2>/dev/null && RS="ionice -c3 $RS"; RS="nice -n 19 $RS"
$RS "$ROOT"/ "$DATA"/ || { echo "COPY-FAIL"; exit 1; }
# 静态部分（不会被评测改写）逐文件 sha256 对比；动态部分（cards/results/runs）由 swap 时的增量补拷覆盖
bad=0
for d in GOSIM26/.venv GOSIM26/v4/agent GOSIM26/v4/evaluation GOSIM26/v4/experiments hackathon-survey26 up.git env.sh; do
  [ -e "$ROOT/$d" ] || continue
  (cd "$ROOT" && find "$d" -type f -not -name '*.pyc' -print0 | sort -z | xargs -0r sha256sum) > /tmp/mig-a.$$ 2>&1
  (cd "$DATA" && find "$d" -type f -not -name '*.pyc' -print0 | sort -z | xargs -0r sha256sum) > /tmp/mig-b.$$ 2>&1
  if ! cmp -s /tmp/mig-a.$$ /tmp/mig-b.$$; then echo "CHECKSUM-DIFF in $d:"; diff /tmp/mig-a.$$ /tmp/mig-b.$$ | head -5; bad=1; fi
  echo "  校验 $d: $(wc -l < /tmp/mig-a.$$) 个文件"
done
rm -f /tmp/mig-a.$$ /tmp/mig-b.$$
[ $bad = 0 ] || exit 1
ha=$(pyhash "$ROOT"); hb=$(pyhash "$DATA")
echo "源码哈希 原目录=$ha 数据盘副本=$hb"
[ -n "$ha" ] && [ "$ha" = "$hb" ] || { echo "HASH-MISMATCH"; exit 1; }
echo "PRECOPY-OK 大小(字节) 原=$(du -sb "$ROOT" | cut -f1) 副本=$(du -sb "$DATA" | cut -f1)"
EOF
  ;;
swap)
  deadline=$(( $(date +%s) + WAIT ))
  while :; do
    out=$(remote <<'EOF'
[ -L "$ROOT" ] && { echo "ALREADY-LINK"; exit 9; }
[ -e "$ROOT.old" ] && { echo "OLD-EXISTS"; exit 8; }
[ -d "$DATA/GOSIM26" ] || { echo "NO-PRECOPY"; exit 7; }
idle() { [ "$(busy)" = 0 ] && [ "$(holders "$ROOT")" = 0 ]; }
idle || { echo "BUSY procs=$(busy) cwd_holders=$(holders "$ROOT")"; exit 3; }
rsync -aH --delete "$ROOT"/ "$DATA"/ || { echo "DELTA1-FAIL"; exit 1; }
idle || { echo "BUSY-AFTER-DELTA"; exit 3; }
t0=$(date +%s%N)
if ! { mv "$ROOT" "$ROOT.old" && ln -s "$DATA" "$ROOT"; }; then
  [ -L "$ROOT" ] && rm "$ROOT"; [ -d "$ROOT.old" ] && [ ! -e "$ROOT" ] && mv "$ROOT.old" "$ROOT"; echo "SWAP-FAIL"; exit 1
fi
t1=$(date +%s%N)
rsync -aH --update "$ROOT.old"/ "$DATA"/   # 切换间隙内写入旧目录的内容（不带 --delete，不覆盖新目录里更新的文件）
rollback() { rm "$ROOT" && mv "$ROOT.old" "$ROOT"; }
h=$(holders "$ROOT.old")
if [ "$h" != 0 ]; then rollback; echo "ROLLED-BACK: 切换间隙内有 $h 个进程进入旧目录"; exit 5; fi
s=$(pyhash "$ROOT"); want=$(pyhash "$ROOT.old")
if [ -z "$s" ] || [ "$s" != "$want" ]; then
  if [ "$(busy)" != 0 ] || [ "$(holders "$DATA")" != 0 ]; then echo "MANUAL: 复核失败且已有进程使用新目录，未回滚 new=$s old=$want"; exit 6; fi
  rollback; echo "ROLLED-BACK: 复核失败 new=$s old=$want"; exit 6
fi
echo "SWAPPED gap_ms=$(( (t1-t0)/1000000 )) hash=$s target=$(readlink -f "$ROOT")"
EOF
)
    rc=$?
    now=$(date +%s)
    if [ $rc != 3 ] || [ -z "${lastsay:-}" ] || [ $((now - lastsay)) -ge 120 ]; then
      say "$(echo "$out" | tr '\n' ' ')"; lastsay=$now
    fi
    if [ $rc = 3 ] && [ "$now" -lt "$deadline" ]; then sleep 4; continue; fi
    exit $rc
  done
  ;;
check)
  remote <<'EOF'
echo "链接: $(ls -ld "$ROOT" | sed 's/^.* root root *[0-9]* //')  -> $(readlink -f "$ROOT")"
echo "源码哈希(经链接路径): $(pyhash "$ROOT")"
df -h / "$(dirname "$DATA")" | sed 's/^/  /'
[ -e "$ROOT.old" ] && echo "旧目录 $ROOT.old 仍在（$(du -sh "$ROOT.old" | cut -f1)），验证后请 cleanup" || echo "旧目录已清理"
EOF
  ;;
cleanup)
  remote <<'EOF'
[ -L "$ROOT" ] || { echo "NOT-A-LINK（未迁移），不删除"; exit 1; }
[ -d "$ROOT.old" ] || { echo "nothing-to-clean"; exit 0; }
[ "$(holders "$ROOT.old")" = 0 ] || { echo "HOLDERS-IN-OLD: 仍有进程 cwd 在旧目录内，拒绝删除"; exit 4; }
# 删除前再确认：新目录是旧目录的超集（旧目录里没有新目录缺失或更新的文件）
miss=$(rsync -aHn --update --itemize-changes "$ROOT.old"/ "$DATA"/ | grep -cE '^[<>c]' || true)
[ "$miss" = 0 ] || { echo "OLD-HAS-NEWER: 旧目录里有 $miss 项新目录缺失或更旧，拒绝删除"; exit 4; }
rm -rf "$ROOT.old" && echo "removed"
df -h / "$(dirname "$DATA")" | sed 's/^/  /'
EOF
  ;;
rollback)
  remote <<'EOF'
if [ -L "$ROOT" ] && [ -d "$ROOT.old" ]; then
  [ "$(busy)" = 0 ] || echo "警告：有评测进程在跑；回滚后它们写入数据盘目录的内容需手动合并"
  rm "$ROOT" && mv "$ROOT.old" "$ROOT" && echo "rolled-back"
else echo "nothing-to-rollback"; fi
ls -ld "$ROOT"
EOF
  ;;
*) echo "用法: $0 <host> precopy|swap [WAIT_SEC]|check|cleanup|rollback"; exit 2;;
esac
