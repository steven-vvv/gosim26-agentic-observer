"""全候选（不依赖 Δ）的严格上界：拉格朗日松弛 + 次梯度。

    .venv/bin/python tools/omni/audit/lagr_bound.py kit/scenarios/dev-reference --iters 3000

记号（全部来自 runs/omni/<场景>/tables.npz，和官方计分器逐位一致）：
- A(k)：格 k 上 _can_complete_from 为真的天区；A'(k) = ∩_{k0 同时隙且 k0 ≤ k} A(k0)。
- R：所有“带标签重访”和“注定天气中断”动作区间 [k, end) 的并（忽略状态条件，是超集）。
- 硬格 H = {k ∉ R : A'(k) ≠ ∅}。

对任意合法决策序列（不含中途停止），总分 ≤ 3920 + Σ_i v_i(k_i) − Pen，其中
  Pen ≥ rate · Σ_{h∈H, h∉O} [∃ i∈A'(h): e_i > h]   （O = 科学观测占用格；等待动作从 k0 ≤ h 开始时 A'(h) ⊆ A(k0)，且 e_i>h ⟹ e_i>k0）
      ≥ rate · Σ_{h∈H} Σ_i λ_{h,i} [e_i > h] − rate · |H ∩ O|         （λ_{h,·} 为 A'(h) 上的任意概率分布）
其它罚分（invalid ≥100 且同样按等待判罚、几何中断 100 > 覆盖 ≤8 格的 1.2、unsafe 2000、flexible 缺额 ≥ 0）只会更大。
科学观测两两不相交：Σ_i occ_i(t) ≤ 1 ∀t，乘子 μ_t ≥ 0。于是
  UB(λ, μ) = 3920 + Σ_t μ_t + Σ_i max{ max_{k∈C_i} [v_i(k) + rate·|H∩[k,e)| − rate·Λ_i(e) − (P(e) − P(k))],  −rate·Λ_i(n) − 1000·[REQUIRED_i] }
  Λ_i(t) = Σ_{h∈H, h<t} λ_{h,i}，P = μ 的前缀和，C_i = 天区 i 全部可完成的开始格。
“中途停止（不再发决策）”的策略：等价于把硬格截断到停止时刻 T、可选开始格限制为 e ≤ T；脚本末尾对所有 T 取最大值。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]  # tools/omni/audit -> 仓库根目录
sys.path.insert(0, str(ROOT / "tools" / "omni"))
from model import COMPLETED, TICK, WEATHER, Scenario, format_utc


def project_columns_simplex(v: np.ndarray, support: np.ndarray) -> np.ndarray:
    """把每一列投影到 support 指示的子单纯形（非支撑分量置 0）。v: (m, c), support: bool (m, c)。"""
    big = -1e9
    u = np.where(support, v, big)
    s = -np.sort(-u, axis=0)
    css = np.cumsum(s, axis=0) - 1.0
    ind = np.arange(1, u.shape[0] + 1, dtype=float)[:, None]
    cond = (s - css / ind) > 0
    rho = cond.shape[0] - 1 - np.argmax(cond[::-1], axis=0)
    theta = css[rho, np.arange(u.shape[1])] / (rho + 1)
    w = np.maximum(u - theta[None, :], 0.0)
    w[~support] = 0.0
    return w


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--iters", type=int, default=2000)
    parser.add_argument("--lb", type=float, default=None, help="已知可行值（Polyak 步长目标），默认 dev-reference 21119.494551")
    parser.add_argument("--beta", type=float, default=1.0)
    parser.add_argument("--req", type=float, default=None, help="请求奖励上界，默认按 request_opportunity 可完成的请求求和")
    parser.add_argument("--save", type=Path, default=None)
    parser.add_argument("--load", type=Path, default=None)
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    n, per, T = sc.n_ticks, sc.per, sc.n_tiles
    rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
    req_pen = float(sc.score_config["penalties"]["required_miss"])
    comp = sc.outcome == COMPLETED
    val = np.where(comp, sc.base + sc.bonus.max(axis=2), -np.inf)
    end = sc.end.astype(np.int64)
    # 请求奖励上界：required_tile_count 块天区在窗口内都有可完成格（比官方 request_opportunity 更宽）
    req_ub = 0.0
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        ok = sum(bool(comp[sc.index[t], a:b].any()) for t in sc.request_tiles[rid])
        if ok >= int(row["required_tile_count"]):
            req_ub += float(row["completion_reward"])
        else:
            print(f"  请求 {rid} 不可完成（窗口内可完成天区 {ok} < {row['required_tile_count']}）")
    if args.req is not None:
        req_ub = args.req
    print(f"请求奖励上界 {req_ub}")
    # R：重访/注定中断区间的并
    cover = np.zeros(n + 1, dtype=np.int64)
    for row in sc.requests:
        rid = row["request_id"]
        a, b = sc.request_window[rid]
        for t in sc.request_tiles[rid]:
            j = sc.index[t]
            ks = np.flatnonzero(comp[j, a:b] | (sc.outcome[j, a:b] == WEATHER)) + a
            np.add.at(cover, ks, 1)
            np.add.at(cover, end[j, ks], -1)
    for i in range(T):
        ks = np.flatnonzero(sc.outcome[i] == WEATHER)
        np.add.at(cover, ks, 1)
        np.add.at(cover, end[i, ks], -1)
    R = np.cumsum(cover)[:n] > 0
    # A'
    act = sc.actionable.copy()
    Ap = act.copy()
    for k in range(1, n):
        if k % per:
            Ap[:, k] &= Ap[:, k - 1]
    H = np.flatnonzero(~R & Ap.any(axis=0))
    nH = len(H)
    print(f"格 {n}，R 覆盖 {int(R.sum())}，硬格 |H| = {nH}（按 A 计 {int((~R & act.any(axis=0)).sum())}），"
          f"硬格全部等待的罚分 = {nH * rate:.1f}")
    supp = Ap[:, H]  # (T, nH)
    hard_ind = np.zeros(n + 1, dtype=np.int64)
    hard_ind[H] = 1
    Hc = np.concatenate([[0], np.cumsum(hard_ind[:n])])  # Hc[t] = #hard ticks < t
    # 候选
    ti, ki = np.nonzero(comp)
    ei = end[ti, ki]
    vi = val[ti, ki] + rate * (Hc[ei] - Hc[ki])
    seg = np.searchsorted(ti, np.arange(T + 1))
    assert np.all(np.diff(seg) > 0)
    # 乘子
    if args.load and args.load.exists():
        data = np.load(args.load)
        lam, mu = data["lam"], data["mu"]
    else:
        lam = supp.astype(float)
        lam /= np.maximum(lam.sum(axis=0, keepdims=True), 1)
        mu = np.zeros(n, dtype=float)
    lb = args.lb if args.lb is not None else 21119.494551
    best = (np.inf, None, None)
    beta = args.beta
    stall = 0
    started = time.time()
    np.zeros(n + 1, dtype=np.int64)  # 每格对应的硬格序号（用于 e_i > h 的比较：h < e ⟺ 硬格序号 < Hc[e]）
    for it in range(args.iters + 1):
        # Λ_i(t) = Σ_{h<t} λ_{h,i}：先对硬格做前缀和
        lam_c = np.cumsum(lam, axis=1)  # (T, nH)  lam_c[:, q] = Σ_{h ≤ H[q]}
        # Λ_i(e) = lam_c[i, Hc[e]-1]（Hc[e] 个硬格 < e）
        P = np.concatenate([[0.0], np.cumsum(mu)])
        cnt_e = Hc[ei]
        Lam_e = np.where(cnt_e > 0, lam_c[ti, np.maximum(cnt_e - 1, 0)], 0.0)
        g = vi - rate * Lam_e - (P[ei] - P[ki])
        gmax = np.maximum.reduceat(g, seg[:-1])
        garg = np.array([seg[i] + int(np.argmax(g[seg[i]:seg[i + 1]])) for i in range(T)])
        Lam_n = lam_c[:, -1] if nH else np.zeros(T)
        gnc = -rate * Lam_n - req_pen * sc.required.astype(float)
        choose_nc = gnc > gmax
        U = np.where(choose_nc, gnc, gmax)
        UB = req_ub + mu.sum() + U.sum()
        if UB < best[0] - 1e-9:
            best = (UB, lam.copy(), mu.copy())
            stall = 0
        else:
            stall += 1
            if stall >= 30:
                beta *= 0.7
                stall = 0
        if it % 50 == 0 or it == args.iters:
            print(f"  it {it:5d}  UB {UB:.3f}  best {best[0]:.3f}  gap {best[0] - lb:.3f}  beta {beta:.4f}  "
                  f"不完成 {int(choose_nc.sum())}  {time.time() - started:.0f}s", flush=True)
        if it == args.iters:
            break
        # 次梯度
        e_star = np.where(choose_nc, n, ei[garg])  # 每块天区的完成格（不完成 = n）
        # λ 方向：λ_{h,i} 增大，若 h < e_i*
        cnt_star = Hc[e_star]  # 前 cnt_star 个硬格满足 h < e*
        ramp = (np.arange(nH)[None, :] < cnt_star[:, None]) & supp  # (T, nH)
        grad_lam = -rate * ramp.astype(float)
        occ = np.zeros(n + 1, dtype=np.int64)
        sel = ~choose_nc
        np.add.at(occ, ki[garg[sel]], 1)
        np.add.at(occ, ei[garg[sel]], -1)
        occ = np.cumsum(occ)[:n]
        grad_mu = 1.0 - occ
        norm2 = float((grad_lam ** 2).sum() + (grad_mu ** 2).sum())
        step = beta * max(UB - lb, 1e-6) / max(norm2, 1e-12)
        lam = project_columns_simplex(lam - step * grad_lam, supp)
        mu = np.maximum(mu - step * grad_mu, 0.0)
    UB, lam, mu = best
    print(f"最终上界（不含中途停止）UB = {UB:.6f}，与 {lb:.6f} 的差 {UB - lb:.6f}")
    # 中途停止：对每个时隙边界 T 评估 UB(T)
    lam_c = np.cumsum(lam, axis=1)
    P = np.concatenate([[0.0], np.cumsum(mu)])
    cnt_e = Hc[ei]
    Lam_e = np.where(cnt_e > 0, lam_c[ti, np.maximum(cnt_e - 1, 0)], 0.0)
    g = vi - rate * Lam_e - (P[ei] - P[ki])
    # 每块天区按 e 排序的前缀最大
    boundaries = np.arange(0, n + 1, per)
    pref = np.full((T, len(boundaries)), -np.inf)
    for i in range(T):
        a, b = seg[i], seg[i + 1]
        order = np.argsort(ei[a:b], kind="stable")
        e_sorted = ei[a:b][order]
        g_sorted = g[a:b][order]
        run = np.maximum.accumulate(g_sorted)
        idx = np.searchsorted(e_sorted, boundaries, side="right") - 1
        pref[i] = np.where(idx >= 0, run[np.maximum(idx, 0)], -np.inf)
    cntT = Hc[boundaries]
    LamT = np.where(cntT[None, :] > 0, lam_c[:, np.maximum(cntT - 1, 0)], 0.0)
    ncT = -rate * LamT - req_pen * sc.required.astype(float)[:, None]
    UBT = req_ub + mu.sum() + np.maximum(pref, ncT).sum(axis=0)
    tbest = int(np.argmax(UBT))
    print(f"含中途停止的上界 max_T UB(T) = {UBT.max():.6f}（T = 格 {boundaries[tbest]}，"
          f"{format_utc(sc.tick_time[min(boundaries[tbest], n - 1)])}）；不停止 UB(n) = {UBT[-1]:.6f}")
    if args.save:
        np.savez_compressed(args.save, lam=lam, mu=mu, UB=UB, UBT=UBT, H=H)


if __name__ == "__main__":
    main()
