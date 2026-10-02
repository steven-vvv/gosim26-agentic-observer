#!/usr/bin/env python3
"""汇总蒙特卡洛结果：真实决赛天区目录 × 多个随机天气实现。

    uv run python tools/mc_report.py [--agents base obs10]

每个 mc-a-<种子> / mc-b-<种子> 场景读取 results/<agent>/ 与 results/upper_bound/ 的结果，按 eval-a / eval-b 分组统计，
并给出决赛成绩（两场平均）的分布。两场的天气相互独立，所以两场平均的标准差 = √(σa² + σb²) / 2。
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(kind: str, scenario: str):
    path = ROOT / "results" / kind / f"{scenario}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def describe(values: list[float]) -> str:
    if not values:
        return "—"
    sd = st.stdev(values) if len(values) > 1 else 0.0
    return f"{st.mean(values):9.0f} ± {sd:6.0f}  [{min(values):7.0f}, {max(values):7.0f}]"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agents", nargs="+", default=["base", "obs10"])
    args = parser.parse_args(argv)
    groups = {}
    for prefix, label in (("mc-a", "eval-a"), ("mc-b", "eval-b")):
        scenarios = sorted(p.name for p in (ROOT / "scenarios").glob(f"{prefix}-*"))
        rows = []
        for s in scenarios:
            ub = load("upper_bound", s)
            scores = {a: (load(a, s) or {}).get("total") for a in args.agents}
            if ub is None or any(v is None for v in scores.values()):
                continue
            rows.append({"scenario": s, "upper": ub["upper_bound"], "primal": ub["primal"]["total"], **scores})
        groups[label] = rows
        print(f"== {label}（真实决赛目录）× {len(rows)} 个随机天气   均值 ± 标准差  [最小, 最大]")
        print(f"   完美信息上界      {describe([r['upper'] for r in rows])}")
        print(f"   全知可行排程      {describe([r['primal'] for r in rows])}")
        for a in args.agents:
            print(f"   {a:16}  {describe([r[a] for r in rows])}   占上界 {st.mean(r[a] / r['upper'] for r in rows):.1%}")
        if len(args.agents) >= 2 and rows:
            a0, a1 = args.agents[0], args.agents[-1]
            gains = [(r[a1] / r[a0] - 1) * 100 for r in rows]
            print(f"   同一天气下 {a1} 相对 {a0}：{st.mean(gains):+.2f}%（范围 {min(gains):+.2f}% ~ {max(gains):+.2f}%）")
        print()
    if all(groups.get(k) for k in ("eval-a", "eval-b")):
        print("== 决赛成绩（eval-a 与 eval-b 的平均）")
        for key in ["upper", "primal", *args.agents]:
            a = [r[key] for r in groups["eval-a"]]
            b = [r[key] for r in groups["eval-b"]]
            mean = (st.mean(a) + st.mean(b)) / 2
            sd = math.sqrt((st.stdev(a) ** 2 if len(a) > 1 else 0) + (st.stdev(b) ** 2 if len(b) > 1 else 0)) / 2
            name = {"upper": "完美信息上界", "primal": "全知可行排程"}.get(key, key)
            print(f"   {name:14}  {mean:9.0f} ± {sd:6.0f}   约 95% 区间 [{mean - 2 * sd:7.0f}, {mean + 2 * sd:7.0f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
