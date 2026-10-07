"""List what changed upstream since the commit pinned in ``upstream.lock.json`` (read-only).

Fetches the organisers' clone (never moves its HEAD; contents are read from ``origin/main``) and prints
the commits and files touching the engine (``challenge/``), rules/docs/FAQ/announcements and anything
about the final or hidden cards, plus a stat of the ``challenge/v4_*.py`` diff. Use ``--diff`` to
print the engine diff itself and ``--since-hours`` to list recent commits regardless of path.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

LOCK = Path(__file__).resolve().parent / "upstream.lock.json"
RULE_PATHS = re.compile(
    r"(rules|docs/|faq|announce|final|hidden|runbook|ops/|/cli\.(en|zh)\.md|cli/spec|cli/skill)", re.IGNORECASE
)
WATCH_WORDS = re.compile(
    r"final|hidden|E[–-]H|timing|fair.?clock|cpu|evaluation|concurrent|super board|A1.D1|sophon|quota|\bcap\b|clock",
    re.IGNORECASE,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--diff", action="store_true", help="print the full diff of challenge/v4_*.py")
    parser.add_argument("--since-hours", type=float, default=48.0)
    parser.add_argument("--ref", default="origin/main")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    repo = (LOCK.parents[2] / lock["relative_clone"]).resolve()
    base = lock["commit"]
    if not args.no_fetch:
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], check=True)
    head = git(repo, "rev-parse", "--short", args.ref).strip()
    commits = git(repo, "log", "--format=%h|%cI|%s", f"{base}..{args.ref}").splitlines()
    print(f"# 上游 {base[:7]}..{args.ref}({head}) 共 {len(commits)} 个提交；锁定在 {base[:7]}")
    engine = git(repo, "log", "--format=%h %cI %s", f"{base}..{args.ref}", "--", "challenge/").strip()
    print("\n## 触及 challenge/ 的提交\n" + (engine or "无"))
    print("\n## 引擎文件 diff 统计\n" + (git(repo, "diff", "--stat", base, args.ref, "--", "challenge/").strip() or "无"))
    files = git(repo, "diff", "--name-only", base, args.ref).splitlines()
    print("\n## 规则/文档/决赛相关文件\n" + "\n".join(f for f in files if RULE_PATHS.search(f) and "migrations/" not in f))
    print("\n## 最近提交（标题命中关键词标 *）")
    cutoff = git(repo, "log", "-1", "--format=%ct", args.ref).strip()
    for line in commits:
        sha, when, subject = line.split("|", 2)
        age = (int(cutoff) - int(git(repo, "log", "-1", "--format=%ct", sha).strip())) / 3600
        if age <= args.since_hours:
            print(f"{'*' if WATCH_WORDS.search(subject) else ' '} {sha} {when[5:16]} {subject[:110]}")
    if args.diff:
        print(git(repo, "diff", base, args.ref, "--", "challenge/v4_*.py"))


if __name__ == "__main__":
    main()
