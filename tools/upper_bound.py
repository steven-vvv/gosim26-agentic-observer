#!/usr/bin/env python3
"""完美信息（全知天气）上界：任何算法在该场景上都不可能超过的分数。

    uv run python tools/upper_bound.py scenarios/comp-a-s101 [--agents base obs10]

1. 价值表：对每块天区、每个 150 秒网格上的开始时刻，完全按评分器规则计算这次曝光的真实得分——
   分段中点几何、每段开始时的合法性（可用期、高度 ≥ 30°、同一夜）、站点天气 × 区域事件乘数（按配置截断）、
   强制关闭、仪器效率（含寒潮与故障；假设故障一开始就被上报，两天后修复）、隐藏标签乘数、逐段档位与最优项目。
2. 上界：对“每个 150 秒时刻最多只能进行一次曝光”的约束做拉格朗日松弛。对任意价格 λ ≥ 0，
       L(λ) = Σ_τ λ_τ + Σ_i max(0, max_k (w_ik − Σ_{τ∈曝光} λ_τ))
   都是合法上界（弱对偶）；用次梯度把它压低，取见过的最小值。覆盖奖励取均匀度 = 1（w 里的基础分乘 1.35），
   REQUIRED 每块 +1000、正确的标签上报 +100、临时请求按全部完成计。
3. 可行排程：用最终价格按时间顺序贪心（与 agent 的规划规则相同，但知道全部天气），得到一个全知条件下可以做到的真实分数。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TICK = 150
PROGRAMS = ("DARK", "BRIGHT", "BACKUP")


def utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------- 几何（与 kit 的低精度公式一致）

def _days(t):
    return t / 86400.0 + 2440587.5 - 2451545.0


def lst_deg(t, lon):
    return (280.46061837 + 360.98564736629 * _days(t) + lon) % 360.0


def sun_radec(t):
    d = _days(t)
    ml = (280.460 + 0.9856474 * d) % 360.0
    ma = np.radians((357.528 + 0.9856003 * d) % 360.0)
    lon = np.radians((ml + 1.915 * np.sin(ma) + 0.020 * np.sin(2 * ma)) % 360.0)
    obl = np.radians(23.439 - 0.0000004 * d)
    return (np.degrees(np.arctan2(np.cos(obl) * np.sin(lon), np.cos(lon))) % 360.0,
            np.degrees(np.arcsin(np.sin(obl) * np.sin(lon))))


def moon_radec(t):
    d = _days(t)
    ml = np.radians((218.316 + 13.176396 * d) % 360.0)
    ma = np.radians((134.963 + 13.064993 * d) % 360.0)
    arg = np.radians((93.272 + 13.229350 * d) % 360.0)
    lon = ml + np.radians(6.289) * np.sin(ma)
    lat = np.radians(5.128) * np.sin(arg)
    obl = np.radians(23.439 - 0.0000004 * d)
    x = np.cos(lon) * np.cos(lat)
    y = np.sin(lon) * np.cos(lat) * np.cos(obl) - np.sin(lat) * np.sin(obl)
    z = np.sin(lon) * np.cos(lat) * np.sin(obl) + np.sin(lat) * np.cos(obl)
    return np.degrees(np.arctan2(y, x)) % 360.0, np.degrees(np.arcsin(z))


def separation(ra1, dec1, ra2, dec2):
    ra1, dec1, ra2, dec2 = map(np.radians, (ra1, dec1, ra2, dec2))
    c = np.sin(dec1) * np.sin(dec2) + np.cos(dec1) * np.cos(dec2) * np.cos(ra1 - ra2)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def alt_az(t, ra, dec, lat_deg, lon_deg):
    """t: [时刻]，ra/dec: [天区]，返回 [天区, 时刻] 的高度与方位角（度）。"""
    lat = np.radians(lat_deg)
    ha = np.radians((lst_deg(np.asarray(t), lon_deg)[None, :] - np.asarray(ra)[:, None] + 180.0) % 360.0 - 180.0)
    dec_r = np.radians(np.asarray(dec))[:, None]
    s = np.sin(lat) * np.sin(dec_r) + np.cos(lat) * np.cos(dec_r) * np.cos(ha)
    alt = np.arcsin(np.clip(s, -1.0, 1.0))
    cos_alt = np.maximum(1e-12, np.cos(alt))
    sin_az = -np.sin(ha) * np.cos(dec_r) / cos_alt
    cos_az = (np.sin(dec_r) - np.sin(alt) * np.sin(lat)) / (cos_alt * max(1e-12, math.cos(lat)))
    return np.degrees(alt), np.degrees(np.arctan2(sin_az, cos_az)) % 360.0


def airmass(alt):
    z = 90.0 - alt
    raw = 1.0 / (np.cos(np.radians(z)) + 0.50572 * np.maximum(96.07995 - z, 1e-6) ** -1.6364)
    out = raw * (1.0 + 0.50572 * 96.07995 ** -1.6364)
    return np.where(alt > 0, out, np.inf)


def lunar_factor(t, ra, dec, site, lunar):
    sun_ra, sun_dec = sun_radec(np.asarray(t))
    moon_ra, moon_dec = moon_radec(np.asarray(t))
    illum = (1.0 - np.cos(np.radians(separation(sun_ra, sun_dec, moon_ra, moon_dec)))) / 2.0
    lat = np.radians(site["latitude_deg"])
    ha = np.radians((lst_deg(np.asarray(t), site["longitude_deg"]) - moon_ra + 180.0) % 360.0 - 180.0)
    s = np.sin(lat) * np.sin(np.radians(moon_dec)) + np.cos(lat) * np.cos(np.radians(moon_dec)) * np.cos(ha)
    moon_alt = np.degrees(np.arcsin(np.clip(s, -1.0, 1.0)))
    weight = lunar["maximum_penalty"] * illum * np.sin(np.radians(np.maximum(0.0, moon_alt))) ** lunar["altitude_exponent"]
    sep = separation(np.asarray(ra)[:, None], np.asarray(dec)[:, None], moon_ra[None, :], moon_dec[None, :])
    return np.clip(1.0 - weight[None, :] * np.exp(-sep / lunar["angular_decay_scale_deg"]), 0.0, 1.0)


# ---------------------------------------------------------------- 场景与价值表

def load(scenario: Path) -> dict:
    ref = scenario / "outputs" / "reference"
    cfg = lambda name: json.loads((scenario / "config" / name).read_text(encoding="utf-8"))
    rows = lambda name: list(csv.DictReader((ref / name).open(encoding="utf-8-sig")))
    tiles = rows("tiles.csv")
    index = {t["tile_id"]: i for i, t in enumerate(tiles)}
    value = np.zeros(len(tiles))
    for target in rows("targets.csv"):
        value[index[target["tile_id"]]] += float(target["science_weight"])
    anomaly = np.ones(len(tiles))
    ntags = np.zeros(len(tiles))
    score = cfg("score_config.json")
    factors = {"nova": float(score.get("anomaly_tags", {}).get("nova_factor", 1.5)),
               "reddening": float(score.get("anomaly_tags", {}).get("reddening_factor", 0.8))}
    if (ref / "tile_anomalies.csv").exists():
        for row in rows("tile_anomalies.csv"):
            anomaly[index[row["tile_id"]]] *= factors[row["anomaly_tag"]]
            ntags[index[row["tile_id"]]] += 1
    return {"tiles": tiles, "index": index, "value": value, "anomaly": anomaly, "ntags": ntags,
            "slots": rows("slots.csv"), "weather": {w["slot_id"]: w for w in rows("weather.csv")},
            "events": rows("weather_events.csv"), "requests": rows("observation_requests.csv"),
            "score": score, "calendar": cfg("calendar_config.json"), "tile_cfg": cfg("tile_config.json"),
            "weather_cfg": cfg("weather_config.json")}


def effective_weather(sc: dict, slot_mid_alt, slot_mid_az):
    """[天区, 时隙] 的有效天气：站点天气（已含 ALL 范围事件）× 区域事件乘数，按配置截断；区域强制关闭则不可观测。"""
    tiles, slots = sc["tiles"], sc["slots"]
    n_t, n_s = len(tiles), len(slots)
    starts = np.array([utc(s["timestamp_utc"]) for s in slots])
    ends = starts + np.array([float(s["duration_seconds"]) for s in slots])
    site = {k: np.zeros(n_s) for k in ("seeing_arcsec", "transparency", "sky_quality", "instrument_efficiency")}
    site_open = np.zeros(n_s, dtype=bool)
    for j, s in enumerate(slots):
        w = sc["weather"][s["slot_id"]]
        site_open[j] = w["is_observable"] == "true"
        if site_open[j]:
            for key, column in site.items():
                column[j] = float(w[key])
    mult = {k: np.ones((n_t, n_s)) for k in site}
    closed = np.zeros((n_t, n_s), dtype=bool)
    region = np.array([t["region_id"] for t in tiles])
    ra = np.array([float(t["ra_deg"]) for t in tiles])
    dec = np.array([float(t["dec_deg"]) for t in tiles])
    repair = float(sc["score"].get("fault_response", {}).get("repair_duration_days", 2)) * 86400
    for ev in sc["events"]:
        if ev["spatial_scope_type"] == "ALL":
            continue
        start, end = utc(ev["actual_start_utc"]), utc(ev["actual_end_utc"])
        if ev["condition"] == "instrument_fault":
            end = min(end, start + repair)  # 全知：故障一开始就被正确上报，两天后修复
        active = (starts < end) & (ends > start)
        if not active.any():
            continue
        payload = json.loads(ev["spatial_scope_payload"]) if ev["spatial_scope_payload"] else {}
        kind = ev["spatial_scope_type"]
        if kind == "REGION_SET":
            applies = np.isin(region, payload["region_ids"])[:, None] & active[None, :]
        elif kind == "TILE_SET":
            ids = set(payload["tile_ids"])
            applies = np.array([t["tile_id"] in ids for t in tiles])[:, None] & active[None, :]
        elif kind == "SKY_CAP_ICRS":
            inside = separation(ra, dec, float(payload["ra_deg"]), float(payload["dec_deg"])) <= float(payload["radius_deg"])
            applies = inside[:, None] & active[None, :]
        else:  # HORIZON_SECTOR：按时隙中点的高度与方位
            a0, a1 = float(payload["azimuth_start_deg"]), float(payload["azimuth_end_deg"])
            az_ok = (slot_mid_az >= a0) & (slot_mid_az <= a1) if a0 <= a1 else (slot_mid_az >= a0) | (slot_mid_az <= a1)
            alt_ok = (slot_mid_alt >= float(payload["min_altitude_deg"])) & (slot_mid_alt <= float(payload["max_altitude_deg"]))
            applies = az_ok & alt_ok & active[None, :]
        if ev["force_close"] == "true":
            closed |= applies
        for key, column in (("seeing_arcsec", "seeing_multiplier"), ("transparency", "transparency_multiplier"),
                            ("sky_quality", "sky_quality_multiplier"), ("instrument_efficiency", "instrument_efficiency_multiplier")):
            mult[key] = np.where(applies, mult[key] * float(ev[column]), mult[key])
    bounds = sc["weather_cfg"]["quality"]
    eff = {k: np.clip(site[k][None, :] * mult[k], float(bounds[k]["minimum"]), float(bounds[k]["maximum"])) for k in site}
    is_open = site_open[None, :] & ~closed
    return eff, is_open


def value_table(sc: dict):
    """w_base[i, k]、w_bonus[i, k]：天区 i 从第 k 个 150 秒格开始曝光的基础分与最优项目加成；不可行为 -inf。"""
    tiles, slots = sc["tiles"], sc["slots"]
    site = sc["calendar"]["site"]
    lunar = sc["tile_cfg"]["lunar_model"]
    min_alt = float(sc["tile_cfg"]["geometry"]["minimum_altitude_deg"])
    score = sc["score"]
    dark, bright = float(score["quality_thresholds"]["dark"]), float(score["quality_thresholds"]["bright"])
    bonus = {p: float(score["program_bonus"][p]) for p in PROGRAMS}
    n_t, n_s = len(tiles), len(slots)
    slot_len = int(float(slots[0]["duration_seconds"]))
    per = slot_len // TICK
    starts = np.array([utc(s["timestamp_utc"]) for s in slots])
    night = np.array([s["night_id"] for s in slots])
    ra = np.array([float(t["ra_deg"]) for t in tiles])
    dec = np.array([float(t["dec_deg"]) for t in tiles])
    exposure = np.array([int(t["nominal_exptime_seconds"]) for t in tiles])
    a_from = np.array([utc(t["available_from_utc"]) for t in tiles])
    a_until = np.array([utc(t["available_until_utc"]) for t in tiles])
    mid_alt, mid_az = alt_az(starts + slot_len / 2.0, ra, dec, site["latitude_deg"], site["longitude_deg"])
    eff, is_open = effective_weather(sc, mid_alt, mid_az)
    q_eff = eff["instrument_efficiency"] * eff["transparency"] * eff["sky_quality"] / eff["seeing_arcsec"]
    q_free = eff["transparency"] * eff["sky_quality"] / eff["seeing_arcsec"]
    q_eff = np.where(is_open, q_eff, 0.0)
    q_free = np.where(is_open, q_free, 0.0)
    n_ticks = n_s * per
    w_base = np.full((n_t, n_ticks), -np.inf, dtype=np.float32)
    w_bonus = np.zeros((n_t, n_ticks), dtype=np.float32)
    for e in np.unique(exposure):
        rows = np.nonzero(exposure == e)[0]
        for offset in range(per):
            # 这一组的分段结构：(相对起始时隙的时隙偏移, 段开始相对时刻, 段长)
            segments, remaining, rel, slot_shift, first = [], int(e), offset * TICK, 0, True
            while remaining > 0:
                length = min(remaining, slot_len - (rel if first else 0))
                segments.append((slot_shift, (rel if first else 0), length))
                remaining -= length
                slot_shift += 1
                first = False
            feasible = np.ones((len(rows), n_s), dtype=bool)
            base_total = np.zeros((len(rows), n_s))
            bonus_p = {p: np.zeros((len(rows), n_s)) for p in PROGRAMS}
            for shift, seg_rel, length in segments:
                idx = np.arange(n_s) + shift
                valid = idx < n_s
                idx = np.minimum(idx, n_s - 1)
                valid &= night[idx] == night  # 夜按时隙顺序连续排列：分段所在时隙与起始时隙同夜即可
                seg_start = starts[idx] + seg_rel
                seg_mid = seg_start + length / 2.0
                alt_start, _ = alt_az(seg_start, ra[rows], dec[rows], site["latitude_deg"], site["longitude_deg"])
                alt_mid, _ = alt_az(seg_mid, ra[rows], dec[rows], site["latitude_deg"], site["longitude_deg"])
                legal = (alt_start >= min_alt) & (seg_start[None, :] >= a_from[rows, None]) & (seg_start[None, :] < a_until[rows, None])
                open_ = is_open[rows][:, idx]
                feasible &= legal & open_ & valid[None, :]
                am = airmass(alt_mid)
                lq = lunar_factor(seg_mid, ra[rows], dec[rows], site, lunar)
                combined = np.minimum(q_eff[rows][:, idx] / am, 3.0) * lq
                band_q = np.minimum(q_free[rows][:, idx] / am, 3.0) * lq
                base = sc["value"][rows, None] * (length / e) * combined * sc["anomaly"][rows, None]
                base_total += base
                band = np.where(band_q >= dark, 0, np.where(band_q >= bright, 1, 2))
                for k, p in enumerate(PROGRAMS):
                    bonus_p[p] += np.where(band == k, base * bonus[p], 0.0)
            best_bonus = np.maximum(np.maximum(bonus_p["DARK"], bonus_p["BRIGHT"]), bonus_p["BACKUP"])
            cols = np.arange(n_s) * per + offset
            w_base[np.ix_(rows, cols)] = np.where(feasible, base_total, -np.inf).astype(np.float32)
            w_bonus[np.ix_(rows, cols)] = np.where(feasible, best_bonus, 0.0).astype(np.float32)
    return w_base, w_bonus, exposure // TICK, per


# ---------------------------------------------------------------- 上界与可行排程

def _reduced_best(w, span, lam):
    """在价格 λ 下每块天区的最佳开始格与剩余价值；返回 (L 中的天区部分, 选中曝光覆盖每格的次数, μ)。"""
    n_t, n_k = w.shape
    prefix = np.concatenate([[0.0], np.cumsum(lam)])
    cover = np.zeros(n_k + 17)
    mu = np.zeros(n_t)
    total = 0.0
    for s in np.unique(span):
        rows = np.nonzero(span == s)[0]
        cost = prefix[s:n_k + s] - prefix[:n_k]
        reduced = w[rows] - cost[None, :]
        k = reduced.argmax(axis=1)
        gain = reduced[np.arange(len(rows)), k]
        chosen = gain > 0
        mu[rows] = np.maximum(gain, 0.0)
        total += float(gain[chosen].sum())
        np.add.at(cover, k[chosen], 1.0)
        np.add.at(cover, k[chosen] + s, -1.0)
    return total, np.cumsum(cover)[: n_k + 16], mu


def greedy_schedule(w, span, per, mu):
    """时间顺序贪心：每次选 (w − μ)/占用格数 最大的可行天区（μ=0 即按价值密度），没有可行天区就等到下一个时隙边界。"""
    n_t, n_k = w.shape
    done = np.zeros(n_t, dtype=bool)
    chosen = []
    t = 0
    while t < n_k:
        column = w[:, t]
        ok = np.isfinite(column) & ~done
        if not ok.any():
            t = (t // per + 1) * per
            continue
        score = np.where(ok, (column - mu) / span, -np.inf)
        i = int(score.argmax())
        done[i] = True
        chosen.append((i, t))
        t += int(span[i])
    return chosen


def dual_bound(w, span, per, iterations=800):
    """拉格朗日对偶上界（Polyak 步长）；同时维护目前最好的可行排程。返回 (上界, 最好的可行排程, 其 w 目标值)。"""
    n_t, n_k = w.shape
    usable = np.zeros(n_k + 16, dtype=bool)  # 至少有一个可行曝光覆盖的格子；其余格子的价格恒为 0
    feasible = np.isfinite(w)
    for s in np.unique(span):
        rows = np.nonzero(span == s)[0]
        starts = np.nonzero(feasible[rows].any(axis=0))[0]
        diff = np.zeros(n_k + 17)
        np.add.at(diff, starts, 1)
        np.add.at(diff, starts + s, -1)
        usable |= np.cumsum(diff)[: n_k + 16] > 0
    objective = lambda sched: float(sum(w[i, t] for i, t in sched))
    best_sched = greedy_schedule(w, span, per, np.zeros(n_t))
    target = objective(best_sched)
    lam = np.zeros(n_k + 16)
    best_bound, theta, stall = np.inf, 1.0, 0
    for it in range(iterations):
        part, usage, mu = _reduced_best(w, span, lam)
        bound = float(lam.sum()) + part
        if bound < best_bound - 1e-6:
            best_bound, stall = bound, 0
        else:
            stall += 1
            if stall >= 20:
                theta, stall = theta / 2.0, 0
        if it % 40 == 39 or it == iterations - 1:
            sched = greedy_schedule(w, span, per, mu)
            value = objective(sched)
            if value > target:
                best_sched, target = sched, value
        g = np.where(usable, usage - 1.0, 0.0)  # λ 的上升方向（L 的负次梯度）
        norm = float((g * g).sum())
        if norm <= 0 or theta < 1e-4:
            break
        lam = np.where(usable, np.maximum(0.0, lam + theta * (bound - target) / norm * g), 0.0)
    return best_bound, best_sched, target


def evaluate(sc, chosen, w_base, w_bonus):
    """可行排程的真实分数：基础分 + 加成 + 覆盖（真实均匀度）+ 标签 − REQUIRED 缺失 − FLEXIBLE 缺额（请求不计）。"""
    tiles = sc["tiles"]
    score = sc["score"]
    base = sum(float(w_base[i, t]) for i, t in chosen)
    bonus = sum(float(w_bonus[i, t]) for i, t in chosen)
    regions = sorted({t["region_id"] for t in tiles})
    counts = {r: 0 for r in regions}
    flexible = {r: 0 for r in regions}
    done = set()
    for i, _t in chosen:
        done.add(i)
        counts[tiles[i]["region_id"]] += 1
        if tiles[i]["scheduling_class"] == "FLEXIBLE":
            flexible[tiles[i]["region_id"]] += 1
    x = np.array([counts[r] for r in regions], dtype=float)
    evenness = x.sum() ** 2 / (len(x) * (x * x).sum()) if x.sum() else 0.0
    coverage = float(score.get("coverage_bonus_weight", 0.0)) * base * evenness
    tags = 100.0 * sum(sc["ntags"][i] for i in done)
    missing = sum(1 for i, t in enumerate(tiles) if t["scheduling_class"] == "REQUIRED" and i not in done)
    shortfall = sum(max(0, int(score["flexible_quota_per_region"]) - flexible[r]) for r in regions)
    penalties = missing * float(score["penalties"]["required_miss"]) + shortfall * float(score["penalties"]["flexible_shortfall_per_tile"])
    return {"total": base + bonus + coverage + tags - penalties, "base": base, "bonus": bonus, "coverage": coverage,
            "evenness": evenness, "tags": tags, "penalties": penalties, "tiles": len(done)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--agents", nargs="*", default=["base", "obs10"], help="results/ 下用于对比的版本")
    parser.add_argument("--iterations", type=int, default=800)
    parser.add_argument("--out", type=Path, default=None, help="结果写入该 JSON（默认 results/upper_bound/<场景>.json）")
    args = parser.parse_args(argv)
    sc = load(args.scenario)
    score = sc["score"]
    w_base, w_bonus, span, per = value_table(sc)
    cov_w = float(score.get("coverage_bonus_weight", 0.0))
    extras = (1000.0 * np.array([t["scheduling_class"] == "REQUIRED" for t in sc["tiles"]]) + 100.0 * sc["ntags"])
    w = np.where(np.isfinite(w_base), (1.0 + cov_w) * w_base + w_bonus + extras[:, None], -np.inf).astype(np.float64)
    bound, chosen, _target = dual_bound(w, span, per, args.iterations)
    n_required = sum(t["scheduling_class"] == "REQUIRED" for t in sc["tiles"])
    request_max = sum(float(r["completion_reward"]) for r in sc["requests"])  # 每个请求的总奖励（已按所需天区数计）
    upper = bound - 1000.0 * n_required + request_max
    primal = evaluate(sc, chosen, w_base, w_bonus)
    feasible_pairs = int(np.isfinite(w_base).sum())
    print(f"{args.scenario.name}: 可行 (天区, 开始格) 组合 {feasible_pairs}")
    print(f"  完美信息上界（均匀度按 1 计）  {upper:10.1f}")
    print(f"  全知可行排程                    {primal['total']:10.1f}   (天区 {primal['tiles']}, 均匀度 {primal['evenness']:.3f}, "
          f"基础 {primal['base']:.0f}, 加成 {primal['bonus']:.0f}, 覆盖 {primal['coverage']:.0f}, 标签 {primal['tags']:.0f}, 罚 {primal['penalties']:.0f})")
    result = {"scenario": args.scenario.name, "upper_bound": upper, "primal": primal, "feasible_pairs": feasible_pairs}
    for agent in args.agents:
        path = ROOT / "results" / agent / f"{args.scenario.name}.json"
        if path.exists():
            total = json.loads(path.read_text(encoding="utf-8"))["total"]
            result[agent] = total
            print(f"  {agent:30} {total:10.1f}   = 上界的 {total / upper:.1%}，全知排程的 {total / primal['total']:.1%}")
    out = args.out or ROOT / "results" / "upper_bound" / f"{args.scenario.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
