#!/usr/bin/env python3
"""离线比较几种“按时间顺序逐次决策”的规则在几何质量上的差别（只看几何，不含天气起伏，关闭时隙照常跳过）。

    uv run python tools/plan_experiment.py scenarios/comp-a-s101

规则：
  greedy   选 V·q(t)/e 最大者（官方基线的几何部分）
  timing   再乘当晚时机因子 (q(t)/q_tonight_best)，只对还没到当晚最佳时刻的天区生效（obs3 的做法）
  price    选 (V·q(t) − μ_i)/e 最大者；μ_i 是天区 i 留到将来能拿到的剩余价值，
           来自“天区 → 时段”分配问题的对偶价格（次梯度法求解，时段粒度 1 小时）
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import TICK, geometry_quality, utc


def build(scenario: Path):
    ref = scenario / "outputs" / "reference"
    tiles = list(csv.DictReader((ref / "tiles.csv").open(encoding="utf-8-sig")))
    slots = list(csv.DictReader((ref / "slots.csv").open(encoding="utf-8-sig")))
    weather = {w["slot_id"]: w for w in csv.DictReader((ref / "weather.csv").open(encoding="utf-8-sig"))}
    ra = np.array([float(t["ra_deg"]) for t in tiles])
    dec = np.array([float(t["dec_deg"]) for t in tiles])
    e = np.array([int(t["nominal_exptime_seconds"]) // TICK for t in tiles])
    value = np.array([int(t["n_lrg"]) + int(t["n_elg"]) + 1.7 * int(t["n_qso"]) + 0.45 * int(t["n_bgs"]) for t in tiles])
    a_from = np.array([utc(t["available_from_utc"]) for t in tiles])
    a_until = np.array([utc(t["available_until_utc"]) for t in tiles])
    regions = sorted({t["region_id"] for t in tiles})
    region = np.array([regions.index(t["region_id"]) for t in tiles])
    times, is_open, night, w0, eff = [], [], [], [], []
    for s in slots:
        start = utc(s["timestamp_utc"])
        w = weather[s["slot_id"]]
        ok = w["is_observable"] == "true"
        for j in range(int(float(s["duration_seconds"])) // TICK):
            times.append(start + j * TICK)
            is_open.append(ok)
            night.append(s["night_id"])
            w0.append(float(w["transparency"]) * float(w["sky_quality"]) / float(w["seeing_arcsec"]) if ok else 0.0)
            eff.append(float(w["instrument_efficiency"]) if ok else 0.0)
    times, is_open, w0, eff = np.array(times), np.array(is_open), np.array(w0), np.array(eff)
    nights = sorted(set(night))
    night_idx = np.array([nights.index(n) for n in night])
    # 每个格的“本夜结束时刻”
    night_end = np.zeros(len(times))
    for n in range(len(nights)):
        idx = np.nonzero(night_idx == n)[0]
        night_end[idx] = times[idx[-1]] + TICK
    # 以曝光中点的几何评估从每个格开始的价值；按曝光时长分组计算
    q = np.zeros((len(tiles), len(times)), dtype=np.float32)
    ok = np.zeros((len(tiles), len(times)), dtype=bool)
    _alt0, am0, lq0 = geometry_quality(scenario, times, ra, dec)
    q_start = (lq0 / am0).astype(np.float32)  # 曝光开始时刻的几何（快照给出的就是这个）
    for ex in np.unique(e):
        rows = np.nonzero(e == ex)[0]
        alt, am, lq = geometry_quality(scenario, times + ex * TICK / 2.0, ra[rows], dec[rows])
        q[rows] = (lq / am).astype(np.float32)
        end = times + ex * TICK
        ok[rows] = ((alt >= 30.0) & (times[None, :] + ex * TICK / 2.0 >= a_from[rows, None])
                    & (end[None, :] <= a_until[rows, None]) & (end[None, :] <= night_end[None, :]))
    ok_geo = ok.copy()  # 定价只能用几何与可用期：真实的天气关闭事先不可知
    # 寒潮（ALL 范围的效率乘数）的真实时段：只用于“全知寒潮”的上限实验
    cold = np.ones(len(times))
    for ev in csv.DictReader((ref / "weather_events.csv").open(encoding="utf-8-sig")):
        if ev["condition"] == "cold_wave" and ev["spatial_scope_type"] == "ALL":
            inside = (times >= utc(ev["actual_start_utc"])) & (times < utc(ev["actual_end_utc"]))
            cold[inside] *= float(ev["instrument_efficiency_multiplier"])
    # 曝光期间必须一直开放：用前缀和检查
    closed_prefix = np.concatenate([[0], np.cumsum(~is_open)])
    for ex in np.unique(e):
        rows = np.nonzero(e == ex)[0]
        idx = np.arange(len(times))
        end_idx = np.minimum(idx + ex, len(times))
        all_open = (closed_prefix[end_idx] - closed_prefix[idx]) == 0
        ok[rows] &= all_open[None, :]
    night_last = np.zeros(len(times), dtype=int)  # 每个格所在夜的最后一格（含）
    for n in range(len(nights)):
        idx = np.nonzero(night_idx == n)[0]
        night_last[idx] = idx[-1]
    # 价值分解实验用：每夜开放格的天气样本；关闭格的天气按当夜均值填充、效率按均值 0.95 填充
    all_open = w0[is_open]
    night_w0 = [w0[(night_idx == n) & is_open] for n in range(len(nights))]
    night_w0 = [x if len(x) else all_open for x in night_w0]
    w0_filled = np.where(is_open, w0, np.array([night_w0[n].mean() for n in night_idx]))
    eff_filled = np.where(is_open, eff, 0.95)
    return {"value": value, "e": e, "q": q, "q_start": q_start, "ok": ok, "ok_geo": ok_geo, "times": times, "is_open": is_open,
            "region": region, "n_regions": len(regions), "cold": cold,
            "night_idx": night_idx, "night_last": night_last, "w0": w0, "eff": eff,
            "night_w0": night_w0, "w0_filled": w0_filled, "eff_filled": eff_filled}


def expected_value_factor(q_geo, w_samples):
    """E[W0·(1+档位加成(W0·q))]：对历史天气样本积分，q_geo 可以是数组。"""
    w = np.asarray(w_samples)[:, None]
    q0 = w * np.asarray(q_geo).reshape(1, -1)
    bonus = np.where(q0 >= 0.65, 0.25, np.where(q0 >= 0.40, 0.15, 0.08))
    return (w * (1.0 + bonus)).mean(axis=0).reshape(np.shape(q_geo))


def realized(value, q_geo, w0, eff):
    """与评分器一致的单次曝光得分（忽略分段变化）：V·q·eff·(1+档位加成)，档位按不含效率的质量判定。"""
    q0 = np.minimum(w0 * 1.0, 3.0) * q_geo
    bonus = np.where(q0 >= 0.65, 0.25, np.where(q0 >= 0.40, 0.15, 0.08))
    return value * q0 * eff * (1.0 + bonus)


def _base_values(d, known, integrate_weather):
    """与 t0 无关的逐格价值（未乘可行性掩码）。按信息设定缓存一次；逐元素运算与逐次重算完全相同。"""
    key = ("_base", tuple(sorted(known)), bool(integrate_weather))
    if key not in d:
        value, q = d["value"], d["q"]
        if "weather" in known:
            eff = d["eff_filled"][None, :] if "efficiency" in known else 0.95
            base = realized(value[:, None], q, d["w0_filled"][None, :], eff)
        elif integrate_weather:
            samples = np.quantile(d["w0"][d["is_open"]], np.linspace(0.025, 0.975, 20))
            grid = np.linspace(0.0, 1.05, 106)
            if "nights" in known:
                factor = np.zeros_like(q)
                for k, night_samples in enumerate(d["night_w0"]):
                    cols = d["night_idx"] == k
                    factor[:, cols] = np.interp(q[:, cols], grid, expected_value_factor(grid, night_samples))
            else:
                factor = np.interp(q, grid, expected_value_factor(grid, samples))
            base = value[:, None] * q * factor * 0.95
        else:
            base = realized(value[:, None], q, float(d["w0"][d["is_open"]].mean()), 0.95)
        d[key] = base
    return d[key]


def dual_prices(d, bin_ticks=24, iterations=300, t0=0, done=None, lam0=None, verbose=True, horizon_nights=None,
                integrate_weather=False, cov_scale=None, cov_shift=None, level=None, phi=0.82, cold_known=False,
                known=()):
    """时段粒度 bin_ticks 格；返回每块天区的 μ_i（留到将来能拿到的剩余价值）。只考虑 t0 之后、未完成的天区。

    known：天气信息价值实验里假设事先知道的真实未来（只用于求上限，agent 拿不到）：
      closures   哪些格关闭（可行性与容量都按真实开放格计）
      nights     每夜的天气分布（不知道夜内何时好坏）
      weather    每格的真实天气（关闭格按当夜均值填充）
      efficiency 每格的仪器效率（与 weather 同用）

    实现上只在 [t0 所在时段, 视野末所在时段] 的窗口内、对窗口内有正价值的天区计算：窗口外价值恒为 0、
    无正价值天区的约化收益恒 ≤ 0，都不会被选中，结果与在全矩阵上计算逐位相同（λ 仍在全部时段上更新）。
    """
    value, e, q = d["value"], d["e"], d["q"]
    n_tiles, n_ticks = q.shape
    n_bins = (n_ticks + bin_ticks - 1) // bin_ticks
    night_idx = d["night_idx"]
    lo_b = t0 // bin_ticks
    limit_tick = n_ticks
    if horizon_nights is not None:
        limit = night_idx[min(t0, n_ticks - 1)] + horizon_nights
        limit_tick = int(np.searchsorted(night_idx, limit, side="left"))
    hi_b = min(n_bins, -(-limit_tick // bin_ticks))
    lo, hi = lo_b * bin_ticks, min(n_ticks, hi_b * bin_ticks)
    nb = hi_b - lo_b
    ok = (d["ok"] if "closures" in known else d["ok_geo"])[:, lo:hi].copy()
    ok[:, :t0 - lo] = False
    if done is not None:
        ok[done] = False
    if limit_tick < hi:
        ok[:, limit_tick - lo:] = False
    open_rate = 1.0 if "closures" in known else float(d["is_open"].mean())
    if integrate_weather and level is not None and not ({"weather", "nights"} & set(known)):
        # 按天气持续性缩放各夜的天气分布：今晚用当前水平，第 k 晚回归均值 phi^k（只算窗口内的夜）
        samples = np.quantile(d["w0"][d["is_open"]], np.linspace(0.025, 0.975, 20))
        grid = np.linspace(0.0, 1.05, 106)
        mean = float(samples.mean())
        night0 = int(night_idx[min(t0, n_ticks - 1)])
        nights_w = night_idx[lo:hi]
        q_w = q[:, lo:hi]
        factor = np.zeros_like(q_w)
        for k in range(int(nights_w.max()) - night0 + 1):
            cols = nights_w == night0 + k
            if not cols.any():
                continue
            m = (mean + phi ** k * (level - mean)) / mean
            factor[:, cols] = np.interp(q_w[:, cols], grid, expected_value_factor(grid, samples * m))
        v = np.where(ok, value[:, None] * q_w * factor * 0.95, 0.0)
    else:
        v = np.where(ok, _base_values(d, known, integrate_weather)[:, lo:hi], 0.0)
    if cold_known:
        v = v * d["cold"][None, lo:hi]
    if cov_scale is not None:  # 覆盖边际纳入规划：科学分 × (1 + w·E_after) + w·B·(E_after − E_now)
        v = np.where(ok, v * cov_scale[:, None] + cov_shift[:, None], 0.0)
    pad = nb * bin_ticks - (hi - lo)
    if pad:
        v = np.pad(v, ((0, 0), (0, pad)))
    v = v.reshape(n_tiles, nb, bin_ticks).max(axis=2) * open_rate
    future = np.arange(n_ticks) >= t0
    if "closures" in known:  # 容量也按真实开放的格数计
        future = future & d["is_open"]
    future = np.pad(future.astype(float), (0, n_bins * bin_ticks - n_ticks))
    capacity = future.reshape(n_bins, bin_ticks).sum(axis=1) * open_rate
    cap_max = max(1.0, capacity.max())
    v_max = v.max() if nb == n_bins else max(v.max(), 0.0)  # 窗口外的时段价值为 0
    active = np.nonzero(v.max(axis=1) > 0)[0]
    va, ea = v[active], e[active]
    rows = np.arange(len(active))
    lam = np.zeros(n_bins) if lam0 is None else lam0.copy()
    demand = np.zeros(n_bins)
    for it in range(iterations):
        reduced = va - ea[:, None] * lam[None, lo_b:hi_b]
        best = reduced.argmax(axis=1)
        pos = reduced[rows, best] > 0
        demand[:] = 0.0
        demand[lo_b:hi_b] = np.bincount(best[pos], weights=ea[pos], minlength=nb)
        step = 0.5 / (1 + it) ** 0.5 * (v_max / cap_max)
        lam = np.maximum(0.0, lam + step * (demand - capacity) / cap_max)
    reduced = va - ea[:, None] * lam[None, lo_b:hi_b]
    best = reduced.argmax(axis=1)
    gain_active = reduced[rows, best]
    gain = np.zeros(n_tiles)
    gain[active] = np.maximum(0.0, gain_active)
    if verbose:
        pos = gain_active > 0
        demand[:] = 0.0
        demand[lo_b:hi_b] = np.bincount(best[pos], weights=ea[pos], minlength=nb)
        over = np.clip(demand - capacity, 0, None).sum() / max(1.0, capacity.sum())
        print(f"  对偶价格：选中天区 {int(pos.sum())}，需求/容量 = {demand.sum() / max(1.0, capacity.sum()):.2f}，超载比例 = {over:.2f}")
    v_full = np.zeros((n_tiles, n_bins))
    v_full[:, lo_b:hi_b] = v
    return gain, lam, v_full, bin_ticks


def exact_prices(d, bin_ticks=24):
    """用 HiGHS 精确求解分配问题的线性松弛，返回每块天区的对偶价格 μ_i。"""
    from scipy.optimize import linprog
    from scipy.sparse import coo_matrix

    value, e, q, ok = d["value"], d["e"], d["q"], d["ok"]
    n_bins = (q.shape[1] + bin_ticks - 1) // bin_ticks
    pad = n_bins * bin_ticks - q.shape[1]
    w_mean = float(d["w0"][d["is_open"]].mean())
    open_rate = float(d["is_open"].mean())
    v = np.where(ok, realized(value[:, None], q, w_mean, 0.95), 0.0)
    v = np.pad(v, ((0, 0), (0, pad))).reshape(len(value), n_bins, bin_ticks).max(axis=2) * open_rate
    capacity = np.pad(np.ones(q.shape[1]), (0, pad)).reshape(n_bins, bin_ticks).sum(axis=1) * open_rate
    ii, bb = np.nonzero(v > 0)
    n_var = len(ii)
    rows = np.concatenate([ii, len(value) + bb])
    cols = np.concatenate([np.arange(n_var), np.arange(n_var)])
    data = np.concatenate([np.ones(n_var), e[ii].astype(float)])
    a_ub = coo_matrix((data, (rows, cols)), shape=(len(value) + n_bins, n_var)).tocsr()
    b_ub = np.concatenate([np.ones(len(value)), capacity])
    res = linprog(-v[ii, bb], A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs")
    mu = -res.ineqlin.marginals[:len(value)]
    print(f"  精确 LP：变量 {n_var}，目标值 {-res.fun:.0f}，状态 {res.status}，μ>0 的天区 {int((mu > 1e-9).sum())}")
    return np.maximum(0.0, mu)


def jain(x):
    total = x.sum()
    return 0.0 if total <= 0 else total * total / (len(x) * (x * x).sum())


def simulate(d, rule: str, mu=None, beta=1.0, replan=False, plan_kwargs=None, iters=(300, 150), at_start=False,
             coverage=0.0, plan_coverage=False, replan_ticks=None, warm=False, persist=False, rollout=None):
    value, e, q, ok = d["value"], d["e"], d["q"], d["ok"]
    n = q.shape[1]
    done = np.zeros(len(value), dtype=bool)
    t, geo, count, ratio = 0, 0.0, 0, []
    best_q = np.where(ok, q, 0).max(axis=1)
    counts = np.zeros(d["n_regions"])
    base_total = 0.0
    current_night, lam, last_plan, last_level = -1, None, -10**9, None
    while t < n:
        due = replan_ticks is not None and t - last_plan >= replan_ticks
        if replan and (d["night_idx"][t] != current_night or due):
            current_night, last_plan = d["night_idx"][t], t
            kwargs = dict(plan_kwargs or {})
            if persist:
                # 今晚已观测到的天气水平（开放格的 W0 均值）；今晚还没有观测时用上一晚的水平
                tonight = np.nonzero((d["night_idx"] == d["night_idx"][t]) & d["is_open"] & (np.arange(n) < t))[0]
                if len(tonight):
                    last_level = float(d["w0"][tonight].mean())
                if last_level is not None:
                    kwargs["level"] = last_level
            if plan_coverage and coverage > 0:
                total, sq = counts.sum(), (counts * counts).sum()
                after = (total + 1) ** 2 / (len(counts) * (sq + 2 * counts[d["region"]] + 1))
                kwargs["cov_scale"] = 1.0 + coverage * after
                kwargs["cov_shift"] = coverage * base_total * (after - jain(counts))
            mu, lam, _v, _ = dual_prices(d, iterations=iters[1] if lam is not None else iters[0], t0=t, done=done,
                                         lam0=lam if warm or (plan_kwargs or {}).get("horizon_nights") is None else None,
                                         verbose=False, **kwargs)
            mu = beta * mu
        if not d["is_open"][t]:
            t += 1
            continue
        avail = ok[:, t] & ~done
        if not avail.any():
            t += 1
            continue
        truth = realized(value, q[:, t], d["w0"][t], d["eff"][t])  # 按曝光中点几何结算
        gain = realized(value, d["q_start"][:, t], d["w0"][t], d["eff"][t]) if at_start else truth
        if coverage > 0:
            # 与 agent 相同的 Jain 指数精确边际：0.35·[(B+b)·E(x+e_r) − B·E(x)]
            base_now = gain / np.where(d["w0"][t] * q[:, t] >= 0.65, 1.25, np.where(d["w0"][t] * q[:, t] >= 0.40, 1.15, 1.08))
            e_now = jain(counts)
            total, sq = counts.sum(), (counts * counts).sum()
            after = (total + 1) ** 2 / (len(counts) * (sq + 2 * counts[d["region"]] + 1))
            gain = gain + coverage * ((base_total + base_now) * after - base_total * e_now)
        if rule == "greedy":
            score = gain / e
        elif rule == "timing":
            end = d["night_last"][t] + 1
            tonight = np.where(ok[:, t:end], q[:, t:end], 0).max(axis=1)
            factor = np.where(tonight > q[:, t], q[:, t] / np.maximum(tonight, 1e-9), 1.0)
            score = gain / e * factor
        elif rule == "price+timing":
            end = d["night_last"][t] + 1
            tonight = np.where(ok[:, t:end], q[:, t:end], 0).max(axis=1)
            factor = np.where(tonight > q[:, t], q[:, t] / np.maximum(tonight, 1e-9), 1.0)
            score = (gain * factor - mu) / e
        else:
            score = (gain - mu) / e
        score = np.where(avail, score, -np.inf)
        i = int(score.argmax())
        if rollout is not None and rule == "price":
            i = rollout_pick(d, t, score, gain, mu, done, rollout)
        if not np.isfinite(score[i]):
            t += 1
            continue
        done[i] = True
        geo += truth[i]  # 含天气与档位加成的得分
        q0 = d["w0"][t] * q[i, t]
        base_total += truth[i] / (1.25 if q0 >= 0.65 else (1.15 if q0 >= 0.40 else 1.08))
        counts[d["region"][i]] += 1
        count += 1
        ratio.append(q[i, t] / best_q[i])
        t += int(e[i])
    if coverage > 0:
        geo += coverage * base_total * jain(counts)
    return count, geo, float(np.mean(ratio)), jain(counts)


def rollout_pick(d, t, score, gain, mu, done, params):
    """对价格排序前 K 名分别往后推演 H 格（沿用价格规则，假设当前天气持续、不关闭），按单位时间累计的 (收益 − μ) 选择。"""
    top_k, horizon = params
    value, e, q, ok = d["value"], d["e"], d["q"], d["ok_geo"]
    w0, eff = d["w0"][t], d["eff"][t]
    order = np.argsort(-score)[:top_k]
    best_i, best_rate = int(order[0]), -np.inf
    for k in order:
        if not np.isfinite(score[k]):
            break
        taken = done.copy()
        taken[k] = True
        reward = gain[k] - mu[k]
        now = t + int(e[k])
        while now < t + horizon and now < q.shape[1]:
            avail = ok[:, now] & ~taken
            if not avail.any():
                now += 1
                continue
            g = realized(value, q[:, now], w0, eff)
            s = np.where(avail, (g - mu) / e, -np.inf)
            j = int(s.argmax())
            taken[j] = True
            reward += g[j] - mu[j]
            now += int(e[j])
        rate = reward / (now - t)
        if rate > best_rate:
            best_i, best_rate = int(k), rate
    return best_i


def main(scenario: Path) -> None:
    d = build(scenario)
    base = None
    plan = {"horizon_nights": 14, "integrate_weather": True}
    cov = {"coverage": 0.35}
    lp = {"replan": True, "plan_kwargs": plan, "plan_coverage": True, **cov}
    base_1h = {**lp, "replan_ticks": 24}  # 与 agent 的最终设计一致：规划内覆盖、14 夜视野、天气积分、每小时重规划

    def knows(*items):
        return "price", None, {**base_1h, "plan_kwargs": {**plan, "known": items}}

    registry = {
        "greedy+cov": ("greedy", None, cov),
        "price 1h": ("price", None, base_1h),
        "完美预报": knows("closures", "weather", "efficiency"),
        "知道关闭": knows("closures"),
        "知道每夜天气": knows("nights"),
        "知道每格天气": knows("weather"),
        "天气+效率": knows("weather", "efficiency"),
        "持续性": ("price", None, {**base_1h, "persist": True}),
    }
    names = ["greedy+cov", "price 1h", "完美预报"]
    if "--variants" in sys.argv:
        names = sys.argv[sys.argv.index("--variants") + 1].split(",")
    variants = [(name, *registry[name]) for name in names]
    if "--exact" in sys.argv:
        variants.append(("exact", "price", exact_prices(d), {}))
    for name, rule, prices, kwargs in variants:
        count, geo, ratio, evenness = simulate(d, rule, prices, **kwargs)
        base = base or geo
        print(f"[{name:10}] 天区 {count:5d}  Σ 得分 = {geo:9.0f} ({(geo / base - 1) * 100:+.2f}%)  "
              f"几何质量/该天区最佳 = {ratio:.3f}  均匀度 = {evenness:.3f}  精确 = {float(geo)!r}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
