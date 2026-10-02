"""随机子集 LNS：每次放开一小组天区（全程、候选限价值不低于当前值 − Δ），其余天区固定；
等待、重访、注定中断在整个时间轴上都是变量（没有固定的中间段）。多个进程通过 --best 文件共享最优解。

    uv run python tools/omni/rlns.py kit/scenarios/dev-reference --init X.npy --best runs/omni/dev-reference/best.npy \
        --seed 1 --size 10 --delta 80 --hours 3
"""
from __future__ import annotations

import argparse
import fcntl
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP
from gmlns import solve
from model import Scenario


def load_best(path: Path, cdp: CDP):
    if not path.exists():
        return None, -np.inf
    with open(str(path) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        sci = np.load(path).astype(np.int32)
    return sci, cdp.evaluate(sci)


def save_best(path: Path, sci, value, cdp: CDP):
    with open(str(path) + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            other = np.load(path).astype(np.int32)
            if cdp.evaluate(other) >= value - 1e-9:
                return False
        tmp = path.with_suffix(".tmp.npy")
        np.save(tmp, sci)
        tmp.replace(path)
    return True


def choose(sc: Scenario, sci, rng: random.Random, size: int, night, groups):
    mode = rng.random()
    scheduled = [i for i in range(sc.n_tiles) if sci[i] >= 0]
    if mode < 0.35:
        return set(rng.sample(scheduled, size)), "random"
    if mode < 0.7:
        # 一两个夜里的天区 + 随机补足
        nights = sorted({int(night[sci[i]]) for i in scheduled})
        pick = set()
        for nt in rng.sample(nights, min(len(nights), rng.choice((1, 2)))):
            members = [i for i in scheduled if int(night[sci[i]]) == nt]
            pick.update(rng.sample(members, min(len(members), size)))
        while len(pick) < size:
            pick.add(rng.choice(scheduled))
        return set(list(pick)[:size + 4]), "night"
    # 罚分组里一起出现的天区
    key = rng.choice(groups)
    pick = set(rng.sample(key, min(len(key), size)))
    while len(pick) < size:
        pick.add(rng.choice(scheduled))
    return pick, "group"


def penalty_groups(sc: Scenario, sci, cdp: CDP):
    _total, plan = cdp.plan(sci)
    per, n = sc.per, sc.n_ticks
    end_of = np.array([sc.end[i, sci[i]] if sci[i] >= 0 else n + 1 for i in range(sc.n_tiles)])
    at = {k: t for k, t, _p, _r in plan}
    groups = defaultdict(float)
    k = 0
    while k < n:
        if k in at:
            k = int(sc.end[at[k], k])
            continue
        b = (k // per + 1) * per
        inc = tuple(i for i in range(sc.n_tiles) if end_of[i] > k and sc.actionable[i, k])
        if inc:
            groups[inc] += b - k
        k = b
    return [list(g) for g in groups]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--best", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--delta", type=float, default=80.0)
    parser.add_argument("--time-limit", type=float, default=300.0)
    parser.add_argument("--hours", type=float, default=3.0)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cdp = CDP(sc)
    night = np.repeat(sc.night_of_slot, sc.per)
    sci = np.load(args.init).astype(np.int32)
    cur = cdp.evaluate(sci)
    other, ov = load_best(args.best, cdp)
    if other is not None and ov > cur:
        sci, cur = other, ov
    print(f"[{args.seed}] 初始 {cur:.6f}", flush=True)
    rng = random.Random(args.seed)
    start = time.time()
    groups = penalty_groups(sc, sci, cdp)
    it = 0
    while time.time() - start < args.hours * 3600:
        it += 1
        other, ov = load_best(args.best, cdp)
        if other is not None and ov > cur + 1e-9:
            sci, cur = other, ov
            groups = penalty_groups(sc, sci, cdp)
            print(f"[{args.seed}] 载入共享最优 {cur:.6f}", flush=True)
        subset, mode = choose(sc, sci, rng, args.size, night, groups)
        _t, plan = cdp.plan(sci)
        t0 = time.time()
        new, status, _dt = solve(sc, sci, plan, [(0, int(night.max()))], args.delta, args.time_limit,
                                 threads=1, log=False, free_tiles=subset)
        if new is None:
            print(f"[{args.seed}] {it} {mode} {status}", flush=True)
            continue
        v = cdp.evaluate(new)
        tag = ""
        if v > cur + 1e-7:
            sci, cur = new, v
            tag = "  ← 改进"
            if save_best(args.best, sci, cur, cdp):
                tag += "（写入共享）"
            groups = penalty_groups(sc, sci, cdp)
        print(f"[{args.seed}] {it} {mode} |S|={len(subset)} {status} {time.time() - t0:.0f}s -> {v:.6f}{tag}", flush=True)
    print(f"[{args.seed}] 结束 {cur:.6f}", flush=True)


if __name__ == "__main__":
    main()
