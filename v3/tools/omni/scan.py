"""穷举单块天区的改位：每块天区试所有不冲突、价值损失不超过 --loss 的开始格，精确评估，取最好的改进，反复直到没有改进。

    uv run python tools/omni/scan.py kit/scenarios/dev-reference --init X.npy --out Y.npy [--loss 30] [--pairs]
--pairs 在单块没有改进时，再试“一块改位 + 被挤掉的一块改到当夜最好空位”。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP
from model import COMPLETED, Scenario


def intervals(sc, sci, skip=()):
    return sorted((int(k), int(sc.end[i, k]), i) for i, k in enumerate(sci) if k >= 0 and i not in skip)


def free(iv, a, b):
    return not any(s < b and a < e for s, e, _i in iv)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--loss", type=float, default=30.0)
    parser.add_argument("--pairs", action="store_true")
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cdp = CDP(sc)
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    night = np.repeat(sc.night_of_slot, sc.per)
    sci = np.load(args.init).astype(np.int32)
    best = cdp.evaluate(sci)
    print(f"初始 {best:.6f}", flush=True)
    rounds = 0
    while True:
        rounds += 1
        started = time.time()
        top = None
        evals = 0
        for i in range(sc.n_tiles):
            if sci[i] < 0:
                continue
            cur = int(sci[i])
            iv = intervals(sc, sci, skip={i})
            ks = np.flatnonzero(val[i] >= val[i, cur] - args.loss)
            for k in ks:
                k = int(k)
                if k == cur or not free(iv, k, int(sc.end[i, k])):
                    continue
                trial = sci.copy()
                trial[i] = k
                v = cdp.evaluate(trial)
                evals += 1
                if v > best + 1e-7 and (top is None or v > top[0]):
                    top = (v, i, k)
        print(f"  第 {rounds} 轮单块：评估 {evals} 次，{time.time() - started:.0f}s，"
              f"{f'最好 {top[0]:.6f}（{sc.tile_ids[top[1]]} 挪到格 {top[2]}）' if top else '无改进'}", flush=True)
        if top is None and args.pairs:
            started = time.time()
            evals = 0
            for i in range(sc.n_tiles):
                cur = int(sci[i])
                iv = intervals(sc, sci, skip={i})
                ks = np.flatnonzero(val[i] >= val[i, cur] - args.loss)
                for k in ks:
                    k = int(k)
                    if k == cur:
                        continue
                    e = int(sc.end[i, k])
                    hit = [j for s, ee, j in iv if s < e and k < ee]
                    if len(hit) != 1:
                        continue
                    j = hit[0]
                    trial = sci.copy()
                    trial[i] = k
                    trial[j] = -1
                    iv2 = intervals(sc, trial)
                    nt = int(night[sci[j]])
                    ksj = np.flatnonzero((night == nt) & np.isfinite(val[j]))
                    ksj = ksj[np.argsort(-val[j, ksj])][:60]
                    for kj in ksj:
                        kj = int(kj)
                        if not free(iv2, kj, int(sc.end[j, kj])):
                            continue
                        trial[j] = kj
                        v = cdp.evaluate(trial)
                        evals += 1
                        if v > best + 1e-7 and (top is None or v > top[0]):
                            top = (v, i, k, j, kj)
            print(f"  第 {rounds} 轮成对：评估 {evals} 次，{time.time() - started:.0f}s，"
                  f"{f'最好 {top[0]:.6f}' if top else '无改进'}", flush=True)
        if top is None:
            break
        best = top[0]
        sci[top[1]] = top[2]
        if len(top) > 3:
            sci[top[3]] = top[4]
        np.save(args.out, sci)
    np.save(args.out, sci)
    print(f"结束 {best:.6f}", flush=True)


if __name__ == "__main__":
    main()
