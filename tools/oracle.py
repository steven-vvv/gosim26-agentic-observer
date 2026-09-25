#!/usr/bin/env python3
"""离线估算“更好的观测时机”能挽回多少质量（只用于分析，不进 agent）。

    uv run python tools/oracle.py scenarios/comp-a-s101 [runs/eval/diag-obs3/comp-a-s101]

几何按入门包 tile_geometry_simulator 的公式用 numpy 计算。去掉天气关闭的时隙后，用贪心求解
“天区 → 开始时刻”的分配（时间离散为 150 秒一格，曝光不重叠，每块天区最多一次）：

  (a) geometry：按 V × 月光因子 / airmass 选择，不知道天气；
  (b) clairvoyant：再乘以真实的站点天气项，相当于全知天气的上界。

给出运行目录时，同时统计该次运行实际观测的几何质量，便于对比。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

TICK = 150  # 秒；所有曝光时长都是 150 秒的整数倍


def utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def sun_moon(jd_days: np.ndarray):
    """太阳、月亮赤经赤纬（度），与 kit 的低精度公式一致。"""
    ml = np.radians((280.460 + 0.9856474 * jd_days) % 360.0)
    ma = np.radians((357.528 + 0.9856003 * jd_days) % 360.0)
    lon = np.radians((np.degrees(ml) + 1.915 * np.sin(ma) + 0.020 * np.sin(2 * ma)) % 360.0)
    obl = np.radians(23.439 - 0.0000004 * jd_days)
    sun_ra = np.degrees(np.arctan2(np.cos(obl) * np.sin(lon), np.cos(lon))) % 360.0
    sun_dec = np.degrees(np.arcsin(np.sin(obl) * np.sin(lon)))
    mml = np.radians((218.316 + 13.176396 * jd_days) % 360.0)
    mma = np.radians((134.963 + 13.064993 * jd_days) % 360.0)
    arg = np.radians((93.272 + 13.229350 * jd_days) % 360.0)
    mlon = mml + np.radians(6.289) * np.sin(mma)
    mlat = np.radians(5.128) * np.sin(arg)
    x = np.cos(mlon) * np.cos(mlat)
    y = np.sin(mlon) * np.cos(mlat) * np.cos(obl) - np.sin(mlat) * np.sin(obl)
    z = np.sin(mlon) * np.cos(mlat) * np.sin(obl) + np.sin(mlat) * np.cos(obl)
    return sun_ra, sun_dec, np.degrees(np.arctan2(y, x)) % 360.0, np.degrees(np.arcsin(z))


def separation(ra1, dec1, ra2, dec2):
    ra1, dec1, ra2, dec2 = map(np.radians, (ra1, dec1, ra2, dec2))
    c = np.sin(dec1) * np.sin(dec2) + np.cos(dec1) * np.cos(dec2) * np.cos(ra1 - ra2)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def altitude(lst, ra, dec, lat):
    ha = np.radians((lst - ra + 180.0) % 360.0 - 180.0)
    s = np.sin(lat) * np.sin(np.radians(dec)) + np.cos(lat) * np.cos(np.radians(dec)) * np.cos(ha)
    return np.degrees(np.arcsin(np.clip(s, -1.0, 1.0)))


def airmass(alt):
    z = 90.0 - alt
    raw = 1.0 / (np.cos(np.radians(z)) + 0.50572 * np.maximum(96.07995 - z, 1e-6) ** -1.6364)
    return raw * (1.0 + 0.50572 * 96.07995 ** -1.6364)


def geometry_quality(scenario: Path, times: np.ndarray, ra: np.ndarray, dec: np.ndarray):
    """返回 (airmass, 月光因子)，形状 [天区, 时刻]。"""
    cal = json.loads((scenario / "config" / "calendar_config.json").read_text())
    lunar = json.loads((scenario / "config" / "tile_config.json").read_text())["lunar_model"]
    lat = np.radians(cal["site"]["latitude_deg"])
    lon = cal["site"]["longitude_deg"]
    days = times / 86400.0 + 2440587.5 - 2451545.0
    lst = (280.46061837 + 360.98564736629 * days + lon) % 360.0
    sun_ra, sun_dec, moon_ra, moon_dec = sun_moon(days)
    illum = (1.0 - np.cos(np.radians(separation(sun_ra, sun_dec, moon_ra, moon_dec)))) / 2.0
    moon_alt = altitude(lst, moon_ra, moon_dec, lat)
    weight = illum * np.sin(np.radians(np.maximum(0.0, moon_alt))) ** lunar["altitude_exponent"]
    alt = altitude(lst[None, :], ra[:, None], dec[:, None], lat)
    sep = separation(ra[:, None], dec[:, None], moon_ra[None, :], moon_dec[None, :])
    lq = np.clip(1.0 - lunar["maximum_penalty"] * weight[None, :] * np.exp(-sep / lunar["angular_decay_scale_deg"]), 0, 1)
    return alt, airmass(np.maximum(alt, 0.1)), lq


def greedy_schedule(value: np.ndarray, exp_ticks: np.ndarray, start_ok: np.ndarray, starts: np.ndarray, n_ticks: int):
    """value[i, k]：天区 i 从 starts[k] 格开始的价值；按 value / 曝光格数 贪心放入，保证不重叠。"""
    ii, kk = np.nonzero(start_ok)
    dens = value[ii, kk] / exp_ticks[ii]
    order = np.argsort(-dens, kind="stable")
    busy = np.zeros(n_ticks + 16, dtype=bool)
    assigned = {}
    for idx in order:
        i, k = int(ii[idx]), int(kk[idx])
        if i in assigned:
            continue
        span, tick = int(exp_ticks[i]), int(starts[k])
        if busy[tick:tick + span].any():
            continue
        busy[tick:tick + span] = True
        assigned[i] = k
    return assigned


def main(scenario: Path, run_dir: Path | None) -> None:
    ref = scenario / "outputs" / "reference"
    tiles = list(csv.DictReader((ref / "tiles.csv").open(encoding="utf-8-sig")))
    slots = list(csv.DictReader((ref / "slots.csv").open(encoding="utf-8-sig")))
    weather = {w["slot_id"]: w for w in csv.DictReader((ref / "weather.csv").open(encoding="utf-8-sig"))}
    ra = np.array([float(t["ra_deg"]) for t in tiles])
    dec = np.array([float(t["dec_deg"]) for t in tiles])
    exp_ticks = np.array([int(t["nominal_exptime_seconds"]) // TICK for t in tiles])
    value = np.array([int(t["n_lrg"]) + int(t["n_elg"]) + 1.7 * int(t["n_qso"]) + 0.45 * int(t["n_bgs"]) for t in tiles])
    avail_from = np.array([utc(t["available_from_utc"]) for t in tiles])
    avail_until = np.array([utc(t["available_until_utc"]) for t in tiles])

    # 时间轴：每个时隙 6 格；夜与夜之间插一段不可用的间隔，保证曝光不会跨夜
    tick_time, tick_open, tick_w, tick_night_end = [], [], [], []
    night_end = {}
    for s in slots:
        night_end[s["night_id"]] = max(night_end.get(s["night_id"], 0.0), utc(s["timestamp_utc"]) + float(s["duration_seconds"]))
    previous_night = None
    for s in slots:
        if previous_night is not None and s["night_id"] != previous_night:
            for _ in range(10):
                tick_time.append(tick_time[-1])
                tick_open.append(False)
                tick_w.append(0.0)
                tick_night_end.append(0.0)
        previous_night = s["night_id"]
        w = weather[s["slot_id"]]
        is_open = w["is_observable"] == "true"
        quality = (float(w["instrument_efficiency"]) * float(w["transparency"]) * float(w["sky_quality"]) / float(w["seeing_arcsec"])) if is_open else 0.0
        start = utc(s["timestamp_utc"])
        for j in range(int(float(s["duration_seconds"])) // TICK):
            tick_time.append(start + j * TICK)
            tick_open.append(is_open)
            tick_w.append(quality)
            tick_night_end.append(night_end[s["night_id"]])
    tick_time = np.array(tick_time)
    tick_open = np.array(tick_open)
    tick_w = np.array(tick_w)
    tick_night_end = np.array(tick_night_end)
    n_ticks = len(tick_time)

    # 以曝光中点评估几何；只允许从每 3 格（450 秒）开始，控制规模
    starts = np.arange(0, n_ticks, 3)
    mid = tick_time[starts][None, :] + exp_ticks[:, None] * TICK / 2.0
    # 按天区的曝光时长分组计算中点几何（只有 5 种曝光时长）
    alt = np.zeros((len(tiles), len(starts)))
    am = np.zeros_like(alt)
    lq = np.zeros_like(alt)
    for e in np.unique(exp_ticks):
        rows = np.nonzero(exp_ticks == e)[0]
        a, m, q = geometry_quality(scenario, tick_time[starts] + e * TICK / 2.0, ra[rows], dec[rows])
        alt[rows], am[rows], lq[rows] = a, m, q
    end_time = tick_time[starts][None, :] + exp_ticks[:, None] * TICK
    open_all = np.ones((len(tiles), len(starts)), dtype=bool)
    for e in np.unique(exp_ticks):
        rows = np.nonzero(exp_ticks == e)[0]
        ok = np.ones(len(starts), dtype=bool)
        for j in range(int(e)):
            idx = np.minimum(starts + j, n_ticks - 1)
            ok &= tick_open[idx] & (starts + j < n_ticks)
        open_all[rows] = ok[None, :]
    start_ok = (open_all & (alt >= 30.0) & (mid >= avail_from[:, None]) & (end_time <= avail_until[:, None])
                & (end_time <= tick_night_end[starts][None, :]))
    q_geo = lq / am
    best_q = np.where(start_ok, q_geo, 0.0).max(axis=1)
    print(f"天区 {len(tiles)}，开放的 150 秒格 {int(tick_open.sum())}（{tick_open.sum() * TICK / 3600:.0f} h），"
          f"可开始的 (天区, 时刻) 组合 {int(start_ok.sum())}")

    results = {}
    for name, val in (("geometry", value[:, None] * q_geo), ("clairvoyant", value[:, None] * q_geo * tick_w[starts][None, :])):
        assigned = greedy_schedule(val, exp_ticks, start_ok, starts, n_ticks)
        idx_t = np.array(list(assigned.keys()))
        idx_k = np.array(list(assigned.values()))
        geo_sum = float((value[idx_t] * q_geo[idx_t, idx_k]).sum())
        real_sum = float((value[idx_t] * q_geo[idx_t, idx_k] * tick_w[starts][idx_k]).sum())
        loss = float(np.mean(q_geo[idx_t, idx_k] / best_q[idx_t]))
        results[name] = (len(assigned), geo_sum, real_sum, loss)
        print(f"[{name:11}] 天区 {len(assigned):5d}  Σ V·q_geo = {geo_sum:9.0f}  Σ V·q_geo·天气 = {real_sum:9.0f}  "
              f"几何质量/该天区最佳 = {loss:.3f}")

    if run_dir is not None:
        report = json.loads((run_dir / "score_report.json").read_text(encoding="utf-8"))
        index = {t["tile_id"]: i for i, t in enumerate(tiles)}
        geo_sum = real_sum = 0.0
        ratios, n = [], 0
        for action in report["actions"]:
            if action["outcome"] != "completed" or not action["segments"]:
                continue
            segs = action["segments"]
            total = sum(s["duration_seconds"] for s in segs)
            q = sum(s["lunar_quality_factor"] / s["airmass"] * s["duration_seconds"] for s in segs) / total
            i = index[action["tile_id"]]
            geo_sum += value[i] * q
            real_sum += sum(s["base_science_score"] for s in segs)
            ratios.append(q / best_q[i] if best_q[i] > 0 else 1.0)
            n += 1
        print(f"[{'run':11}] 曝光 {n:5d}  Σ V·q_geo = {geo_sum:9.0f}  实际基础分 = {real_sum:9.0f}  "
              f"几何质量/该天区最佳 = {np.mean(ratios):.3f}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
