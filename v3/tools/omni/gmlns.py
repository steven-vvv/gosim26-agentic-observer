"""近全局的多窗口 LNS：所有天区一起放开，只在“有希望的夜”（价值不低于当前值 − Δ）上放候选。

与 mlns.py 相同的精确建模（窗口内逐格，窗口之间路径固定），另加确定性剪枝：
天区在其最早候选结束之前必未完成、在最晚候选结束之后必已完成，只有两者之间的时刻才需要
完成状态变量与逐天区的罚分约束。

    uv run python tools/omni/gmlns.py kit/scenarios/dev-reference --init X.npy --out Y.npy --delta 10 --threads 3
"""
from __future__ import annotations

import argparse
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


def promising_blocks(sc, sci, val, delta, pad=0, extra_nights=()):
    night = np.repeat(sc.night_of_slot, sc.per)
    good = set(extra_nights)
    for i in range(sc.n_tiles):
        if sci[i] < 0:
            continue
        ok = np.flatnonzero(val[i] >= val[i, sci[i]] - delta)
        good.update(int(x) for x in np.unique(night[ok]))
        good.add(int(night[sci[i]]))
    nmax = int(night.max())
    padded = set()
    for nt in good:
        padded.update(range(max(0, nt - pad), min(nmax, nt + pad) + 1))
    blocks = []
    for nt in sorted(padded):
        if blocks and nt == blocks[-1][1] + 1:
            blocks[-1][1] = nt
        else:
            blocks.append([nt, nt])
    return [tuple(b) for b in blocks]


