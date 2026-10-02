"""时间窗大邻域搜索：放开若干连续夜里的科学观测，用逐格精确的流模型 MILP 重排，其余固定。

窗口 [t1, t2) 取夜的边界（游标必然经过每夜的第一格）。窗口内：
- 自由天区（当前科学观测在窗口内）可以放到窗口内任意可完成的开始格
- 固定天区的完成状态已知：窗口前完成的算完成，窗口后才观测的算未完成
- 等待罚分、重访合法性、注定中断合法性都逐格精确；请求按窗口外已有访问 + 窗口内访问计
子问题最优时，全局解不会变差（窗口外路径不变，自由天区的完成状态在窗口外也不变）。
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

import highspy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flowagg import LP
from model import COMPLETED, TICK, WEATHER, Scenario


def window_milp(sc: Scenario, sci: np.ndarray, full_plan, n1: int, n2: int, time_limit=120.0, gap=1e-7,
                extra_free=(), log=False):
    """返回 (新的 sci, 子问题目标改进量估计, 状态)。full_plan 为当前完整计划 [(tick, tile, program, rid)]。"""
    per, n = sc.per, sc.n_ticks
    rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
    night = np.repeat(sc.night_of_slot, per)
    starts = np.searchsorted(night, np.arange(int(night.max()) + 2))
    t1, t2 = int(starts[n1]), int(starts[n2 + 1]) if n2 + 1 < len(starts) else n
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    free = sorted({i for i in range(sc.n_tiles) if sci[i] >= 0 and t1 <= sci[i] < t2} | set(extra_free))
    fixed_end = {i: (int(sc.end[i, sci[i]]) if sci[i] >= 0 else n + 1) for i in range(sc.n_tiles) if i not in free}
    L = LP()
    out_arcs, in_arcs = defaultdict(list), defaultdict(list)
    X = {}
    for i in free:
        ks = np.flatnonzero(comp[i, t1:t2]) + t1
        for k in ks:
            k = int(k)
            e = int(sc.end[i, k])
            if e > t2:
                continue
            X[(i, k)] = c = L.var(-(float(val[i, k])), integer=True)
            out_arcs[k].append(c)
            in_arcs[e].append(c)
    W = {}
    for k in range(t1, t2):
        b = (k // per + 1) * per
        W[k] = c = L.var(0.0)
        out_arcs[k].append(c)
        in_arcs[b].append(c)
    Y = []
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        lo, hi = max(a, t1), min(b, t2)
        if lo >= hi:
            continue
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            for k in np.flatnonzero(comp[j, lo:hi] | (sc.outcome[j, lo:hi] == WEATHER)) + lo:
                k = int(k)
                e = int(sc.end[j, k])
                if e > t2:
                    continue
                if j not in free and fixed_end[j] > k:
                    continue
                c = L.var(0.0)
                Y.append((j, k, rid, c))
                out_arcs[k].append(c)
                in_arcs[e].append(c)
    Dm = []
    for i in range(sc.n_tiles):
        ks = np.flatnonzero(sc.outcome[i, t1:t2] == WEATHER) + t1
        for k in ks:
            k = int(k)
            e = int(sc.end[i, k])
            if e > t2:
                continue
            if i not in free and fixed_end[i] <= k:
                continue
            c = L.var(0.0)
            Dm.append((i, k, c))
            out_arcs[k].append(c)
            in_arcs[e].append(c)
    for node in range(t1, t2 + 1):
        entries = [(c, 1.0) for c in out_arcs.get(node, [])] + [(c, -1.0) for c in in_arcs.get(node, [])]
        rhs = 1.0 if node == t1 else (-1.0 if node == t2 else 0.0)
        L.row(entries, rhs, rhs)
    by_tile = defaultdict(list)
    for (i, k), c in X.items():
        by_tile[i].append((k, c))
    for i in free:
        # 自由天区必须在窗口内完成一次（保持窗口外状态不变）
        L.row([(c, 1.0) for _k, c in by_tile.get(i, [])], 1.0, 1.0)
    # 自由天区的完成链
    need = defaultdict(set)
    for j, k, _rid, _c in Y:
        if j in free:
            need[j].add(k)
    for i, k, _c in Dm:
        if i in free:
            need[i].add(k)
    act = sc.actionable
    pen_rows = []
    for k in range(t1, t2):
        tiles = np.flatnonzero(act[:, k])
        if not len(tiles):
            continue
        forced = any(i not in free and fixed_end[i] > k for i in tiles)
        fr = [int(i) for i in tiles if i in free]
        pen_rows.append((k, forced, fr))
        if not forced:
            for i in fr:
                need[i].add(k)
    C = {}
    for i in free:
        items = sorted((int(sc.end[i, k]), c) for k, c in by_tile.get(i, []))
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
    for j, k, _rid, c in Y:
        if j in free:
            L.row([(c, 1.0), (C[(j, k)], -1.0)], -np.inf, 0.0)
    for i, k, c in Dm:
        if i in free:
            L.row([(c, 1.0), (C[(i, k)], 1.0)], -np.inf, 1.0)
    for k, forced, fr in pen_rows:
        b = (k // per + 1) * per
        pw = L.var(rate * (b - k))
        if forced:
            L.row([(pw, 1.0), (W[k], -1.0)], 0.0, np.inf)
        else:
            for i in fr:
                L.row([(pw, 1.0), (W[k], -1.0), (C[(i, k)], 1.0)], 0.0, np.inf)
    # 请求：窗口外已有的带标签访问
    outside = defaultdict(set)
    for k, tile, _p, rid in full_plan:
        if rid and comp[tile, k] and not (t1 <= k < t2):
            outside[rid].add(tile)
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        needn = int(row["required_tile_count"])
        if b <= t1 or a >= t2:
            continue
        value = float(row["completion_reward"]) + float(row["miss_penalty"])
        z = L.var(-value, integer=True)
        qs = []
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            if j in outside[rid]:
                qs.append(None)
                continue
            q = L.var(0.0)
            entries = [(q, 1.0)] + [(c, -1.0) for k, c in by_tile.get(j, []) if a <= k < b]
            entries += [(c, -1.0) for jj, k, r2, c in Y if jj == j and r2 == rid and comp[j, k]]
            L.row(entries, -np.inf, 0.0)
            qs.append(q)
        const = sum(1 for q in qs if q is None)
        L.row([(z, float(needn))] + [(q, -1.0) for q in qs if q is not None], -np.inf, float(const))
    h = highspy.Highs()
    h.setOptionValue("output_flag", log)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", float(gap))
    h.setOptionValue("threads", 1)
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


def run_lns(sc: Scenario, sci, cdp, windows, time_limit=120.0, log=print):
    best = cdp.evaluate(sci)
    _total, plan = cdp.plan(sci)
    for n1, n2 in windows:
        new, status, dt = window_milp(sc, sci, plan, n1, n2, time_limit)
        if new is None:
            log(f"  夜 {n1}-{n2}: {status}（{dt:.0f}s）无解")
            continue
        val = cdp.evaluate(new)
        if val > best + 1e-7:
            log(f"  夜 {n1}-{n2}: {best:.6f} -> {val:.6f}（{status}，{dt:.0f}s）")
            best, sci = val, new
            _total, plan = cdp.plan(sci)
        else:
            log(f"  夜 {n1}-{n2}: 无改进 {val:.6f}（{status}，{dt:.0f}s）")
    return sci, best
