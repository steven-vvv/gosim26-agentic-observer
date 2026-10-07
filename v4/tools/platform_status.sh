#!/usr/bin/env bash
# 输出平台上的版本列表、评测额度与当前最终版本（只读）。
# 令牌只在本进程内从 secret 读入环境变量，不回显、不落盘。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SECRET="${SECRET_FILE:-$ROOT/secret}"
# secret 里的键名就是 SURVERY26_TOKEN（拼写如此），CLI 读取的是 SURVEY26_TOKEN
SURVEY26_TOKEN="$(sed -n 's/^SURVERY26_TOKEN=//p' "$SECRET")"
[ -n "$SURVEY26_TOKEN" ] || { echo "secret 中没有 SURVERY26_TOKEN" >&2; exit 3; }
export SURVEY26_TOKEN

echo "== 版本列表（含已撤回）=="
survey26 project list --all --json | python3 -c "$(cat <<'PY'
import json, sys
revs = json.load(sys.stdin)["data"]
print(f"{'revision':9} {'title':24} {'status':11} {'created(UTC)':19} {'评测':>3} {'公开测试':>10}")
for r in revs:
    pt = r.get("public_test") or {}
    print(f"{r['revision_id'][:8]:9} {r['title'][:24]:24} {r.get('raw_status', ''):11} "
          f"{str(r.get('created_at'))[:19]:19} {r.get('evaluations', 0):>3} {str(pt.get('score'))[:10]:>10}")
PY
)"

echo
echo "== 评测额度 =="
survey26 quota --json | python3 -c "$(cat <<'PY'
import json, sys
for q in json.load(sys.stdin)["data"]:
    print(f"{q['phase']:18} 已用 {q['used']:>4}  剩余 {q['remaining']:>5}  准备剩余 {q['preparations_remaining']:>5}  重置 {q['resets_at']}")
PY
)"

echo
echo "== 最终版本 =="
survey26 final show --json | python3 -c "$(cat <<'PY'
import json, sys
d = json.load(sys.stdin)["data"]
for k in ("source", "revision_id", "chosen_revision_id", "best_revision_id", "chosen_at", "locked", "frozen_at", "deadline"):
    print(f"{k:20} {d.get(k)}")
PY
)"
