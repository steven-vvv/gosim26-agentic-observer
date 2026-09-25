#!/usr/bin/env python3
"""构造总分约为 0 的结果文件（Playground 上榜用，不透露策略信息）。

    uv run python tools/zero_score.py --scenario kit/scenarios/dev-fortnight \
        --decisions runs/zero/base-dev-fortnight/decisions.csv --out dist/playground/dev-fortnight-zero.csv

1. 粗调：取一份完整决策序列（官方基线的）的前 N 条、之后结束文件。N 越大拍得越多，总分从负变正；二分找到刚好越过 0 的 N。
2. 细调：把若干次观测的项目档位换成与其全部分段档位都不匹配的档位——该次项目加成变为 0，时间轴、基础分与请求都不变。
   用折半枚举求一个加成子集，使其和最接近且不超过剩余正分；报告里的数值只有 6 位小数，所以再用新报告补一轮。
用到的都是正常决策（提前结束、档位不匹配），没有利用评分器缺陷。打分直接调用平台同款的 score_files。
"""
from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))

from challenge.scoring_core import score_files  # 平台同款评分入口

PROGRAMS = ("DARK", "BRIGHT", "BACKUP")


def read_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return header, [list(row) for row in reader]


def write_rows(path: Path, header, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def score(scenario: Path, header, rows, work: Path) -> dict:
    decisions, report = work / "decisions.csv", work / "score_report.json"
    write_rows(decisions, header, rows)
    return score_files(scenario, decisions, report, "trace_complete")


def best_subset(values: np.ndarray, target: float) -> tuple[list[int], float]:
    """折半枚举：返回和不超过 target 且最接近它的子集下标，以及剩余量 target − 和。"""
    half = len(values) // 2
    left, right = values[:half], values[half:]

    def sums(part):
        out = np.zeros(1)
        for v in part:
            out = np.concatenate([out, out + v])
        return out  # 第 m 个元素对应子集掩码 m（二进制第 k 位 = 第 k 个元素）

    a, b = sums(left), sums(right)
    order = np.argsort(b)
    b_sorted = b[order]
    pos = np.searchsorted(b_sorted, target - a, side="right") - 1
    ok = pos >= 0
    residual = np.where(ok, target - a - b_sorted[np.maximum(pos, 0)], np.inf)
    i = int(residual.argmin())
    j = int(order[pos[i]])
    chosen = [k for k in range(half) if i >> k & 1] + [half + k for k in range(len(right)) if j >> k & 1]
    return chosen, float(residual[i])


def zero_out(scenario: Path, header, rows, work: Path, rounds: int = 3, pool: int = 40):
    col = {name: header.index(name) for name in header}
    for _ in range(rounds):
        report = score(scenario, header, rows, work)
        total = float(report["score"]["total"])
        if abs(total) < 1e-6:
            break
        by_id = {row[col["decision_id"]]: row for row in rows}
        candidates = []
        for action in report["actions"]:
            bonus = float(action.get("program_bonus_score") or 0.0)
            bands = {seg["quality_band"] for seg in action.get("segments") or []}
            spare = [p for p in PROGRAMS if p not in bands]
            if action.get("outcome") == "completed" and bonus > 0 and spare and action["decision_id"] in by_id:
                candidates.append((bonus, action["decision_id"], spare[0]))
        if total < 0 or not candidates:
            break
        candidates.sort(reverse=True)
        chosen, _residual = best_subset(np.array([c[0] for c in candidates[:pool]]), total)
        for k in chosen:
            _bonus, decision_id, program = candidates[k]
            by_id[decision_id][col["program"]] = program
    report = score(scenario, header, rows, work)
    return rows, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True, help="一份完整的决策序列（例如官方基线的 decisions.csv）")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    scenario = args.scenario.resolve()
    header, rows = read_rows(args.decisions)
    col = {name: header.index(name) for name in header}
    for row in rows:  # 理由字段不保留任何原始说明
        row[col["reason"]] = row[col["action"]]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        total = lambda n: float(score(scenario, header, rows[:n], work)["score"]["total"])
        lo, hi = 0, len(rows)
        if total(hi) < 0:
            raise SystemExit("完整序列的总分仍为负，无法越过 0")
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if total(mid) >= 0:
                hi = mid
            else:
                lo = mid
        prefix = [list(row) for row in rows[:hi]]
        before = total(hi)
        prefix, report = zero_out(scenario, header, prefix, work)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_rows(args.out, header, prefix)
    sc = report["score"]
    print(f"{args.out}: 前 {hi} 条决策（完整序列 {len(rows)} 条），粗调后 {before:.6f}，细调后总分 {sc['total']:.6f}")
    print(f"  基础分 {sc['base_science']:.3f}  加成 {sc['program_bonus']:.3f}  请求 {sc['request_reward']:.3f}  "
          f"罚分 { {k: v for k, v in sc['penalties'].items() if v} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
