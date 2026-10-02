"""精确时间轴流模型 MILP：游标在 150 秒格上走一条路径，罚分、重访、注定中断都按官方规则建模。

节点：格 0..n。弧：
- 等待 w[k]：k → 下一个时隙边界；若开始时存在未完成且能完成的天区，罚 0.15·长度
- 科学观测 x[i,k]：首次完成，得分 = 基础分 + 最优项目加成（REQUIRED 另计 +1000）
- 请求重访 y[j,r,k]：已完成的请求天区、带标签，窗口内；结果为完成或天气中断
- 注定中断 d[i,k]：未完成的天区、会被天气打断
完成状态 D[i](t) = Σ_{end(x) ≤ t} x，只在需要的格上用递推定义。
罚分：pw[k] ≥ w[k] − D[i](k)，对每个 i ∈ A(k)（A(k) 为格 k 能完成的天区）。

--prune 用于大场景：科学候选按夜内价值剪枝（每块天区保留若干好夜的密集候选和其余夜的最好几格）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import COMPLETED, TICK, WEATHER, Scenario


class Builder:
    def __init__(self):
        self.rows, self.cols, self.data, self.lo, self.hi = [], [], [], [], []
        self.n_rows = 0
        self.cost = []
        self.integral = []
        self.ub = []

    def var(self, cost=0.0, integral=False, ub=1.0):
        self.cost.append(cost)
        self.integral.append(1 if integral else 0)
        self.ub.append(ub)
        return len(self.cost) - 1

    def row(self, entries, lower, upper):
        for col, coef in entries:
            self.rows.append(self.n_rows)
            self.cols.append(col)
            self.data.append(coef)
        self.lo.append(lower)
        self.hi.append(upper)
        self.n_rows += 1

    def solve(self, time_limit, gap, verbose=True, x0=None):
        n = len(self.cost)
        A = coo_array((np.array(self.data), (np.array(self.rows), np.array(self.cols))), shape=(self.n_rows, n)).tocsr()
        if verbose:
            print(f"  MILP：{n} 变量，{self.n_rows} 约束，{len(self.data)} 非零", flush=True)
        started = time.time()
        res = milp(np.array(self.cost), integrality=np.array(self.integral), bounds=Bounds(np.zeros(n), np.array(self.ub)),
                   constraints=LinearConstraint(A, np.array(self.lo), np.array(self.hi)),
                   options={"time_limit": time_limit, "mip_rel_gap": gap, "disp": verbose})
        if verbose:
            print(f"  {res.message}  目标={None if res.x is None else -res.fun:.3f}  {time.time() - started:.0f}s", flush=True)
        return res


def science_candidates(sc: Scenario, prune: dict | None):
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    if not prune:
        return [(int(i), int(k)) for i, k in zip(*np.nonzero(comp))], val
    night = np.repeat(sc.night_of_slot, sc.per)
    nn = int(night.max()) + 1
    out = set()
    for i in range(sc.n_tiles):
        v = val[i]
        ok = np.flatnonzero(np.isfinite(v))
        if not len(ok):
            continue
        nb = np.full(nn, -np.inf)
        np.maximum.at(nb, night[ok], v[ok])
        order = [int(n) for n in np.argsort(-nb) if np.isfinite(nb[n])]
        dense = set(order[:prune["dense_nights"]])
        for n in order:
            ks = ok[night[ok] == n]
            if n in dense:
                keep = ks[v[ks] >= nb[n] - prune["dense_delta"]]
            else:
                keep = ks[np.argsort(-v[ks])[:prune["sparse_top"]]]
            out.update((i, int(k)) for k in keep)
    for i, k in prune.get("extra", ()):
        if np.isfinite(val[i, k]):
            out.add((int(i), int(k)))
    return sorted(out), val


def build(sc: Scenario, prune=None, penalty=True, verbose=True):
    per, n = sc.per, sc.n_ticks
    rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
    cand, val = science_candidates(sc, prune)
    B = Builder()
    out_arcs = defaultdict(list)   # 节点 -> [(var, 1)]
    in_arcs = defaultdict(list)
    x_vars = {}
    for i, k in cand:
        e = int(sc.end[i, k])
        bonus = 1000.0 if sc.required[i] else 0.0
        v = B.var(-(float(val[i, k]) + bonus), integral=True)
        x_vars[(i, k)] = v
        out_arcs[k].append(v)
        in_arcs[e].append(v)
    w_vars = {}
    for k in range(n):
        b = (k // per + 1) * per
        v = B.var(0.0)
        w_vars[k] = v
        out_arcs[k].append(v)
        in_arcs[b].append(v)
    # 请求重访
    y_vars = defaultdict(list)  # (j, rid) -> [(k, var)]
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            for k in np.flatnonzero((sc.outcome[j, a:b] == COMPLETED) | (sc.outcome[j, a:b] == WEATHER)) + a:
                k = int(k)
                e = int(sc.end[j, k])
                v = B.var(0.0)
                y_vars[(j, rid)].append((k, v))
                out_arcs[k].append(v)
                in_arcs[e].append(v)
    d_vars = defaultdict(list)
    for i, k in zip(*np.nonzero(sc.outcome == WEATHER)):
        i, k = int(i), int(k)
        e = int(sc.end[i, k])
        v = B.var(0.0)
        d_vars[i].append((k, v))
        out_arcs[k].append(v)
        in_arcs[e].append(v)
    # 流守恒：节点 0 流出 1，节点 n 流入 1
    for node in range(n + 1):
        entries = [(v, 1.0) for v in out_arcs.get(node, [])] + [(v, -1.0) for v in in_arcs.get(node, [])]
        rhs = 1.0 if node == 0 else (-1.0 if node == n else 0.0)
        if entries:
            B.row(entries, rhs, rhs)
    # 每块天区最多一次科学观测
    by_tile = defaultdict(list)
    for (i, k), v in x_vars.items():
        by_tile[i].append((k, v))
    for i, items in by_tile.items():
        B.row([(v, 1.0) for _k, v in items], 0.0, 1.0)
    # 完成状态 D[i](t)：在需要的格上递推
    need_ticks = defaultdict(set)
    for (j, _rid), items in y_vars.items():
        need_ticks[j].update(k for k, _v in items)
    for i, items in d_vars.items():
        need_ticks[i].update(k for k, _v in items)
    act_ticks = {}
    if penalty:
        for i in range(sc.n_tiles):
            ks = np.flatnonzero(sc.actionable[i])
            act_ticks[i] = ks
            need_ticks[i].update(int(k) for k in ks)
    D = {}
    for i, ticks in need_ticks.items():
        ticks = sorted(ticks)
        items = sorted((int(sc.end[i, k]), v) for k, v in by_tile.get(i, []))
        prev = None
        pos = 0
        for t in ticks:
            v = B.var(0.0)
            entries = [(v, 1.0)]
            if prev is not None:
                entries.append((prev, -1.0))
            while pos < len(items) and items[pos][0] <= t:
                entries.append((items[pos][1], -1.0))
                pos += 1
            B.row(entries, 0.0, 0.0)
            D[(i, t)] = v
            prev = v
    # 重访要求已完成；注定中断要求未完成
    for (j, _rid), items in y_vars.items():
        for k, v in items:
            B.row([(v, 1.0), (D[(j, k)], -1.0)], -np.inf, 0.0)
    for i, items in d_vars.items():
        for k, v in items:
            B.row([(v, 1.0), (D[(i, k)], 1.0)], -np.inf, 1.0)
    # 请求
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        need = int(row["required_tile_count"])
        z = B.var(-(float(row["completion_reward"]) + float(row["miss_penalty"])), integral=True)
        vis = []
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            q = B.var(0.0)
            entries = [(q, 1.0)]
            entries += [(v, -1.0) for k, v in by_tile.get(j, []) if a <= k < b]
            entries += [(v, -1.0) for k, v in y_vars.get((j, rid), []) if sc.outcome[j, k] == COMPLETED]
            B.row(entries, -np.inf, 0.0)
            vis.append(q)
        B.row([(z, float(need))] + [(q, -1.0) for q in vis], -np.inf, 0.0)
    # 罚分
    if penalty:
        for k in range(n):
            tiles = np.flatnonzero(sc.actionable[:, k])
            if not len(tiles):
                continue
            b = (k // per + 1) * per
            pw = B.var(rate * (b - k))
            for i in tiles:
                B.row([(pw, 1.0), (w_vars[k], -1.0), (D[(int(i), k)], 1.0)], 0.0, np.inf)
    return B, x_vars, w_vars, y_vars, d_vars


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--time-limit", type=float, default=1800)
    parser.add_argument("--gap", type=float, default=0.0)
    parser.add_argument("--prune", type=str, default="", help='JSON，例如 {"dense_nights":6,"dense_delta":40,"sparse_top":3}')
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    prune = json.loads(args.prune) if args.prune else None
    B, x_vars, *_ = build(sc, prune)
    res = B.solve(args.time_limit, args.gap)
    if res.x is None:
        raise SystemExit(res.message)
    chosen = sorted((k, i) for (i, k), v in x_vars.items() if res.x[v] > 0.5)
    out = args.out or Path("runs/omni") / sc.name / "flow_science.json"
    out.write_text(json.dumps([[int(k), int(i)] for k, i in chosen]))
    print(out, len(chosen))


if __name__ == "__main__":
    main()
