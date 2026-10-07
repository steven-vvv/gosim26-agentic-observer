#!/usr/bin/env bash
# 用法：platform_upload.sh <zip> [标题]
# 上传 → 轮询准备与公开测试 → 通过后确认并标记为最终版本。
# 不触发在线评测，也不做「选定冻结」（不可逆）。令牌只在本进程内从 secret 读入，不回显、不落盘。
# 续跑：REV=<版本号> 时跳过上传，直接对已有版本继续走轮询与后续步骤（不重复占用上传额度）。
set -euo pipefail

ZIP="${1:?用法: platform_upload.sh <zip> [标题]}"
TITLE="${2:-$(basename "$ZIP" .zip)}"
INTERVAL="${POLL_INTERVAL:-30}"
TIMEOUT="${POLL_TIMEOUT:-1800}"
REV="${REV:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRET="${SECRET_FILE:-$ROOT/secret}"
# secret 里的键名就是 SURVERY26_TOKEN（拼写如此），CLI 读取的是 SURVEY26_TOKEN
SURVEY26_TOKEN="$(sed -n 's/^SURVERY26_TOKEN=//p' "$SECRET")"
[ -n "$SURVEY26_TOKEN" ] || { echo "secret 中没有 SURVERY26_TOKEN" >&2; exit 3; }
export SURVEY26_TOKEN

[ -f "$ZIP" ] || { echo "找不到 $ZIP" >&2; exit 2; }
size=$(stat -c %s "$ZIP")
files=$(unzip -Z1 "$ZIP" | wc -l)
echo "包 $ZIP：$((size / 1048576)) MB，$files 个文件"
[ "$size" -le $((50 * 1048576)) ] || { echo "超过平台 50 MB 上限" >&2; exit 2; }
[ "$files" -le 10000 ] || { echo "文件数超过 10000" >&2; exit 2; }
unzip -tq "$ZIP" >/dev/null

jget() { python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][sys.argv[1]])' "$1"; }

if [ -n "$REV" ]; then
  rev="$REV"
  echo "续跑已有版本 $rev，跳过上传"
else
  rev=$(survey26 project upload "$ZIP" --title "$TITLE" --yes --json | jget revision_id)
  echo "已上传，版本 $rev"
fi

# 准备（构建 + 公开测试）：排队与准备都算未完成（CLI 中 pending = queued + preparing）
start=$(date +%s)
while :; do
  info=$(survey26 project show "$rev" --json)
  status=$(printf '%s' "$info" | jget raw_status)
  echo "$(date +%T) 状态 $status"
  case "$status" in queued|preparing) ;; *) break;; esac
  [ $(( $(date +%s) - start )) -lt "$TIMEOUT" ] || { echo "等待超时" >&2; exit 7; }
  sleep "$INTERVAL"
done
printf '%s' "$info" | python3 -c '
import json, sys
x = json.load(sys.stdin)["data"]
print("公开测试:", json.dumps(x.get("public_test"), ensure_ascii=False))
if x.get("error"):
    print("错误:", x["error"])
'
[ "$status" = reviewable ] || { echo "校验未通过（状态 $status），不标记最终版本" >&2; exit 9; }
printf '%s' "$info" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["data"]["public_test"].get("passed") else 1)' \
  || { echo "公开测试未通过，不标记最终版本" >&2; exit 9; }

# 确认不占评测次数；final set 只是「选择」，没有冻结语义
survey26 project confirm "$rev" --yes --json >/dev/null
echo "已确认"
survey26 final set "$rev" --json >/dev/null
survey26 final show --json | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
print("最终版本:", d["revision_id"], "来源:", d["source"], "locked:", d["locked"], "frozen_at:", d["frozen_at"])
'
