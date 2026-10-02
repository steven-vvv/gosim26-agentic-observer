"""夜间价格规划：求解“天区 → 未来 1 小时时段”分配问题线性松弛的对偶价格，得到每块天区留到将来的机会价值 μ。

在线决策按 (现在拍的收益 − μ) / 曝光秒数 排序：将来有更好机会的天区自动让位，快要错过的天区优先。
时间稀缺时（决赛规模）价格高、推迟少；时间富余时（练习场景）价格趋近 0，天区会等到最佳时机。

离线验证（tools/plan_experiment.py，只计基础分 + 加成，定价不含天气关闭信息）：相对当前收益贪心，
comp-a-s101 +4.8%，comp-b-s201 +6.5%。实现与离线版一致：时间离散为 150 秒一格，每夜重新规划，
视野 14 夜，将来价值按天气分布积分档位加成。
"""
from __future__ import annotations

import bisect
import math
import sys
import time as _time
from datetime import date, datetime, timedelta

from geometry import Site, TileGeometry

TICK = 150.0
# 本地同配置场景（4 个开发场景）开放时隙的天气项 W0 = T·S/seeing 的 20 个分位数，作为开局先验
PRIOR_W0 = (0.444, 0.471, 0.487, 0.503, 0.517, 0.532, 0.545, 0.558, 0.572, 0.586,
            0.599, 0.616, 0.635, 0.654, 0.673, 0.692, 0.717, 0.751, 0.803, 0.886)
PRIOR_OPEN_RATE = 0.87


