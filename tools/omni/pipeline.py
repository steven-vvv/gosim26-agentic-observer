"""全知排程流水线：结果表 → 初始解 → 滑动窗口 LNS → 全程精确模型，最后导出回放排程。

    uv run python tools/omni/pipeline.py kit/scenarios/dev-fortnight [--init X.npy] [--delta 1000]

- 初始解：给了 --init 就用它，否则取 runs/public_once 里 GPT 版本的科学观测（官方 decisions.csv）。
- 滑动窗口：lns.window_milp（窗口内逐格精确）。
- 全程模型：gmlns.solve（所有天区一起放开，候选限价值 ≥ 当前值 − Δ）；子问题最优即不劣于当前解。
每一步都用 dp.c 精确评估；输出 runs/omni/<场景>/pipeline_best.npy。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP
from gmlns import solve
from lns import window_milp
from model import Scenario

ROOT = Path(__file__).resolve().parents[2]


def initial(sc: Scenario, path: Path | None) -> np.ndarray:
    if path is not None:
        return np.load(path).astype(np.int32)
    decisions = sc.plan_from_decisions_csv(ROOT / "runs" / "public_once" / f"official_{sc.name}" / "decisions.csv")
    log = sc.evaluate(decisions, detail=True)["log"]
    sci = np.full(sc.n_tiles, -1, dtype=np.int32)
    for entry, decision in zip(log, decisions):
        if decision[0] == "observe" and entry[1] == "completed" and sci[decision[1]] < 0:
            sci[decision[1]] = entry[0]
    return sci


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path)
    parser.add_argument("--width", type=int, default=6)
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--delta", type=float, default=30.0)
    parser.add_argument("--time-limit", type=float, default=3600.0)
    parser.add_argument("--threads", type=int, default=3)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cdp = CDP(sc)
    out = ROOT / "runs" / "omni" / sc.name / "pipeline_best.npy"
    sci = initial(sc, args.init)
    best = cdp.evaluate(sci)
    print(f"初始 {best:.6f}", flush=True)
    night = np.repeat(sc.night_of_slot, sc.per)
    nn = int(night.max()) + 1
    for n1 in range(0, nn, args.step):
        n2 = min(nn - 1, n1 + args.width - 1)
        _t, plan = cdp.plan(sci)
        new, status, _dt = window_milp(sc, sci, plan, n1, n2, 600.0)
        if new is not None and (v := cdp.evaluate(new)) > best + 1e-7:
            print(f"  窗口 {n1}-{n2}: {best:.6f} -> {v:.6f}（{status}）", flush=True)
            best, sci = v, new
    np.save(out, sci)
    for _round in range(3):
        _t, plan = cdp.plan(sci)
        new, status, dt = solve(sc, sci, plan, [(0, nn - 1)], args.delta, args.time_limit, threads=args.threads, log=False)
        if new is None:
            print(f"  全程模型：{status}", flush=True)
            break
        v = cdp.evaluate(new)
        print(f"  全程模型（Δ={args.delta}）：{status} {dt:.0f}s，{best:.6f} -> {v:.6f}", flush=True)
        if v <= best + 1e-7:
            break
        best, sci = v, new
        np.save(out, sci)
    print(f"结束 {best:.6f} -> {out}", flush=True)


if __name__ == "__main__":
    main()
