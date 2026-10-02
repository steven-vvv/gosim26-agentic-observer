#!/usr/bin/env python3
"""生成线上比赛择优提交用的一组程序包：基础 agent + 若干组温和的参数扰动。

    uv run python tools/build_portfolio.py --agent agents/obs12 --count 30 --out dist/portfolio \
        [--structural '[{"plan_every_seconds": 3600}, {"rollout_seconds": 5400}]']

这是历史的扁平程序包组合器，不是当前完整项目上传入口；完整项目请使用 tools/pack_project.py。

规则按“每队取该阶段最佳的已评分提交”排名，比赛 3 天、每天 10 次，评测天气固定、agent 完全确定。
近似等价的变体会走出不同的轨迹：obs11 的 8 个中性扰动在 4 个场景上的单场相对标准差约 0.15%，
两个场景平均后约 0.10%，从 30 个里取最好一次，期望比单次提交高约 0.2%（runs/noise/）。

扰动只取期望得分不变的参数（规划迭代次数、夜内重规划间隔、推演时长、精算前 K 名），以基础 agent 的默认值为中心
各取三档（默认值从它的 policy.py 读取）；第 1 个包就是基础 agent 本身。--structural 可以再放入几组结构不同、
期望相近的配置（紧随基础版本之后），它们在“取最佳”里与扰动等价，均值还可能更高。挑选扰动时让相邻的包尽量在多个参数上
都不同，轨迹更分散。每个包 = 基础 agent 的源文件（含 requirements.txt）+ 只写改动参数的 params.json；
清单 manifest.csv 记下参数与 sha256。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import itertools
import json
import zipfile
from pathlib import Path

STEPS = {  # 相对默认值的扰动：(0, −, +)
    "plan_iterations": (0, -10, 10),
    "plan_every_seconds": (0, -150, 150),
    "rollout_seconds": (0, -200, 200),
    "refine_top_k": (0, -1, 1),
}
SOURCES = ("main.py", "policy.py", "planner.py", "geometry.py", "protocol.py")
OPTIONAL = ("requirements.txt",)


def agent_defaults(agent: Path) -> dict:
    spec = importlib.util.spec_from_file_location("portfolio_policy", agent / "policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.DEFAULTS)


def variants(count: int, defaults: dict) -> list[dict]:
    """从 3^4 个组合里按“与已选的最少差几个参数”最大的顺序贪心挑选（第 1 个是全默认）。"""
    combos = list(itertools.product(*(range(3) for _ in STEPS)))
    chosen = [combos[0]]
    while len(chosen) < min(count, len(combos)):
        best = max((c for c in combos if c not in chosen),
                   key=lambda c: (min(sum(a != b for a, b in zip(c, s)) for s in chosen), -sum(c), c[::-1]))
        chosen.append(best)
    names = list(STEPS)
    return [{names[i]: defaults[names[i]] + STEPS[names[i]][k] for i, k in enumerate(c) if k} for c in chosen]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--structural", default="[]", help="JSON 列表：放在基础版本之后的结构性配置")
    args = parser.parse_args(argv)
    base_params = json.loads((args.agent / "params.json").read_text(encoding="utf-8")) if (args.agent / "params.json").exists() else {}
    structural = json.loads(args.structural)
    perturbed = variants(args.count - len(structural), {**agent_defaults(args.agent), **base_params})
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, delta in enumerate([perturbed[0], *structural, *perturbed[1:]], start=1):
        params = {**base_params, **delta}
        name = f"{args.agent.name}-v{index:02d}"
        path = args.out / f"{name}.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in SOURCES:
                archive.write(args.agent / source, source)
            for source in OPTIONAL:
                if (args.agent / source).exists():
                    archive.write(args.agent / source, source)
            if params:
                archive.writestr("params.json", json.dumps(params, sort_keys=True) + "\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"index": index, "package": path.name, "params": json.dumps(delta, sort_keys=True) if delta else "（基础版本）",
                     "sha256": digest})
    with (args.out / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["index", "package", "params", "sha256"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(f"{row['index']:2d}  {row['package']:24} {row['params']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