class Planner:
    def __init__(self, initial: dict, params: dict) -> None:
        self.p = params
        contract = initial["scoring_contract"]
        score = contract["score_config"]
        self.dark = float(score["quality_thresholds"]["dark"])
        self.bright = float(score["quality_thresholds"]["bright"])
        self.bonus = {k: float(v) for k, v in score["program_bonus"].items()}
        self.max_quality = float(contract["weather_score_interface"]["maximum_weather_quality"])
        self.site = Site(initial["site"], contract["lunar_model"])
        calendar = initial["calendar"]
        slot = int(calendar["slot_duration_seconds"])
        first = date.fromisoformat(str(calendar["first_night"]))
        self.nights = [self.site.night_bounds(first + timedelta(days=k), slot) for k in range(int(calendar["night_count"]))]
        self.slot = slot
        # 1 小时时段：(夜序号, 开始, 结束, 采样时刻 = 时段内各时隙中点)
        self.bins = []
        for n, (start, end) in enumerate(self.nights):
            t = start
            while t < end:
                stop = min(end, t + 3600.0)
                samples = [s + slot / 2.0 for s in range(int(t), int(stop), slot) if s + slot <= end]
                self.bins.append((n, t, stop, samples))
                t = stop
        self.bin_starts = [b[1] for b in self.bins]
        self.night_starts = [start for start, _end in self.nights]

        self.tiles = {}
        for tile in initial["tile_catalog"]["tiles"]:
            tid = str(tile["tile_id"])
            self.tiles[tid] = {
                "region": str(tile["region_id"]),
                "geo": TileGeometry(float(tile["ra_deg"]), float(tile["dec_deg"])),
                "value": float(tile["tile_science_value"]),
                "ticks": float(tile["nominal_exptime_seconds"]) / TICK,
                "exposure": float(tile["nominal_exptime_seconds"]),
                "from": _epoch(tile.get("available_from_utc")) or -math.inf,
                "until": _epoch(tile.get("available_until_utc")) or math.inf,
            }
        self.qmax: dict = {tid: {} for tid in self.tiles}  # tile -> {时段序号: 该时段最佳几何质量 lunar/airmass}
        self.computed_nights: set = set()
        self.mu: dict = {}
        self.w_samples: list = []
        self.night_w: dict = {}  # 夜序号 -> 该夜实测的 W0（天气持续性用）
        self.open_slots = 0
        self.seen_slots: set = set()
        self.plans = 0
        self.plan_seconds = 0.0

    # ------------------------------------------------------------------ 天气统计

    def observe_weather(self, snapshot: dict) -> None:
        weather = snapshot.get("current_site_weather") or {}
        slot_id = weather.get("slot_id")
        if slot_id is None or slot_id in self.seen_slots:
            return
        self.seen_slots.add(slot_id)
        if weather.get("is_observable"):
            self.open_slots += 1
            try:
                w0 = float(weather["transparency"]) * float(weather["sky_quality"]) / float(weather["seeing_arcsec"])
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                return
            self.w_samples.append(w0)
            moment = _epoch((snapshot.get("cursor") or {}).get("timestamp_utc"))
            if moment is not None:
                self.night_w.setdefault(bisect.bisect_right(self.night_starts, moment) - 1, []).append(w0)

    def _weather_quantiles(self) -> list:
        """观测样本不足时混入先验；样本越多越依赖实测。"""
        observed = sorted(self.w_samples)
        weight = min(1.0, len(observed) / 200.0)
        if not observed:
            return list(PRIOR_W0)
        n = len(PRIOR_W0)
        mixed = []
        for k in range(n):
            q = (k + 0.5) / n
            empirical = observed[min(len(observed) - 1, int(q * len(observed)))]
            mixed.append(weight * empirical + (1.0 - weight) * PRIOR_W0[k])
        return mixed

    def _open_rate(self) -> float:
        seen = len(self.seen_slots)
        weight = min(1.0, seen / 200.0)
        observed = self.open_slots / seen if seen else PRIOR_OPEN_RATE
        return weight * observed + (1.0 - weight) * PRIOR_OPEN_RATE

    def _value_factor(self, w_quantiles: list):
        """g(q) = E[W0·(1+档位加成(W0·q))]，在 q 网格上预先算好，返回查表函数。"""
        grid = [k / 100.0 for k in range(106)]
        table = []
        for q in grid:
            total = 0.0
            for w in w_quantiles:
                q0 = min(w, self.max_quality) * q
                band = "DARK" if q0 >= self.dark else ("BRIGHT" if q0 >= self.bright else "BACKUP")
                total += w * (1.0 + self.bonus[band])
            table.append(total / len(w_quantiles))

        def factor(q: float) -> float:
            x = min(1.05, max(0.0, q)) * 100.0
            k = min(104, int(x))
            return table[k] + (table[k + 1] - table[k]) * (x - k)
        return factor

    def _persistence_factors(self, quantiles: list, current: int, horizon: int) -> dict:
        """天气持续性（生成器的夜级 AR(1)）：第 n 夜的天气分布均值从最近一晚的实测水平按 φ^(n − 该晚) 回归长期均值。

        离线试验台 14 个决赛规模场景 +0.19%（标准误 0.08）。返回 {夜序号: g(q) 查表函数}；还没有实测时返回空。
        """
        latest = max((n for n, w in self.night_w.items() if w and n <= current), default=None)
        if latest is None:
            return {}
        level = sum(self.night_w[latest]) / len(self.night_w[latest])
        mean = sum(quantiles) / len(quantiles)
        phi = float(self.p["persistence_phi"])
        factors = {}
        for n in range(current, horizon):
            scale = (mean + phi ** (n - latest) * (level - mean)) / mean
            factors[n] = self._value_factor([w * scale for w in quantiles])
        return factors

    # ------------------------------------------------------------------ 几何预计算

    def _compute_night(self, night: int) -> None:
        if night in self.computed_nights or night >= len(self.nights):
            return
        self.computed_nights.add(night)
        night_end = self.nights[night][1]
        min_alt = float(self.p["plan_min_altitude"])
        for b, (n, _start, _stop, samples) in enumerate(self.bins):
            if n != night:
                continue
            for sample in samples:
                slot_start = sample - self.slot / 2.0
                moment = self.site.moment(sample)
                for tid, tile in self.tiles.items():
                    end = slot_start + tile["exposure"]
                    if slot_start < tile["from"] or end > tile["until"] or end > night_end:
                        continue
                    altitude, am, lunar = moment.quality(tile["geo"])
                    if altitude < min_alt:
                        continue
                    q = lunar / am
                    cell = self.qmax[tid]
                    if q > cell.get(b, 0.0):
                        cell[b] = q

    # ------------------------------------------------------------------ 规划

    def replan(self, now: float, completed: set, exclude: set, coverage=None) -> None:
        """每夜开始时调用：重算未完成天区的 μ（排除的天区 μ 记为 0）。

        coverage = (权重 w, 已入账基础分 B, {分区: 完成数})。给出时，每个时段的价值按与在线决策相同的精确边际计算：
        科学分 × (1 + w·E_after) + w·B·(E_after − E_now)。这样已规划到将来的天区，现在拍与将来拍的覆盖收益相互抵消，
        比较只剩时机差别；未被规划的天区覆盖收益全额计入当下。
        """
        started = _time.monotonic()
        current = next((n for n, (_s, end) in enumerate(self.nights) if end > now), len(self.nights))
        horizon = current + int(self.p["plan_horizon_nights"])
        for night in range(current, min(horizon, len(self.nights))):
            self._compute_night(night)
        first_bin = max(0, bisect.bisect_right(self.bin_starts, now) - 1)
        bins = [b for b in range(first_bin, len(self.bins)) if self.bins[b][0] < horizon]
        if not bins:
            self.mu = {}
            return
        open_rate = self._open_rate()
        quantiles = self._weather_quantiles()
        factor = self._value_factor(quantiles)
        night_factor = self._persistence_factors(quantiles, current, horizon) if self.p.get("plan_persistence") else {}
        efficiency = float(self.p["mean_jitter"])
        index = {b: k for k, b in enumerate(bins)}
        scale, shift = {}, {}
        if coverage is not None and coverage[0] > 0:
            weight, base_total, counts = coverage
            n = len(counts)
            total = sum(counts.values())
            sum_sq = sum(c * c for c in counts.values())
            evenness = total * total / (n * sum_sq) if sum_sq else 0.0
            for region, x in counts.items():
                after = (total + 1) ** 2 / (n * (sum_sq + 2 * x + 1))
                scale[region] = 1.0 + weight * after
                shift[region] = weight * base_total * (after - evenness)
        capacity = []
        for b in bins:
            _n, start, stop, _samples = self.bins[b]
            capacity.append((stop - max(start, now)) / TICK * open_rate)
        # 每块天区的 (时段, 期望价值) 列表
        rows = []
        v_max = 0.0
        for tid, tile in self.tiles.items():
            if tid in completed or tid in exclude:
                continue
            pairs = []
            a, c = scale.get(tile["region"], 1.0), shift.get(tile["region"], 0.0)
            for b, q in self.qmax[tid].items():
                k = index.get(b)
                if k is None:
                    continue
                g = factor(q) if not night_factor else night_factor.get(self.bins[b][0], factor)(q)
                v = (tile["value"] * q * g * efficiency * a + c) * open_rate
                if v <= 0:
                    continue
                pairs.append((k, v))
                v_max = max(v_max, v)
            if pairs:
                rows.append((tid, tile["ticks"], pairs))
        cap_max = max(1.0, max(capacity))
        lam = [0.0] * len(bins)
        iterations = int(self.p["plan_first_iterations"] if self.plans == 0 else self.p["plan_iterations"])
        for it in range(iterations):
            demand = [0.0] * len(bins)
            for _tid, ticks, pairs in rows:
                best_k, best = -1, 0.0
                for k, v in pairs:
                    r = v - ticks * lam[k]
                    if r > best:
                        best_k, best = k, r
                if best_k >= 0:
                    demand[best_k] += ticks
            step = 0.5 / math.sqrt(1.0 + it) * (v_max / cap_max)
            for k in range(len(bins)):
                lam[k] = max(0.0, lam[k] + step * (demand[k] - capacity[k]) / cap_max)
        mu = {}
        for tid, ticks, pairs in rows:
            best = max(v - ticks * lam[k] for k, v in pairs)
            if best > 0:
                mu[tid] = best
        self.mu = mu
        self.plans += 1
        elapsed = _time.monotonic() - started
        self.plan_seconds += elapsed
        print(f"observer: plan #{self.plans} bins={len(bins)} tiles={len(rows)} priced={len(mu)} "
              f"open_rate={open_rate:.3f} took={elapsed:.1f}s total={self.plan_seconds:.1f}s", file=sys.stderr, flush=True)


def _epoch(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
