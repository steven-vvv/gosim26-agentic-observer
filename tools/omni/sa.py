"""模拟退火：在科学观测开始格上做局部搜索，每步用 dp.c 精确评估（填补 + 请求补访 + 罚分）。

    uv run python tools/omni/sa.py kit/scenarios/dev-reference --init runs/omni/dev-reference/gpt_sci.npy --hours 1

邻域：
- 平移：同一块天区在当夜前后挪几格
- 换夜：挪到另一夜的好位置（按该天区的候选格）
- 挤占：挪到与别的天区重叠的位置，被挤掉的天区重新放到它自己当夜或别处最好的空位
- 交换：两块天区互换所在的夜
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP
from model import COMPLETED, Scenario


class Search:
    def __init__(self, sc: Scenario, top_per_night: int = 6):
        self.sc = sc
        self.cdp = CDP(sc)
        comp = sc.outcome == COMPLETED
        self.val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
        self.night = np.repeat(sc.night_of_slot, sc.per)
        nn = int(self.night.max()) + 1
        self.nn = nn
        # 每块天区每夜按价值排序的前若干格
        self.cands = []
        self.night_cands = []
        for i in range(sc.n_tiles):
            ok = np.flatnonzero(np.isfinite(self.val[i]))
            per_night = {}
            for nt in np.unique(self.night[ok]):
                ks = ok[self.night[ok] == nt]
                ks = ks[np.argsort(-self.val[i, ks])]
                per_night[int(nt)] = ks
            self.night_cands.append(per_night)
            allk = ok[np.argsort(-self.val[i, ok])]
            self.cands.append(allk)

    def score(self, sci):
        return self.cdp.evaluate(sci)

    def occupied(self, sci, skip=()):
        iv = []
        for i, k in enumerate(sci):
            if k >= 0 and i not in skip:
                iv.append((int(k), int(self.sc.end[i, k]), i))
        iv.sort()
        return iv

    @staticmethod
    def overlaps(iv, a, b):
        return [i for s, e, i in iv if s < b and a < e]

    def best_free(self, i, iv, nights=None, limit=40):
        """天区 i 在给定夜（默认全部）里最好的、不与 iv 重叠的开始格。"""
        best = None
        if nights is None:
            pool = self.cands[i][:4000]
        else:
            pool = np.concatenate([self.night_cands[i].get(nt, np.array([], dtype=np.int64))[:limit] for nt in nights])
        for k in pool:
            e = int(self.sc.end[i, k])
            if not self.overlaps(iv, int(k), e):
                v = self.val[i, k]
                if best is None or v > best[0]:
                    best = (v, int(k))
                    if nights is None:
                        break
        return None if best is None else best[1]

    def propose(self, sci, rng: random.Random):
        sc = self.sc
        new = sci.copy()
        move = rng.random()
        scheduled = [i for i in range(sc.n_tiles) if sci[i] >= 0]
        i = rng.choice(scheduled)
        k0 = int(sci[i])
        nt0 = int(self.night[k0])
        if move < 0.35:
            # 当夜平移
            ks = self.night_cands[i][nt0]
            k = int(ks[min(len(ks) - 1, int(rng.expovariate(1 / 8)))])
            new[i] = k
            return new, "shift"
        if move < 0.7:
            # 换夜（带挤占）
            nights = list(self.night_cands[i].keys())
            nt = rng.choice(nights)
            ks = self.night_cands[i][nt]
            k = int(ks[min(len(ks) - 1, int(rng.expovariate(1 / 4)))])
            new[i] = k
            iv = self.occupied(new, skip={i})
            hit = self.overlaps(iv, k, int(sc.end[i, k]))
            for j in hit:
                new[j] = -1
                iv2 = self.occupied(new)
                kk = self.best_free(j, iv2, nights=[int(self.night[sci[j]])])
                if kk is None:
                    kk = self.best_free(j, iv2)
                if kk is None:
                    return None, "fail"
                new[j] = kk
            return new, "move"
        if move < 0.85:
            # 两块天区换夜
            j = rng.choice(scheduled)
            if j == i:
                return None, "fail"
            ni, nj = nt0, int(self.night[sci[j]])
            new[i] = -1
            new[j] = -1
            iv = self.occupied(new)
            ki = self.best_free(i, iv, nights=[nj])
            if ki is None:
                return None, "fail"
            new[i] = ki
            iv = self.occupied(new)
            kj = self.best_free(j, iv, nights=[ni])
            if kj is None:
                return None, "fail"
            new[j] = kj
            return new, "swap"
        # 挪到全局最好的空位
        new[i] = -1
        iv = self.occupied(new)
        k = self.best_free(i, iv)
        if k is None or k == k0:
            return None, "fail"
        new[i] = k
        return new, "best"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path, required=True, help="科学观测开始格（.npy 或 [[tick, tile]] JSON）")
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--t0", type=float, default=3.0)
    parser.add_argument("--t1", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    S = Search(sc)
    if args.init.suffix == ".npy":
        sci = np.load(args.init).astype(np.int32)
    else:
        sci = np.full(sc.n_tiles, -1, dtype=np.int32)
        for k, i in json.loads(args.init.read_text()):
            sci[i] = k
    cur = S.score(sci)
    best, best_sci = cur, sci.copy()
    print(f"初始 {cur:.6f}", flush=True)
    rng = random.Random(args.seed)
    start = time.time()
    limit = args.hours * 3600
    it = 0
    stats = {}
    out = args.out or Path("runs/omni") / sc.name / f"sa_best_{args.seed}.npy"
    last_save = time.time()
    while time.time() - start < limit:
        it += 1
        frac = (time.time() - start) / limit
        T = args.t0 * (args.t1 / args.t0) ** frac
        new, kind = S.propose(sci, rng)
        if new is None:
            continue
        val = S.score(new)
        if not math.isfinite(val) or val < -1e20:
            continue
        d = val - cur
        acc = stats.setdefault(kind, [0, 0])
        acc[0] += 1
        if d >= 0 or rng.random() < math.exp(d / T):
            sci, cur = new, val
            acc[1] += 1
            if cur > best + 1e-9:
                best, best_sci = cur, sci.copy()
                print(f"  {time.time() - start:7.0f}s it={it} T={T:.3f} 最优 {best:.6f} ({kind})", flush=True)
                if time.time() - last_save > 30:
                    np.save(out, best_sci)
                    last_save = time.time()
    np.save(out, best_sci)
    print(f"结束：{it} 步，最优 {best:.6f}，统计 {stats}", flush=True)


if __name__ == "__main__":
    main()