def solve(sc: Scenario, sci, full_plan, windows, delta, time_limit=1800.0, gap=1e-7, threads=1, log=True,
          free_tiles=None):
    per, n = sc.per, sc.n_ticks
    rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
    night = np.repeat(sc.night_of_slot, per)
    starts = list(np.searchsorted(night, np.arange(int(night.max()) + 1))) + [n]
    spans = sorted((int(starts[a]), int(starts[b + 1])) for a, b in windows)
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    win_id = np.full(n, -1, dtype=np.int32)
    for w, (a, b) in enumerate(spans):
        win_id[a:b] = w
    if free_tiles is None:
        free = [i for i in range(sc.n_tiles) if sci[i] >= 0 and win_id[sci[i]] >= 0]
    else:
        free = sorted(free_tiles)
    fset = set(free)
    fixed_end = {i: (int(sc.end[i, sci[i]]) if sci[i] >= 0 else n + 1) for i in range(sc.n_tiles) if i not in fset}
    L = LP()
    out_arcs, in_arcs = defaultdict(list), defaultdict(list)
    X = {}
    by_tile = defaultdict(list)
    for i in free:
        floor = float(val[i, sci[i]]) - delta
        cur = int(sci[i])
        ks = np.flatnonzero((win_id >= 0) & (val[i] >= floor))
        if cur not in set(ks.tolist()):
            ks = np.append(ks, cur)
        for k in ks:
            k = int(k)
            e = int(sc.end[i, k])
            w = win_id[k]
            if w < 0 or e > spans[w][1]:
                continue
            X[(i, k)] = c = L.var(-(float(val[i, k])), integer=True)
            by_tile[i].append((k, c))
            out_arcs[k].append(c)
            in_arcs[e].append(c)
    # 窗口内的固定天区：科学观测作为必选弧（占住时间轴）
    for i in range(sc.n_tiles):
        if i in fset or sci[i] < 0:
            continue
        k = int(sci[i])
        w = win_id[k]
        if w < 0:
            continue
        c = L.var(0.0, lb=1.0, ub=1.0)
        out_arcs[k].append(c)
        in_arcs[int(sc.end[i, k])].append(c)
    first_end = {i: min(int(sc.end[i, k]) for k, _c in by_tile[i]) for i in free}
    last_end = {i: max(int(sc.end[i, k]) for k, _c in by_tile[i]) for i in free}

    def status(i, t):
        """0 必未完成，1 必已完成，None 不确定。"""
        if i not in fset:
            return 1 if fixed_end[i] <= t else 0
        if t < first_end[i]:
            return 0
        if t >= last_end[i]:
            return 1
        return None

    Wv = {}
    for a, b in spans:
        for k in range(a, b):
            nb = (k // per + 1) * per
            Wv[k] = c = L.var(0.0)
            out_arcs[k].append(c)
            in_arcs[nb].append(c)
    need = defaultdict(set)
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
                    st = status(j, k)
                    if e > b or st == 0:
                        continue
                    c = L.var(0.0)
                    Y.append((j, k, rid, c, st))
                    out_arcs[k].append(c)
                    in_arcs[e].append(c)
                    if st is None:
                        need[j].add(k)
    Dm = []
    for a, b in spans:
        for i in range(sc.n_tiles):
            for k in np.flatnonzero(sc.outcome[i, a:b] == WEATHER) + a:
                k = int(k)
                e = int(sc.end[i, k])
                st = status(i, k)
                if e > b or st == 1:
                    continue
                c = L.var(0.0)
                Dm.append((i, k, c, st))
                out_arcs[k].append(c)
                in_arcs[e].append(c)
                if st is None:
                    need[i].add(k)
    for a, b in spans:
        for node in range(a, b + 1):
            if node == b and any(a2 == b for a2, _b2 in spans):
                continue
            entries = [(c, 1.0) for c in out_arcs.get(node, [])] + [(c, -1.0) for c in in_arcs.get(node, [])]
            rhs = 1.0 if node == a else (-1.0 if node == b else 0.0)
            if node == a and any(b0 == a for _a0, b0 in spans):
                rhs = 0.0
            L.row(entries, rhs, rhs)
    for i in free:
        L.row([(c, 1.0) for _k, c in by_tile[i]], 1.0, 1.0)
    act = sc.actionable
    pen_rows = []
    for a, b in spans:
        for k in range(a, b):
            tiles = np.flatnonzero(act[:, k])
            if not len(tiles):
                continue
            sts = [(int(i), status(int(i), k)) for i in tiles]
            forced = any(st == 0 for _i, st in sts)
            unc = [i for i, st in sts if st is None]
            if forced:
                pen_rows.append((k, True, []))
            elif unc:
                pen_rows.append((k, False, unc))
                for i in unc:
                    need[i].add(k)
    # 中间段：固定路径上的等待与动作
    at = {k: (tile, rid) for k, tile, _p, rid in full_plan}
    outside_visits = defaultdict(set)
    mid_rows = defaultdict(float)
    mid_fix = []  # (tile, t, required_status)
    k = 0
    lo_all, hi_all = spans[0][0], spans[-1][1]
    while k < n:
        w = win_id[k] if k < n else -1
        if w >= 0:
            k = spans[w][1]
            continue
        if k in at:
            tile, rid = at[k]
            e = int(sc.end[tile, k])
            if rid and comp[tile, k]:
                outside_visits[rid].add(tile)
            if tile in fset:
                st = status(tile, k)
                if rid:
                    if st == 0:
                        return None, "infeasible-mid-revisit", 0.0
                    if st is None:
                        mid_fix.append((tile, k, 1))
                        need[tile].add(k)
                elif not comp[tile, k] or sci[tile] != k:
                    if st == 1:
                        return None, "infeasible-mid-doomed", 0.0
                    if st is None:
                        mid_fix.append((tile, k, 0))
                        need[tile].add(k)
            k = e
            continue
        nb = (k // per + 1) * per
        if lo_all <= k < hi_all:
            tiles = np.flatnonzero(act[:, k])
            sts = [(int(i), status(int(i), k)) for i in tiles]
            if not any(st == 0 for _i, st in sts):
                unc = tuple(i for i, st in sts if st is None)
                if unc:
                    mid_rows[(k, unc)] += rate * (nb - k)
        k = nb
    # 完成状态链：C_i(t) = Σ_{x: end ≤ t} x
    C = {}
    for i, ticks in need.items():
        items = sorted((int(sc.end[i, kk]), c) for kk, c in by_tile[i])
        prev = None
        pos = 0
        for t in sorted(ticks):
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
    for j, k, _r, c, st in Y:
        if st is None:
            L.row([(c, 1.0), (C[(j, k)], -1.0)], -np.inf, 0.0)
    for i, k, c, st in Dm:
        if st is None:
            L.row([(c, 1.0), (C[(i, k)], 1.0)], -np.inf, 1.0)
    for tile, t, req in mid_fix:
        L.row([(C[(tile, t)], 1.0)], float(req), float(req))
    for k, forced, unc in pen_rows:
        nb = (k // per + 1) * per
        pw = L.var(rate * (nb - k))
        if forced:
            L.row([(pw, 1.0), (Wv[k], -1.0)], 0.0, np.inf)
        else:
            for i in unc:
                L.row([(pw, 1.0), (Wv[k], -1.0), (C[(i, k)], 1.0)], 0.0, np.inf)
    # 中间段罚分：同一段内状态不变，按段起点（前一窗口结束）聚合
    seg_rows = defaultdict(float)
    for (k, unc), weight in mid_rows.items():
        t_ref = max(b for _a, b in spans if b <= k)
        seg_rows[(t_ref, unc)] += weight
    for (t_ref, unc), weight in seg_rows.items():
        u = L.var(weight)
        for i in unc:
            if (i, t_ref) not in C:
                # 需要段起点的状态变量：补一条链变量（直接用求和式）
                cols = [(c, 1.0) for kk, c in by_tile[i] if int(sc.end[i, kk]) <= t_ref]
                L.row([(u, 1.0)] + cols, 1.0, np.inf)
            else:
                L.row([(u, 1.0), (C[(i, t_ref)], 1.0)], 1.0, np.inf)
    Z, Q = {}, {}
    for row in sc.requests:
        rid = row["request_id"]
        ra, rb = sc.request_window[rid]
        if not any(ra < b and a < rb for a, b in spans):
            continue
        needn = int(row["required_tile_count"])
        z = L.var(-(float(row["completion_reward"]) + float(row["miss_penalty"])), integer=True)
        Z[rid] = z
        qs = []
        const = 0
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            if j in outside_visits[rid]:
                const += 1
                continue
            q = L.var(0.0)
            entries = [(q, 1.0)] + [(c, -1.0) for kk, c in by_tile.get(j, []) if ra <= kk < rb]
            entries += [(c, -1.0) for jj, kk, r2, c, _st in Y if jj == j and r2 == rid and comp[j, kk]]
            L.row(entries, -np.inf, 0.0)
            qs.append(q)
            Q[(rid, j)] = q
        L.row([(z, float(needn))] + [(q, -1.0) for q in qs], -np.inf, float(const))
    if log:
        print(f"  自由 {len(free)}，候选 {len(X)}，链 {len(C)}，罚分行 {len(pen_rows)}，中间段组 {len(seg_rows)}；"
              f"{len(L.cost)} 变量 {len(L.rlo)} 约束 {len(L.val)} 非零", flush=True)
    h = highspy.Highs()
    h.setOptionValue("output_flag", log)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", float(gap))
    h.setOptionValue("threads", threads)
    L.to_highs(h)
    # 热启动：当前解
    x0 = np.zeros(len(L.cost))
    for i in free:
        x0[X[(i, int(sci[i]))]] = 1.0
    visits = defaultdict(set)
    for k, tile, _p, rid in full_plan:
        if rid and comp[tile, k]:
            visits[rid].add(tile)
    for (rid, j), q in Q.items():
        x0[q] = 1.0 if j in visits[rid] else 0.0
    for rid, z in Z.items():
        x0[z] = 1.0 if len(visits[rid]) >= int(sc.request_by_id[rid]["required_tile_count"]) else 0.0
    sol = highspy.HighsSolution()
    sol.col_value = list(x0)
    sol.value_valid = True
    h.setSolution(sol)
    started = time.time()
    h.run()
    status_s = h.modelStatusToString(h.getModelStatus())
    s = h.getSolution()
    if not s.value_valid:
        return None, status_s, time.time() - started
    xs = np.array(s.col_value)
    new = sci.copy()
    for (i, k), c in X.items():
        if xs[c] > 0.5:
            new[i] = k
    return new, status_s, time.time() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--delta", type=float, default=10.0)
    parser.add_argument("--pad", type=int, default=0)
    parser.add_argument("--time-limit", type=float, default=3600)
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--full", action="store_true", help="窗口覆盖全程（没有固定的中间段），即带候选限制的精确全局模型")
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cdp = CDP(sc)
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    sci = np.load(args.init).astype(np.int32)
    best = cdp.evaluate(sci)
    print(f"初始 {best:.6f}", flush=True)
    for r in range(args.rounds):
        blocks = ([(0, int(sc.night_of_slot.max()))] if args.full
                  else promising_blocks(sc, sci, val, args.delta, args.pad))
        print(f"第 {r + 1} 轮：{len(blocks)} 个夜块，共 {sum(b - a + 1 for a, b in blocks)} 夜", flush=True)
        _t, plan = cdp.plan(sci)
        new, status, dt = solve(sc, sci, plan, blocks, args.delta, args.time_limit, threads=args.threads)
        if new is None:
            print(f"  {status}", flush=True)
            break
        v = cdp.evaluate(new)
        print(f"  {status} {dt:.0f}s：{best:.6f} -> {v:.6f}", flush=True)
        if v > best + 1e-7:
            best, sci = v, new
            np.save(args.out, sci)
        else:
            break
    np.save(args.out, sci)
    print(f"结束 {best:.6f}", flush=True)


if __name__ == "__main__":
    main()
