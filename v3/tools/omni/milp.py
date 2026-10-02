"""联合 MILP：科学观测排程 + 请求 + 空等罚分代理，求一个好的初始全知排程。

模型：
- x[i,k]：天区 i 在格 k 开始首次（计分）观测，只取剪枝后的候选；每格最多被一次观测占用；每块天区最多一次。
- 时隙中间开始的观测必须紧接另一次科学观测（保守：不靠填补手段接续）。
- 请求 r：窗口结束前完成、且之后窗口里有重访机会（或观测本身在窗口里）的所需天区数 ≥ N_r·y_r。
- 罚分代理：没有填补手段（请求重访或注定中断）的格按 (夜, 可完成天区集合) 分组，
  只有组内全部天区都在该夜之前完成才免罚：u_g ≥ 1 − D[i, 夜]，罚 0.15·|g|·u_g。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import COMPLETED, WEATHER, Scenario


def candidates(sc: Scenario, top_nights: int, near: float, other: int, extra: dict | None = None):
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
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
        order = np.argsort(-nb)
        good = {int(n) for n in order[:top_nights] if np.isfinite(nb[n])}
        for n in range(nn):
            if not np.isfinite(nb[n]):
                continue
            ks = ok[night[ok] == n]
            if n in good:
                keep = ks[v[ks] >= nb[n] * (1 - near)]
            else:
                keep = ks[np.argsort(-v[ks])[:other]]
            out.update((i, int(k)) for k in keep)
    if extra:
        for i, ks in extra.items():
            out.update((i, int(k)) for k in ks if np.isfinite(val[i, k]))
    return sorted(out), val


def build_and_solve(sc: Scenario, cand, val, time_limit: float, gap: float, penalty: bool = True,
                    fixed_request_complete: bool = True, verbose: bool = True):
    per = sc.per
    n = sc.n_ticks
    night = np.repeat(sc.night_of_slot, per)
    nn = int(night.max()) + 1
    m = len(cand)
    ci = np.array([c[0] for c in cand])
    ck = np.array([c[1] for c in cand])
    ce = sc.end[ci, ck].astype(np.int64)
    cv = val[ci, ck] + np.where(sc.required[ci], 1000.0, 0.0)
    rows, cols, data = [], [], []
    lo, hi = [], []
    r = 0

    def add_row(entries, lower, upper):
        nonlocal r
        for col, coef in entries:
            rows.append(r)
            cols.append(col)
            data.append(coef)
        lo.append(lower)
        hi.append(upper)
        r += 1

    # 每块天区最多一次
    for i in range(sc.n_tiles):
        idx = np.flatnonzero(ci == i)
        if len(idx):
            add_row([(int(j), 1.0) for j in idx], -np.inf, 1.0)
    # 每格最多被一次观测占用
    cover = {}
    for j in range(m):
        for t in range(int(ck[j]), int(ce[j])):
            cover.setdefault(t, []).append(j)
    for t, js in cover.items():
        if len(js) > 1:
            add_row([(j, 1.0) for j in js], -np.inf, 1.0)
    # 时隙中间开始必须紧接
    ends_at = {}
    for j in range(m):
        ends_at.setdefault(int(ce[j]), []).append(j)
    for k in sorted({int(x) for x in ck[ck % per != 0]}):
        starts = np.flatnonzero(ck == k)
        entries = [(int(j), 1.0) for j in starts] + [(j, -1.0) for j in ends_at.get(k, [])]
        add_row(entries, -np.inf, 0.0)
    n_x = m
    # 请求
    req_vars = []
    col = n_x
    revisit_ok = {}
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            okk = np.flatnonzero(sc.outcome[j, a:b] == COMPLETED) + a
            revisit_ok[(rid, j)] = okk
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        need = int(row["required_tile_count"])
        entries = []
        for tile_id in sc.request_tiles[rid]:
            j = sc.index[tile_id]
            okk = revisit_ok[(rid, j)]
            for idx in np.flatnonzero(ci == j):
                k, e = int(ck[idx]), int(ce[idx])
                # 在窗口里观测，或在窗口外完成且之后窗口里有可完成的重访
                if a <= k < b or (e <= b and len(okk) and okk[-1] >= max(a, e)):
                    entries.append((int(idx), 1.0))
        value = float(row["completion_reward"]) + float(row["miss_penalty"])
        req_vars.append((col, value, need))
        add_row(entries + [(col, -float(need))], 0.0, np.inf)
        col += 1
    # 罚分代理
    groups = []
    if penalty:
        fill = np.zeros(n, dtype=bool)
        for row in sc.requests:
            rid = row["request_id"]
            a, b = sc.request_window[rid]
            for tile_id in sc.request_tiles[rid]:
                j = sc.index[tile_id]
                fill[a:b] |= (sc.outcome[j, a:b] == COMPLETED) | (sc.outcome[j, a:b] == WEATHER)
        fill |= (sc.outcome == WEATHER).any(axis=0)
        np.zeros(n, dtype=object)
        act = sc.actionable
        weights = 1 << np.arange(sc.n_tiles, dtype=object)
        for t in np.flatnonzero(~fill & act.any(axis=0)):
            groups.append((int(night[t]), int(sum(int(w) for w in weights[act[:, t]]))))
        from collections import Counter
        groups = Counter(groups)
        # D[i, n]：夜 n 开始前已完成
        d_start = col
        col += sc.n_tiles * nn
        for i in range(sc.n_tiles):
            idx = np.flatnonzero(ci == i)
            end_night = night[np.minimum(ce[idx] - 1, n - 1)]
            for nt in range(nn):
                # D[i, nt] = D[i, nt-1] + 在夜 nt-1 结束的观测
                entries = [(d_start + i * nn + nt, 1.0)]
                if nt:
                    entries.append((d_start + i * nn + nt - 1, -1.0))
                    entries += [(int(j), -1.0) for j in idx[end_night == nt - 1]]
                add_row(entries, 0.0, 0.0)
        u_vars = []
        for (nt, mask), count in groups.items():
            u = col
            col += 1
            u_vars.append((u, count))
            for i in range(sc.n_tiles):
                if mask >> i & 1:
                    add_row([(u, 1.0), (d_start + i * nn + nt, 1.0)], 1.0, np.inf)
    n_var = col
    c = np.zeros(n_var)
    c[:n_x] = -cv
    for colr, value, _need in req_vars:
        c[colr] = -value
    if penalty:
        rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * 150
        for u, count in u_vars:
            c[u] = rate * count
    integrality = np.zeros(n_var)
    integrality[:n_x] = 1
    for colr, _v, _n in req_vars:
        integrality[colr] = 1
    lb = np.zeros(n_var)
    ub = np.ones(n_var)
    A = coo_array((np.array(data), (np.array(rows), np.array(cols))), shape=(r, n_var)).tocsr()
    if verbose:
        print(f"  MILP: {n_x} 候选, {len(req_vars)} 请求, {len(groups)} 罚分组, {n_var} 变量, {r} 约束", flush=True)
    started = time.time()
    res = milp(c, integrality=integrality, bounds=Bounds(lb, ub), constraints=LinearConstraint(A, np.array(lo), np.array(hi)),
               options={"time_limit": time_limit, "mip_rel_gap": gap, "disp": verbose})
    if verbose:
        print(f"  {res.message}  obj={-res.fun if res.x is not None else None}  {time.time() - started:.0f}s", flush=True)
    if res.x is None:
        raise RuntimeError(res.message)
    chosen = [cand[j] for j in np.flatnonzero(res.x[:n_x] > 0.5)]
    return sorted((k, i) for i, k in chosen), res


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--top-nights", type=int, default=8)
    parser.add_argument("--near", type=float, default=0.04)
    parser.add_argument("--other", type=int, default=2)
    parser.add_argument("--time-limit", type=float, default=600)
    parser.add_argument("--gap", type=float, default=1e-4)
    parser.add_argument("--no-penalty", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cand, val = candidates(sc, args.top_nights, args.near, args.other)
    chosen, _res = build_and_solve(sc, cand, val, args.time_limit, args.gap, penalty=not args.no_penalty)
    out = args.out or Path("runs/omni") / sc.name / "milp_science.json"
    out.write_text(json.dumps([[int(k), int(i)] for k, i in chosen]))
    print(out, len(chosen))


if __name__ == "__main__":
    main()
