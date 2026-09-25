"""离线试验台（tools/plan_experiment.py）上的算力/结构敏感性：迭代次数、时段粒度、重规划频率、精确 LP、在线看不到效率。
用法：python offline_driver.py <scenario> [variants,逗号分隔]"""
import sys, time, json
from pathlib import Path
import numpy as np
sys.path.insert(0, '/home/steven/code/GOSIM26/tools')
import plan_experiment as pe
from plan_experiment import dual_prices, realized, jain, rollout_pick

def simulate(d, rule, mu=None, beta=1.0, replan=False, plan_kwargs=None, iters=(300, 150), coverage=0.0, plan_coverage=False,
             replan_ticks=None, rollout=None, eff_online=None, exact=False):
    """plan_experiment.simulate 的复制，加 eff_online（在线估值用固定效率而非真实效率）与 exact（每次重规划用 HiGHS 精确 LP 对偶）。"""
    value, e, q, ok = d["value"], d["e"], d["q"], d["ok"]
    n = q.shape[1]
    done = np.zeros(len(value), dtype=bool)
    t, geo, count, ratio = 0, 0.0, 0, []
    best_q = np.where(ok, q, 0).max(axis=1)
    counts = np.zeros(d["n_regions"]); base_total = 0.0
    current_night, lam, last_plan = -1, None, -10**9
    plan_time = 0.0; n_plans = 0
    while t < n:
        due = replan_ticks is not None and t - last_plan >= replan_ticks
        if replan and (d["night_idx"][t] != current_night or due):
            current_night, last_plan = d["night_idx"][t], t
            kwargs = dict(plan_kwargs or {})
            if plan_coverage and coverage > 0:
                total, sq = counts.sum(), (counts * counts).sum()
                after = (total + 1) ** 2 / (len(counts) * (sq + 2 * counts[d["region"]] + 1))
                kwargs["cov_scale"] = 1.0 + coverage * after
                kwargs["cov_shift"] = coverage * base_total * (after - jain(counts))
            t0 = time.time()
            if exact:
                mu = exact_replan(d, t, done, kwargs)
            else:
                mu, lam, _v, _ = dual_prices(d, iterations=iters[1] if lam is not None else iters[0], t0=t, done=done,
                                             lam0=None, verbose=False, **kwargs)
            plan_time += time.time() - t0; n_plans += 1
            mu = beta * mu
        if not d["is_open"][t]:
            t += 1; continue
        avail = ok[:, t] & ~done
        if not avail.any():
            t += 1; continue
        truth = realized(value, q[:, t], d["w0"][t], d["eff"][t])
        if eff_online is None:
            gain = truth
        else:
            eo = eff_online[t] if isinstance(eff_online, np.ndarray) else eff_online
            gain = realized(value, q[:, t], d["w0"][t], eo)
        if coverage > 0:
            base_now = gain / np.where(d["w0"][t] * q[:, t] >= 0.65, 1.25, np.where(d["w0"][t] * q[:, t] >= 0.40, 1.15, 1.08))
            e_now = jain(counts)
            total, sq = counts.sum(), (counts * counts).sum()
            after = (total + 1) ** 2 / (len(counts) * (sq + 2 * counts[d["region"]] + 1))
            gain = gain + coverage * ((base_total + base_now) * after - base_total * e_now)
        if rule == "greedy":
            score = gain / e
        else:
            score = (gain - mu) / e
        score = np.where(avail, score, -np.inf)
        i = int(score.argmax())
        if rollout is not None and rule == "price":
            i = rollout_pick(d, t, score, gain, mu, done, rollout)
        if not np.isfinite(score[i]):
            t += 1; continue
        done[i] = True
        geo += truth[i]
        q0 = d["w0"][t] * q[i, t]
        base_total += truth[i] / (1.25 if q0 >= 0.65 else (1.15 if q0 >= 0.40 else 1.08))
        counts[d["region"][i]] += 1
        count += 1
        ratio.append(q[i, t] / best_q[i])
        t += int(e[i])
    if coverage > 0:
        geo += coverage * base_total * jain(counts)
    return count, geo, float(np.mean(ratio)), jain(counts), plan_time, n_plans


