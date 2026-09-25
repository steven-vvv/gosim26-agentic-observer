#!/usr/bin/env python3
"""汇总 results/ 下各版本相对官方基线的提升，输出 Markdown 表。

    uv run python tools/summary.py [agent ...]      # 不给 agent 时列出所有有结果的版本

开发集与留出集的划分见 README；只统计该版本在集合内每个场景都有结果的情况。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV = ["comp-a-s101", "comp-a-s102", "comp-a-s104", "comp-a-s105", "comp-b-s201", "comp-b-s202", "comp-b-s204", "comp-b-s205"]
HOLDOUT = ["comp-a-s103", "comp-b-s203", "comp-a-s106", "comp-a-s107", "comp-b-s206", "comp-b-s207"]
PRACTICE = ["dev-reference", "demo-week", "finals-preview"]


def load(agent: str, scenario: str):
    path = ROOT / "results" / agent / f"{scenario}.json"
    return json.loads(path.read_text(encoding="utf-8")).get("total") if path.exists() else None  # 上界等结果没有 total


def gains(agent: str, scenarios: list[str]):
    values = []
    for s in scenarios:
        base, total = load("base", s), load(agent, s)
        if base is None or total is None:
            return None
        values.append((total / base - 1) * 100)
    return values


def main(agents: list[str]) -> None:
    if not agents:
        agents = sorted(p.name for p in (ROOT / "results").iterdir() if p.is_dir() and p.name != "base")
    print("| 版本 | 开发集 8 场均值 | 留出集 6 场均值 | 最差一场 | " + " | ".join(PRACTICE) + " |")
    print("|---|---|---|---|" + "---|" * len(PRACTICE))
    for agent in agents:
        dev, hold = gains(agent, DEV), gains(agent, HOLDOUT)
        if dev is None and hold is None:
            continue
        worst = min((dev or []) + (hold or [])) if (dev or hold) else None
        cells = [f"{sum(v) / len(v):+.2f}%" if v else "—" for v in (dev, hold)]
        cells.append(f"{worst:+.2f}%" if worst is not None else "—")
        for s in PRACTICE:
            g = gains(agent, [s])
            cells.append(f"{g[0]:+.2f}%" if g else "—")
        print(f"| {agent} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main(sys.argv[1:])
