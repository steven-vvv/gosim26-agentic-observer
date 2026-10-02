"""跨夜成对交换实测：把超级夜（默认第 171 夜）里的每块科学观测挪到相邻夜的任意可完成格，
同时把在该夜之外完成、但在该夜价值更高的天区挪进来；用 cdp 精确评估（填补、请求补访自动重排）。

    .venv/bin/python tools/omni/audit/swap171.py kit/scenarios/dev-reference --night 171 --top 10
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]  # tools/omni/audit -> 仓库根目录
sys.path.insert(0, str(ROOT / "tools" / "omni"))
from cdp import CDP
from model import COMPLETED, Scenario, format_utc


def intervals(sc, sci, skip=()):
    return [(int(k), int(sc.end[i, k])) for i, k in enumerate(sci) if k >= 0 and i not in skip]


def free(iv, a, b):
    return not any(s < b and a < e for s, e in iv)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path, default=ROOT / "runs/omni/dev-reference/best.npy")
    parser.add_argument("--night", type=int, default=171)
    parser.add_argument("--span", type=int, default=3, help="j 可挪到 night±span 的夜")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--global-top", type=int, default=0,
                        help="若 >0，j 的目的地改为全程（所有其它夜）价值最高的前 N 个不冲突格，不再按相邻夜取")
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cdp = CDP(sc)
    val = np.where(sc.outcome == COMPLETED, sc.base + sc.bonus.max(axis=2), -np.inf)
    night = np.repeat(sc.night_of_slot, sc.per)
    sci = np.load(args.init).astype(np.int32)
    base = cdp.evaluate(sci)
    print(f"初始 {base:.6f}")
    N = args.night
    in_night = np.flatnonzero(night == N)
    lo, hi = int(in_night[0]), int(in_night[-1]) + 1
    J = [i for i in range(sc.n_tiles) if night[sci[i]] == N]
    cur = np.array([val[i, sci[i]] for i in range(sc.n_tiles)])
    I_in = [i for i in range(sc.n_tiles) if night[sci[i]] != N and val[i, lo:hi].max() > cur[i]]
    print(f"夜 {N}: 夜内科学观测 {len(J)} 块；候选挪入 {len(I_in)} 块 "
          f"{[(sc.tile_ids[i], round(float(val[i, lo:hi].max() - cur[i]), 1)) for i in I_in]}")
    other_nights = [n for n in range(N - args.span, N + args.span + 1) if n != N and 0 <= n <= night.max()]
    best = (base, None)
    evals = 0
    started = time.time()
    results = []
    for j in J:
        trial0 = sci.copy()
        trial0[j] = -1
        iv0 = intervals(sc, trial0)
        # j 的新位置：相邻夜里价值最高的不冲突格
        cand_j = []
        if args.global_top > 0:
            ks = np.flatnonzero((night != N) & np.isfinite(val[j]))
            ks = ks[np.argsort(-val[j, ks])]
            for k in ks:
                k = int(k)
                if free(iv0, k, int(sc.end[j, k])):
                    cand_j.append((k, float(val[j, k])))
                    if len(cand_j) >= args.global_top:
                        break
        for n in (() if args.global_top > 0 else other_nights):
            ks = np.flatnonzero((night == n) & np.isfinite(val[j]))
            ks = ks[np.argsort(-val[j, ks])]
            got = 0
            for k in ks:
                k = int(k)
                if free(iv0, k, int(sc.end[j, k])):
                    cand_j.append((k, float(val[j, k])))
                    got += 1
                    if got >= args.top:
                        break
        for i in I_in:
            # i 挪入夜 N：在 j 已移出的时间轴上，价值高于当前的不冲突格
            ks = np.flatnonzero(np.isfinite(val[i, lo:hi]) & (val[i, lo:hi] > cur[i])) + lo
            ks = ks[np.argsort(-val[i, ks])]
            iv1 = intervals(sc, trial0, skip={i})
            cand_i = []
            for k in ks:
                k = int(k)
                if free(iv1, k, int(sc.end[i, k])):
                    cand_i.append((k, float(val[i, k])))
                    if len(cand_i) >= args.top:
                        break
            for ki, vi in cand_i:
                trial1 = trial0.copy()
                trial1[i] = ki
                iv2 = intervals(sc, trial1)
                for kj, vj in cand_j:
                    if not free(iv2, kj, int(sc.end[j, kj])):
                        continue
                    trial = trial1.copy()
                    trial[j] = kj
                    v = cdp.evaluate(trial)
                    evals += 1
                    results.append((v, j, kj, i, ki))
                    if v > best[0] + 1e-7:
                        best = (v, trial.copy())
                        print(f"  改进 {v:.6f}: {sc.tile_ids[j]} -> 夜 {int(night[kj])} 格 {kj} ({vj:.1f}), "
                              f"{sc.tile_ids[i]} -> 格 {ki} ({vi:.1f})", flush=True)
    results.sort(key=lambda r: -r[0])
    print(f"评估 {evals} 次，{time.time() - started:.0f}s；最好 {best[0]:.6f}（初始 {base:.6f}）")
    for v, j, kj, i, ki in results[:8]:
        print(f"   {v:.6f}  {sc.tile_ids[j]}->夜{int(night[kj])}@{format_utc(sc.tick_time[kj])} "
              f"{sc.tile_ids[i]}->@{format_utc(sc.tick_time[ki])}  Δ={v - base:+.3f}")
    if best[1] is not None:
        np.save(Path(__file__).with_name(f"swap{N}_best.npy"), best[1])


if __name__ == "__main__":
    main()
