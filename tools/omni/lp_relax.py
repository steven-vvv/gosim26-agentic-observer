"""聚合流模型的 LP 松弛：给出该候选集下的上界，并报告分数解的规模。

    uv run python tools/omni/lp_relax.py kit/scenarios/dev-reference --dense-nights 6 --dense-delta 40 --solver ipx
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import highspy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flowagg import build, pick_candidates
from model import COMPLETED, Scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--dense-nights", type=int, default=6)
    parser.add_argument("--dense-delta", type=float, default=40.0)
    parser.add_argument("--solver", default="ipx")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--extra", type=Path, nargs="*", default=[], help="额外加入候选的科学观测 .npy")
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    extra = []
    for path in args.extra:
        sci = np.load(path)
        extra += [(i, int(k)) for i, k in enumerate(sci) if k >= 0]
    cand, _ = pick_candidates(sc, val, args.dense_nights, args.dense_delta, extra)
    L, V = build(sc, cand)
    L.integ = [False] * len(L.integ)
    h = highspy.Highs()
    h.setOptionValue("solver", args.solver)
    h.setOptionValue("threads", args.threads)
    h.setOptionValue("time_limit", 5000.0)
    L.to_highs(h)
    started = time.time()
    h.run()
    obj = -h.getInfo().objective_function_value
    const = 1000.0 * int(sc.required.sum())
    miss_all = sum(float(r["miss_penalty"]) for r in sc.requests if r["request_id"] != "RQ0015" or sc.name != "dev-reference")
    # 目标 = 科学 + 1000·REQUIRED + Σ(奖励+漏罚)·满足 − 罚分；换成比赛总分：减去 REQUIRED 常数和漏罚总额
    print(f"LP {h.modelStatusToString(h.getModelStatus())} 目标 {obj:.3f} → 总分上界约 {obj - const - miss_all:.3f}  "
          f"{time.time() - started:.0f}s", flush=True)
    xs = np.array(h.getSolution().col_value)
    frac = sum(1 for c in V["X"].values() if 1e-6 < xs[c] < 1 - 1e-6)
    print("分数 x：", frac, flush=True)
    np.save(Path("runs/omni") / sc.name / "lp_x.npy", xs)
    (Path("runs/omni") / sc.name / "lp_relax.json").write_text(json.dumps({"objective": obj}))


if __name__ == "__main__":
    main()