def exact_replan(d, t0, done, kwargs):
    """与 dual_prices 相同的 v/容量，用 HiGHS 求分配问题线性松弛的精确对偶 μ。"""
    from scipy.optimize import linprog
    from scipy.sparse import coo_matrix
    bin_ticks = kwargs.get("bin_ticks", 24)
    _gain, _lam, v_full, _bt = dual_prices(d, iterations=0, t0=t0, done=done, verbose=False, **kwargs)
    v = v_full  # (n_tiles, n_bins)，窗口外为 0
    n_tiles, n_bins = v.shape
    n_ticks = d["q"].shape[1]
    open_rate = float(d["is_open"].mean())
    future = np.arange(n_ticks) >= t0
    future = np.pad(future.astype(float), (0, n_bins * bin_ticks - n_ticks))
    capacity = future.reshape(n_bins, bin_ticks).sum(axis=1) * open_rate
    e = d["e"]
    ii, bb = np.nonzero(v > 0)
    if len(ii) == 0:
        return np.zeros(n_tiles)
    n_var = len(ii)
    rows = np.concatenate([ii, n_tiles + bb]); cols = np.concatenate([np.arange(n_var), np.arange(n_var)])
    data = np.concatenate([np.ones(n_var), e[ii].astype(float)])
    a_ub = coo_matrix((data, (rows, cols)), shape=(n_tiles + n_bins, n_var)).tocsr()
    b_ub = np.concatenate([np.ones(n_tiles), capacity])
    res = linprog(-v[ii, bb], A_ub=a_ub, b_ub=b_ub, bounds=(0, None), method="highs")
    if res.status != 0:
        return np.zeros(n_tiles)
    mu = -res.ineqlin.marginals[:n_tiles]
    return np.maximum(0.0, mu)


def main():
    scenario = Path(sys.argv[1])
    want = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    d = pe.build(scenario)
    plan = {"horizon_nights": 14, "integrate_weather": True}
    lp = {"replan": True, "plan_kwargs": plan, "plan_coverage": True, "coverage": 0.35, "replan_ticks": 24}
    variants = {
        "base_1h": dict(lp),
        "iters4x": {**lp, "iters": (1200, 600)},
        "bin30_it4x": {**lp, "plan_kwargs": {**plan, "bin_ticks": 12}, "iters": (1200, 600)},
        "bin30": {**lp, "plan_kwargs": {**plan, "bin_ticks": 12}},
        "bin15_it2x": {**lp, "plan_kwargs": {**plan, "bin_ticks": 6}, "iters": (600, 300)},
        "replan30": {**lp, "replan_ticks": 12},
        "replan15": {**lp, "replan_ticks": 6},
        "effblind": {**lp, "eff_online": 0.95},
        "effcold": {**lp, "eff_online": "cold"},
        "effcold_plancold": {**lp, "eff_online": "cold", "plan_kwargs": {**plan, "cold_known": True}},
        "plancold": {**lp, "plan_kwargs": {**plan, "cold_known": True}},
        "effblind_replan30": {**lp, "replan_ticks": 12, "eff_online": 0.95},
        "rollout": {**lp, "rollout": (5, 24)},
        "exact_lp": {**lp, "exact": True},
        "perfect": {**lp, "plan_kwargs": {**plan, "known": ("closures", "weather", "efficiency")}},
    }
    names = want or ["base_1h", "iters4x", "bin30_it4x", "replan30", "effblind"]
    out = {}
    base = None
    for name in names:
        kw = dict(variants[name])
        if kw.get("eff_online") == "cold":
            kw["eff_online"] = 0.95 * d["cold"]
        t0 = time.time()
        count, geo, ratio, even, plan_time, n_plans = simulate(d, "price", None, **kw)
        base = base or geo
        out[name] = {"count": count, "score": geo, "ratio": ratio, "evenness": even, "seconds": time.time() - t0, "plan_seconds": plan_time, "plans": n_plans}
        print(f"[{scenario.name} {name:18}] 天区 {count:5d} Σ {geo:9.0f} ({(geo / base - 1) * 100:+.2f}%) 几何 {ratio:.3f} 均匀 {even:.3f} 用时 {time.time() - t0:.0f}s 规划 {plan_time:.0f}s/{n_plans}", flush=True)
    Path("/tmp/fable-0925c/offline").mkdir(parents=True, exist_ok=True)
    tag = sys.argv[3] if len(sys.argv) > 3 else ""
    Path(f"/tmp/fable-0925c/offline/{scenario.name}{tag}.json").write_text(json.dumps(out, indent=1))

if __name__ == "__main__":
    main()
