"""多窗口 LNS：几个不相邻的夜窗口里的科学观测一起放开，天区可以换到任一窗口；窗口之间的路径固定。

窗口之间的中间段：
- 固定路径上的每次等待，若开始时存在“固定且未完成、能完成”的天区则必罚；否则只要有一块
  能完成的自由天区此时尚未完成（排在更晚的窗口）就罚。按相同的自由天区集合聚合。
- 中间段用到的自由天区重访要求该天区此前已完成；注定中断要求此时未完成。
- 中间段已有的请求访问按固定计。
窗口内与 lns.window_milp 相同，逐格精确。子问题最优即全局不变差（中间段之后 DP 还会重新填补）。

    uv run python tools/omni/mlns.py kit/scenarios/dev-reference --init X.npy --out Y.npy --sets "0-16,160-179;84-104,128-140"
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from collections import defaultdict
from pathlib import Path

import highspy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP
from flowagg import LP
from model import COMPLETED, TICK, WEATHER, Scenario


def multi_window_milp(sc: Scenario, sci, full_plan, windows, time_limit=300.0, gap=1e-7, log=False, threads=1, delta=None):
    per, n = sc.per, sc.n_ticks
    rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
    night = np.repeat(sc.night_of_slot, per)
    starts = list(np.searchsorted(night, np.arange(int(night.max()) + 1))) + [n]
    spans = sorted((int(starts[a]), int(starts[b + 1])) for a, b in windows)
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        assert b1 <= a2, "窗口重叠"
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)

    def win_of(k):
        for w, (a, b) in enumerate(spans):
            if a <= k < b:
                return w
        return -1

    free = sorted(i for i in range(sc.n_tiles) if sci[i] >= 0 and win_of(int(sci[i])) >= 0)
    fset = set(free)
    fixed_end = {i: (int(sc.end[i, sci[i]]) if sci[i] >= 0 else n + 1) for i in range(sc.n_tiles) if i not in fset}
    L = LP()
    out_arcs, in_arcs = defaultdict(list), defaultdict(list)
    X = {}
    for i in free:
        floor = -np.inf if delta is None else float(val[i, sci[i]]) - delta
        for w, (a, b) in enumerate(spans):
            for k in np.flatnonzero(comp[i, a:b]) + a:
                k = int(k)
                e = int(sc.end[i, k])
                if e > b or (val[i, k] < floor and k != int(sci[i])):
                    continue
                X[(i, k)] = c = L.var(-(float(val[i, k])), integer=True)
                out_arcs[k].append(c)
                in_arcs[e].append(c)
    W = {}
    for a, b in spans:
        for k in range(a, b):
            nb = (k // per + 1) * per
            W[k] = c = L.var(0.0)
            out_arcs[k].append(c)
            in_arcs[nb].append(c)
    Y = []
    for row in sc.requests:
        rid = row["request_id"]
        ra, rb = sc.request_window[rid]
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            for a, b in spans:
                lo, hi = max(ra, a), min(rb, b)
                if lo >= hi:
                    continue
                for k in np.flatnonzero(comp[j, lo:hi] | (sc.outcome[j, lo:hi] == WEATHER)) + lo:
                    k = int(k)
                    e = int(sc.end[j, k])
                    if e > b or (j not in fset and fixed_end[j] > k):
                        continue
                    c = L.var(0.0)
                    Y.append((j, k, rid, c))
                    out_arcs[k].append(c)
                    in_arcs[e].append(c)
    Dm = []
    for a, b in spans:
        for i in range(sc.n_tiles):
            for k in np.flatnonzero(sc.outcome[i, a:b] == WEATHER) + a:
                k = int(k)
                e = int(sc.end[i, k])
                if e > b or (i not in fset and fixed_end[i] <= k):
                    continue
                c = L.var(0.0)
                Dm.append((i, k, c))
                out_arcs[k].append(c)
                in_arcs[e].append(c)
    for a, b in spans:
        for node in range(a, b + 1):
            entries = [(c, 1.0) for c in out_arcs.get(node, [])] + [(c, -1.0) for c in in_arcs.get(node, [])]
            rhs = 1.0 if node == a else (-1.0 if node == b else 0.0)
            # 相邻窗口首尾相接时，节点 b 同时是下一窗口的起点：分开写两行会矛盾，这里合并
            if node == b and any(a2 == b for a2, _b2 in spans):
                continue
            if node == a and any(b0 == a for _a0, b0 in spans):
                rhs = 0.0
            L.row(entries, rhs, rhs)
    by_tile = defaultdict(list)
    for (i, k), c in X.items():
        by_tile[i].append((k, c))
    for i in free:
        L.row([(c, 1.0) for _k, c in by_tile[i]], 1.0, 1.0)
    # 自由天区在每个时刻的完成状态：窗口内链式变量；窗口间用“此前各窗口的 Σx”
    need = defaultdict(set)
    for j, k, _r, _c in Y:
        if j in fset:
            need[j].add(k)
    for i, k, _c in Dm:
        if i in fset:
            need[i].add(k)
    act = sc.actionable
    pen_rows = []
    for a, b in spans:
        for k in range(a, b):
            tiles = np.flatnonzero(act[:, k])
            if not len(tiles):
                continue
            forced = any(i not in fset and fixed_end[i] > k for i in tiles)
            fr = [int(i) for i in tiles if i in fset]
            pen_rows.append((k, forced, fr))
            if not forced:
                for i in fr:
                    need[i].add(k)
    C = {}
    for i in free:
        items = sorted((int(sc.end[i, k]), c) for k, c in by_tile[i])
        prev = None
        pos = 0
        for t in sorted(need.get(i, ())):
            c = L.var(0.0)
            entries = [(c, 1.0)]
            if prev is not None:
                entries.append((prev, -1.0))
            while pos < len(items) and items[pos][0] <= t:
                entries.append((items[pos][1], -1.0))
                pos += 1
            L.row(entries, 0.0, 0.0)
            C[(i, t)] = c
            prev = c

    def done_before(i, t):
        """自由天区 i 在 t（中间段）之前是否完成：此前窗口里的 Σx。"""
        return [(c, 1.0) for k, c in by_tile[i] if int(sc.end[i, k]) <= t]

    for j, k, _r, c in Y:
        if j in fset:
            L.row([(c, 1.0), (C[(j, k)], -1.0)], -np.inf, 0.0)
    for i, k, c in Dm:
        if i in fset:
            L.row([(c, 1.0), (C[(i, k)], 1.0)], -np.inf, 1.0)
    for k, forced, fr in pen_rows:
        nb = (k // per + 1) * per
        pw = L.var(rate * (nb - k))
        if forced:
            L.row([(pw, 1.0), (W[k], -1.0)], 0.0, np.inf)
        else:
            for i in fr:
                L.row([(pw, 1.0), (W[k], -1.0), (C[(i, k)], 1.0)], 0.0, np.inf)
    # 中间段：固定路径
    at = {k: (tile, rid) for k, tile, _p, rid in full_plan}
    outside_visits = defaultdict(set)
    k = 0
    lo_all, hi_all = spans[0][0], spans[-1][1]
    while k < n:
        w = win_of(k)
        if w >= 0:
            k = spans[w][1]
            continue
        if k in at:
            tile, rid = at[k]
            e = int(sc.end[tile, k])
            if rid and comp[tile, k]:
                outside_visits[rid].add(tile)
            if tile in fset and lo_all <= k < hi_all:
                cols = done_before(tile, k)
                if rid:
                    # 重访：此前必须已完成
                    L.row(cols, 1.0, 1.0)
                elif not (comp[tile, k] and sci[tile] == k):
                    # 注定中断：此前必须未完成
                    L.row(cols, 0.0, 0.0) if cols else None
            k = e
            continue
        k = (k // per + 1) * per
    # 中间段的等待罚分：按（段, 自由天区集合）聚合
    mid_rows = defaultdict(float)
    k = 0
    while k < n:
        w = win_of(k)
        if w >= 0:
            k = spans[w][1]
            continue
        if k in at:
            k = int(sc.end[at[k][0], k])
            continue
        nb = (k // per + 1) * per
        if lo_all <= k < hi_all:
            seg = sum(1 for a, _b in spans if a <= k)
            tiles = np.flatnonzero(act[:, k])
            forced = any(i not in fset and fixed_end[i] > k for i in tiles)
            fr = tuple(int(i) for i in tiles if i in fset)
            if not forced and fr:
                mid_rows[(seg, fr)] += rate * (nb - k)
        k = nb
    for (seg, fr), weight in mid_rows.items():
        u = L.var(weight)
        t_ref = spans[seg - 1][1]
        for i in fr:
            L.row([(u, 1.0)] + [(c, 1.0) for c, _v in done_before(i, t_ref)], 1.0, np.inf)
    # 请求
    for row in sc.requests:
        rid = row["request_id"]
        ra, rb = sc.request_window[rid]
        if not any(ra < b and a < rb for a, b in spans):
            continue
        needn = int(row["required_tile_count"])
        z = L.var(-(float(row["completion_reward"]) + float(row["miss_penalty"])), integer=True)
        qs = []
        const = 0
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            if j in outside_visits[rid]:
                const += 1
                continue
            q = L.var(0.0)
            entries = [(q, 1.0)] + [(c, -1.0) for k, c in by_tile.get(j, []) if ra <= k < rb]
            entries += [(c, -1.0) for jj, k, r2, c in Y if jj == j and r2 == rid and comp[j, k]]
            L.row(entries, -np.inf, 0.0)
            qs.append(q)
        L.row([(z, float(needn))] + [(q, -1.0) for q in qs], -np.inf, float(const))
    h = highspy.Highs()
    h.setOptionValue("output_flag", log)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", float(gap))
    h.setOptionValue("threads", threads)
    L.to_highs(h)
    started = time.time()
    h.run()
    status = h.modelStatusToString(h.getModelStatus())
    sol = h.getSolution()
    if not sol.value_valid:
        return None, status, time.time() - started
    xs = np.array(sol.col_value)
    new = sci.copy()
    for (i, k), c in X.items():
        if xs[c] > 0.5:
            new[i] = k
    return new, status, time.time() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sets", type=str, default="", help="分号分隔的窗口组，每组逗号分隔 a-b")
    parser.add_argument("--auto", type=int, default=0, help="自动：把有科学观测的夜聚成块，枚举两两组合（块宽）")
    parser.add_argument("--time-limit", type=float, default=600)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--delta", type=float, default=None, help="自由天区只保留价值不低于当前值减 delta 的开始格")
    parser.add_argument("--promising", type=float, default=None,
                        help="窗口取所有天区“夜内最好价值 ≥ 当前值 − 该值”的夜，合并成块，全部天区一起放开")
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cdp = CDP(sc)
    sci = np.load(args.init).astype(np.int32)
    best = cdp.evaluate(sci)
    print(f"初始 {best:.6f}", flush=True)
    night = np.repeat(sc.night_of_slot, sc.per)
    if args.promising is not None:
        comp = sc.outcome == COMPLETED
        val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
        good = set()
        for i in range(sc.n_tiles):
            cur = val[i, sci[i]]
            ok = np.flatnonzero(val[i] >= cur - args.promising)
            good.update(int(x) for x in np.unique(night[ok]))
        good = sorted(good)
        blocks = []
        for nt in good:
            if blocks and nt == blocks[-1][1] + 1:
                blocks[-1][1] = nt
            else:
                blocks.append([nt, nt])
        sets = [[tuple(b) for b in blocks]]
        print("有希望的夜块：", blocks, flush=True)
    elif args.sets:
        sets = [[tuple(int(x) for x in w.split("-")) for w in group.split(",")] for group in args.sets.split(";")]
    else:
        used = sorted({int(night[k]) for k in sci if k >= 0})
        blocks = []
        for nt in used:
            if blocks and nt - blocks[-1][1] <= args.auto:
                blocks[-1][1] = nt
            else:
                blocks.append([nt, nt])
        blocks = [(max(0, a - 1), min(int(night.max()), b + 1)) for a, b in blocks]
        merged = []
        for a, b in blocks:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        sets = [list(pair) for pair in itertools.combinations(merged, 2)]
        print("窗口块：", merged, flush=True)
    for p in range(args.passes):
        improved = False
        for group in sets:
            _t, plan = cdp.plan(sci)
            new, status, dt = multi_window_milp(sc, sci, plan, group, args.time_limit, delta=args.delta,
                                                threads=args.threads, log=args.threads > 1)
            if new is None:
                print(f"  {group}: {status}（{dt:.0f}s）", flush=True)
                continue
            v = cdp.evaluate(new)
            tag = "改进" if v > best + 1e-7 else "无改进"
            print(f"  {group}: {tag} {v:.6f}（{status}，{dt:.0f}s）", flush=True)
            if v > best + 1e-7:
                best, sci = v, new
                improved = True
                np.save(args.out, sci)
        print(f"第 {p + 1} 轮结束：{best:.6f}", flush=True)
        if not improved:
            break
    np.save(args.out, sci)


if __name__ == "__main__":
    main()
