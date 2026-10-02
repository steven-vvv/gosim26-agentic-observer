"""大场景用的时间轴流模型 MILP（highspy）：科学观测候选剪枝，完成状态按夜 + 夜内链，罚分按组聚合。

与 flow.py 的精确模型相比：
- 科学候选：每块天区在价值最高的若干夜里保留接近夜内最优的全部开始格（稠密），其余夜只留时隙边界上最好的一格。
- 完成状态 C_i(t)：D[i,n]（夜 n 之前完成）加上同夜在 t 之前结束的候选；稠密夜用链式变量，只在需要的格上定义。
- 罚分：同一夜内可完成天区集合相同的连续格合成一组 g，W_g = Σ_{k∈g} 长度·w_k，
  pen_g ≥ 0.15·W_g − M_g·C_i(组起点)，对组内每块天区 i（组中间才完成的天区按组起点状态计，略偏保守）。
- 重访、注定中断的合法性按 C 精确约束；等待、重访、注定中断为连续变量（给定科学观测后路径由 DP 精确求）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import highspy
import numpy as np
from scipy.sparse import coo_array

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import COMPLETED, TICK, WEATHER, Scenario


class LP:
    def __init__(self):
        self.cost, self.lb, self.ub, self.integ = [], [], [], []
        self.r_idx, self.c_idx, self.val = [], [], []
        self.rlo, self.rhi = [], []

    def var(self, cost=0.0, lb=0.0, ub=1.0, integer=False):
        self.cost.append(cost)
        self.lb.append(lb)
        self.ub.append(ub)
        self.integ.append(integer)
        return len(self.cost) - 1

    def row(self, entries, lo, hi):
        r = len(self.rlo)
        for c, v in entries:
            self.r_idx.append(r)
            self.c_idx.append(c)
            self.val.append(v)
        self.rlo.append(lo)
        self.rhi.append(hi)
        return r

    def to_highs(self, h: highspy.Highs):
        inf = highspy.kHighsInf
        n, m = len(self.cost), len(self.rlo)
        A = coo_array((np.array(self.val, dtype=np.float64), (np.array(self.r_idx), np.array(self.c_idx))), shape=(m, n)).tocsc()
        lp = highspy.HighsLp()
        lp.num_col_ = n
        lp.num_row_ = m
        lp.col_cost_ = np.array(self.cost, dtype=np.float64)
        lp.col_lower_ = np.array(self.lb, dtype=np.float64)
        lp.col_upper_ = np.array([inf if u == np.inf else u for u in self.ub], dtype=np.float64)
        lp.row_lower_ = np.array([-inf if x == -np.inf else x for x in self.rlo], dtype=np.float64)
        lp.row_upper_ = np.array([inf if x == np.inf else x for x in self.rhi], dtype=np.float64)
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_ = A.indptr.astype(np.int32)
        lp.a_matrix_.index_ = A.indices.astype(np.int32)
        lp.a_matrix_.value_ = A.data
        lp.integrality_ = [highspy.HighsVarType.kInteger if f else highspy.HighsVarType.kContinuous for f in self.integ]
        h.passModel(lp)
        return A


def pick_candidates(sc: Scenario, val, dense_nights: int, dense_delta: float, extra=(), request_dense=True):
    per = sc.per
    night = np.repeat(sc.night_of_slot, per)
    nn = int(night.max()) + 1
    cand = set()
    dense = defaultdict(set)  # tile -> dense nights
    for i in range(sc.n_tiles):
        v = val[i]
        ok = np.flatnonzero(np.isfinite(v))
        if not len(ok):
            continue
        nb = np.full(nn, -np.inf)
        np.maximum.at(nb, night[ok], v[ok])
        order = [int(x) for x in np.argsort(-nb) if np.isfinite(nb[x])]
        dn = set(order[:dense_nights])
        if request_dense:
            # 请求天区：各请求窗口里最好的两夜也稠密
            for row in sc.requests:
                rid = row["request_id"]
                if sc.tile_ids[i] not in sc.request_tiles[rid]:
                    continue
                a, b = sc.request_window[rid]
                inwin = ok[(ok >= a) & (ok < b)]
                if len(inwin):
                    nbw = {}
                    for k in inwin:
                        nbw[int(night[k])] = max(nbw.get(int(night[k]), -np.inf), v[k])
                    dn.update(sorted(nbw, key=lambda x: -nbw[x])[:2])
        dense[i] = dn
        for nt in order:
            ks = ok[night[ok] == nt]
            if nt in dn:
                keep = ks[v[ks] >= nb[nt] - dense_delta]
            else:
                bnd = ks[ks % per == 0]
                keep = bnd[np.argsort(-v[bnd])[:1]] if len(bnd) else ks[:0]
            cand.update((i, int(k)) for k in keep)
    for i, k in extra:
        if np.isfinite(val[i, k]):
            cand.add((int(i), int(k)))
            dense[int(i)].add(int(night[k]))
    return sorted(cand), dense


def build(sc: Scenario, cand, verbose=True):
    per, n = sc.per, sc.n_ticks
    rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    night = np.repeat(sc.night_of_slot, per)
    nn = int(night.max()) + 1
    np.searchsorted(night, np.arange(nn))
    L = LP()
    out_arcs, in_arcs = defaultdict(list), defaultdict(list)
    X = {}
    for i, k in cand:
        e = int(sc.end[i, k])
        X[(i, k)] = c = L.var(-(float(val[i, k]) + (1000.0 if sc.required[i] else 0.0)), integer=True)
        out_arcs[k].append(c)
        in_arcs[e].append(c)
    Wv = {}
    for k in range(n):
        b = (k // per + 1) * per
        Wv[k] = c = L.var(0.0)
        out_arcs[k].append(c)
        in_arcs[b].append(c)
    Y = defaultdict(list)  # tile -> [(k, rid, var)]
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            for k in np.flatnonzero(comp[j, a:b] | (sc.outcome[j, a:b] == WEATHER)) + a:
                k = int(k)
                c = L.var(0.0)
                Y[j].append((k, rid, c))
                out_arcs[k].append(c)
                in_arcs[int(sc.end[j, k])].append(c)
    Dm = defaultdict(list)
    for i, k in zip(*np.nonzero(sc.outcome == WEATHER)):
        i, k = int(i), int(k)
        c = L.var(0.0)
        Dm[i].append((k, c))
        out_arcs[k].append(c)
        in_arcs[int(sc.end[i, k])].append(c)
    for node in range(n + 1):
        entries = [(c, 1.0) for c in out_arcs.get(node, [])] + [(c, -1.0) for c in in_arcs.get(node, [])]
        rhs = 1.0 if node == 0 else (-1.0 if node == n else 0.0)
        if entries:
            L.row(entries, rhs, rhs)
    by_tile = defaultdict(list)
    for (i, k), c in X.items():
        by_tile[i].append((k, c))
    for i, items in by_tile.items():
        L.row([(c, 1.0) for _k, c in items], 0.0, 1.0)
    # D[i, nt]：夜 nt 开始前已完成
    D = {}
    by_tile_night = defaultdict(list)
    for (i, k), c in X.items():
        by_tile_night[(i, int(night[k]))].append((int(sc.end[i, k]), c))
    for i in range(sc.n_tiles):
        prev = None
        for nt in range(nn):
            c = L.var(0.0)
            entries = [(c, 1.0)]
            if prev is not None:
                entries.append((prev, -1.0))
                entries += [(x, -1.0) for _e, x in by_tile_night.get((i, nt - 1), [])]
            L.row(entries, 0.0, 0.0)
            D[(i, nt)] = c
            prev = c
    # C_i(t)：表达式（变量列表），稠密夜用链
    first_end = {}
    for i, items in by_tile.items():
        first_end[i] = min(int(sc.end[i, k]) for k, _c in items)
    need = defaultdict(set)
    for j, items in Y.items():
        for k, _rid, _c in items:
            need[(j, int(night[k]))].add(k)
    for i, items in Dm.items():
        for k, _c in items:
            need[(i, int(night[k]))].add(k)
    groups = []
    act = sc.actionable
    masks = np.zeros(n, dtype=np.uint64)
    for i in range(sc.n_tiles):
        masks |= act[i].astype(np.uint64) << np.uint64(i)
    k = 0
    while k < n:
        if masks[k] == 0:
            k += 1
            continue
        s = k
        while k < n and masks[k] == masks[s] and night[k] == night[s]:
            k += 1
        tiles = [i for i in range(sc.n_tiles) if (int(masks[s]) >> i) & 1]
        groups.append((s, k, tiles))
        for i in tiles:
            need[(i, int(night[s]))].add(s)
    chain = {}

    def completed_expr(i, t):
        """返回 C_i(t) 的 [(var, coef)]。"""
        nt = int(night[t])
        items = by_tile_night.get((i, nt), [])
        if len(items) <= 3:
            return [(D[(i, nt)], 1.0)] + [(x, 1.0) for e, x in items if e <= t]
        return [(chain[(i, t)], 1.0)]

    for (i, nt), ticks in need.items():
        items = sorted(by_tile_night.get((i, nt), []))
        if len(items) <= 3:
            continue
        prev = D[(i, nt)]
        pos = 0
        for t in sorted(ticks):
            c = L.var(0.0)
            entries = [(c, 1.0), (prev, -1.0)]
            while pos < len(items) and items[pos][0] <= t:
                entries.append((items[pos][1], -1.0))
                pos += 1
            L.row(entries, 0.0, 0.0)
            chain[(i, t)] = c
            prev = c
    # 重访合法：已完成
    for j, items in Y.items():
        per_tick = defaultdict(list)
        for k, _rid, c in items:
            per_tick[k].append(c)
        for k, cs in per_tick.items():
            L.row([(c, 1.0) for c in cs] + [(v, -coef) for v, coef in completed_expr(j, k)], -np.inf, 0.0)
    # 注定中断合法：未完成
    for i, items in Dm.items():
        for k, c in items:
            L.row([(c, 1.0)] + [(v, coef) for v, coef in completed_expr(i, k)], -np.inf, 1.0)
    # 请求
    Z = {}
    Q = {}
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        needn = int(row["required_tile_count"])
        z = L.var(-(float(row["completion_reward"]) + float(row["miss_penalty"])), integer=True)
        Z[rid] = z
        qs = []
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            q = L.var(0.0)
            entries = [(q, 1.0)] + [(c, -1.0) for k, c in by_tile.get(j, []) if a <= k < b]
            entries += [(c, -1.0) for k, r2, c in Y.get(j, []) if r2 == rid and comp[j, k]]
            L.row(entries, -np.inf, 0.0)
            qs.append(q)
            Q[(rid, j)] = q
        L.row([(z, float(needn))] + [(q, -1.0) for q in qs], -np.inf, 0.0)
    # 罚分组
    G = []
    for s, e, tiles in groups:
        span = [(k, (k // per + 1) * per - k) for k in range(s, e)]
        m_g = rate * max(length for _k, length in span) + rate * (e - s)
        wg = L.var(0.0, ub=np.inf)
        L.row([(wg, 1.0)] + [(Wv[k], -rate * length) for k, length in span], 0.0, 0.0)
        pen = L.var(1.0, ub=np.inf)
        certain = [i for i in tiles if first_end.get(i, n + 1) > s]
        if certain:
            L.row([(pen, 1.0), (wg, -1.0)], 0.0, np.inf)
        else:
            for i in tiles:
                L.row([(pen, 1.0), (wg, -1.0)] + [(v, m_g * coef) for v, coef in completed_expr(i, s)], 0.0, np.inf)
        G.append((s, e, tiles, wg, pen))
    if verbose:
        print(f"  候选 {len(X)}，重访弧 {sum(len(v) for v in Y.values())}，注定中断弧 {sum(len(v) for v in Dm.values())}，"
              f"罚分组 {len(G)}，链变量 {len(chain)}；共 {len(L.cost)} 变量 {len(L.rlo)} 约束 {len(L.val)} 非零", flush=True)
    return L, {"X": X, "W": Wv, "Y": Y, "Dm": Dm, "D": D, "chain": chain, "Z": Z, "Q": Q, "G": G, "by_tile_night": by_tile_night}


def warm_values(sc: Scenario, L: LP, V: dict, full_plan):
    """由一条完整路径（[(tick, tile, program, request_id)]，含重访与注定中断）算出全部变量的值。"""
    per, n = sc.per, sc.n_ticks
    rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
    x = np.zeros(len(L.cost))
    comp = sc.outcome == COMPLETED
    night = np.repeat(sc.night_of_slot, per)
    done_at = {}
    at = {}
    for k, tile, _p, rid in full_plan:
        at[k] = (tile, rid)
    k = 0
    seen = set()
    while k < n:
        if k in at:
            tile, rid = at[k]
            e = int(sc.end[tile, k])
            if tile not in seen and comp[tile, k]:
                seen.add(tile)
                x[V["X"][(tile, k)]] = 1.0
                done_at[tile] = e
            elif rid:
                for kk, r2, c in V["Y"][tile]:
                    if kk == k and r2 == rid:
                        x[c] = 1.0
                        break
                else:
                    raise KeyError(("revisit", tile, k, rid))
            else:
                for kk, c in V["Dm"][tile]:
                    if kk == k:
                        x[c] = 1.0
                        break
                else:
                    raise KeyError(("doomed", tile, k))
            k = e
        else:
            x[V["W"][k]] = 1.0
            k = (k // per + 1) * per
    nn = int(night.max()) + 1
    for (i, nt), c in V["D"].items():
        x[c] = 1.0 if i in done_at and int(night[done_at[i] - 1]) < nt else 0.0
    for (i, t), c in V["chain"].items():
        x[c] = 1.0 if i in done_at and done_at[i] <= t else 0.0
    for s, e, tiles, wg, pen in V["G"]:
        wsum = sum(rate * ((kk // per + 1) * per - kk) * x[V["W"][kk]] for kk in range(s, e))
        x[wg] = wsum
        incomplete = any(done_at.get(i, n + 1) > s for i in tiles)
        x[pen] = wsum if incomplete else 0.0
    # 请求：q[r, j] = 窗口内有带该标签的完成访问（科学观测在窗口内也算）
    visits = defaultdict(set)
    for k, tile, _p, rid in full_plan:
        if rid and comp[tile, k]:
            visits[rid].add(tile)
    for (rid, j), q in V["Q"].items():
        x[q] = 1.0 if j in visits[rid] else 0.0
    for row in sc.requests:
        rid = row["request_id"]
        x[V["Z"][rid]] = 1.0 if len(visits[rid]) >= int(row["required_tile_count"]) else 0.0
    return x, done_at, nn


def solve(sc: Scenario, cand, time_limit: float, gap: float, warm_plan=None, threads: int = 4, log=True):
    L, V = build(sc, cand)
    h = highspy.Highs()
    h.setOptionValue("output_flag", log)
    h.setOptionValue("time_limit", float(time_limit))
    h.setOptionValue("mip_rel_gap", float(gap))
    h.setOptionValue("threads", threads)
    A = L.to_highs(h)
    if warm_plan is not None:
        x, _done, _nn = warm_values(sc, L, V, warm_plan)
        # 请求 q、z：逐行满足
        rows = A.tocsr()
        lhs = rows @ x
        sol = highspy.HighsSolution()
        lo, hi = np.array(L.rlo), np.array(L.rhi)
        bad = np.flatnonzero((lhs < lo - 1e-7) | (lhs > hi + 1e-7))
        if log:
            print(f"  热启动违反 {len(bad)} 行", [(int(r), float(lhs[r]), lo[r], hi[r]) for r in bad[:5]], flush=True)
        z_cols = None
        sol.col_value = list(x)
        sol.value_valid = True
        status = h.setSolution(sol)
        if log:
            print("  热启动：", status, flush=True)
        del lhs, z_cols
    started = time.time()
    h.run()
    info = h.getInfo()
    status = h.getModelStatus()
    sol = h.getSolution()
    xs = np.array(sol.col_value)
    chosen = sorted((k, i) for (i, k), c in V["X"].items() if xs[c] > 0.5)
    if log:
        print(f"  {h.modelStatusToString(status)} 目标 {info.objective_function_value:.3f} 界 {info.mip_dual_bound:.3f} "
              f"gap {info.mip_gap:.4%} 用时 {time.time() - started:.0f}s", flush=True)
    return chosen, info, V, xs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--dense-nights", type=int, default=6)
    parser.add_argument("--dense-delta", type=float, default=40.0)
    parser.add_argument("--time-limit", type=float, default=3600)
    parser.add_argument("--gap", type=float, default=1e-5)
    parser.add_argument("--warm", type=Path, help="完整计划 JSON（[tick, tile, program, request_id]）")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    warm = None
    extra = []
    if args.warm:
        warm = [tuple(x) for x in json.loads(args.warm.read_text())]
        seen = set()
        for k, tile, _p, _r in warm:
            if tile not in seen and comp[tile, k]:
                seen.add(tile)
                extra.append((tile, k))
    cand, _dense = pick_candidates(sc, val, args.dense_nights, args.dense_delta, extra)
    chosen, _info, _V, _xs = solve(sc, cand, args.time_limit, args.gap, warm)
    out = args.out or Path("runs/omni") / sc.name / "flowagg_science.json"
    out.write_text(json.dumps([[int(k), int(i)] for k, i in chosen]))
    print(out, len(chosen))


if __name__ == "__main__":
    main()
