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

from events import TRUE_BACKGROUND_OPEN, open_from_probability
from geometry import Site, TileGeometry

try:  # 平台按 requirements.txt 装 numpy；不可用时用纯 Python
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

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
                "id": tid,
                "ra": float(tile["ra_deg"]),
                "dec": float(tile["dec_deg"]),
                "region": str(tile["region_id"]),
                "geo": TileGeometry(float(tile["ra_deg"]), float(tile["dec_deg"])),
                "value": float(tile["tile_science_value"]),
                "ticks": float(tile["nominal_exptime_seconds"]) / TICK,
                "exposure": float(tile["nominal_exptime_seconds"]),
                "from": _epoch(tile.get("available_from_utc")) or -math.inf,
                "until": _epoch(tile.get("available_until_utc")) or math.inf,
            }
        self.qmax: dict = {tid: {} for tid in self.tiles}  # tile -> {时段序号: 该时段最佳几何质量 lunar/airmass}
        self.qtime: dict = {tid: {} for tid in self.tiles}  # tile -> {时段序号: 取到最佳几何的采样时刻（时隙中点）}
        self.qsamples: dict = {tid: {} for tid in self.tiles} if self.p.get("plan_trim_current_bin") else {}
        self.events = None          # 天气事件（events.Event）；None 时规划与不看预报时逐位相同
        self.closing_events = []
        self.events_mode = None     # "forecast"：每周预报；"truth"：真实事件（只用于本地上限试验）
        self.events_until = -math.inf  # 预报的有效期末（之后的时段按平均开放率）
        self._bin_events: dict = {}  # 时段序号 -> 可能起作用的事件（全站闭站的除外）
        self._effects: dict = {}     # (天区, 时段) -> [(概率, 是否闭站, 质量乘数, 效率乘数)]
        self.computed_nights: set = set()
        self.mu: dict = {}
        self.w_samples: list = []
        self.night_w: dict = {}  # 夜序号 -> 该夜实测的 W0（天气持续性用）
        self.last_clean_weather: tuple[float, float, int] | None = None
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
            clean = not weather.get("active_event_ids")
            if clean or not self.p.get("plan_clean_weather_quantiles"):
                bisect.insort(self.w_samples, w0)
            moment = _epoch((snapshot.get("cursor") or {}).get("timestamp_utc"))
            if moment is not None:
                night = bisect.bisect_right(self.night_starts, moment) - 1
                self.night_w.setdefault(night, []).append(w0)
                if clean:
                    self.last_clean_weather = (moment, w0, night)

    def _weather_quantiles(self) -> list:
        """观测样本不足时混入先验；样本越多越依赖实测。"""
        observed = self.w_samples
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
        if np is not None:
            weather = np.asarray(w_quantiles, dtype=float)
            q0 = np.minimum(weather, self.max_quality)[:, None] * (np.arange(106) / 100.0)
            bonus = np.where(q0 >= self.dark, 1.0 + self.bonus["DARK"],
                             np.where(q0 >= self.bright, 1.0 + self.bonus["BRIGHT"],
                                      1.0 + self.bonus["BACKUP"]))
            table = (weather[:, None] * bonus).sum(axis=0) / len(weather)
        else:
            table = []
            for k in range(106):
                q = k / 100.0
                total = 0.0
                for w in w_quantiles:
                    q0 = min(w, self.max_quality) * q
                    band = "DARK" if q0 >= self.dark else ("BRIGHT" if q0 >= self.bright else "BACKUP")
                    total += w * (1.0 + self.bonus[band])
                table.append(total / len(w_quantiles))

        def factor(q: float) -> float:
            x = min(1.05, max(0.0, q)) * 100.0
            k = min(104, int(x))
            return float(table[k] + (table[k + 1] - table[k]) * (x - k))
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

    def _recent_weather_factors(self, quantiles: list, now: float, bins: list[int]) -> dict:
        """Condition the next few hours on the latest clean slot's weather."""
        if self.last_clean_weather is None:
            return {}
        observed_at, observed_w, observed_night = self.last_clean_weather
        if not 0 <= now - observed_at <= 3600.0:
            return {}
        mean = sum(quantiles) / len(quantiles)
        limit = float(self.p["plan_recent_weather_hours"]) * 3600.0
        floor = float(self.p["plan_recent_weather_floor"])
        factors = {}
        for b in bins:
            night, start, stop, _samples = self.bins[b]
            if night != observed_night or start >= now + limit:
                continue
            midpoint = (max(start, now) + stop) / 2.0
            rho = floor + (1.0 - floor) * 0.88 ** (max(0.0, midpoint - observed_at) / self.slot)
            shift = rho * (observed_w - mean)
            spread = math.sqrt(max(0.0, 1.0 - rho * rho)) if self.p.get("plan_recent_weather_conditional") else 1.0
            factors[b] = self._value_factor([max(0.05, mean + spread * (w - mean) + shift) for w in quantiles])
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
                        self.qtime[tid][b] = sample
                    if self.qsamples:
                        self.qsamples[tid].setdefault(b, []).append((sample, q))

    def _remaining_bin_quality(self, tid: str, b: int, now: float) -> tuple[float, float | None]:
        if not self.qsamples:
            return self.qmax[tid][b], None
        available = ((sample, q) for sample, q in self.qsamples[tid].get(b, ())
                     if sample + self.slot / 2.0 > now)
        sample, q = max(available, key=lambda entry: entry[1], default=(None, 0.0))
        return q, sample

    # ------------------------------------------------------------------ 天气事件

    def set_events(self, events: list, mode: str, until: float = math.inf) -> None:
        self.events, self.events_mode, self.events_until = list(events), mode, until
        self.closing_events = [e for e in self.events if e.scope == "ALL" and e.force_close]
        self._bin_events, self._effects = {}, {}

    def _site_open(self, t: float, open_rate: float) -> float:
        """时刻 t 的站点开放率：全站强制闭站的事件（降雨、龙卷风）按预报概率换算；预报有效期外用平均开放率。"""
        if t >= self.events_until:
            return open_rate
        if self.events_mode == "truth":
            return 0.0 if any(e.active(t) > 0.0 for e in self.closing_events) else TRUE_BACKGROUND_OPEN
        stay = 1.0
        for e in self.closing_events:
            stay *= 1.0 - e.active(t)
        return open_from_probability(1.0 - stay)

    def _event_effects(self, tid: str, b: int, sample_time: float | None = None) -> list:
        key = (tid, b, sample_time)
        if key not in self._effects:
            if b not in self._bin_events:
                _n, start, stop, _samples = self.bins[b]
                self._bin_events[b] = [e for e in self.events if not (e.scope == "ALL" and e.force_close)
                                       and e.reach()[0] < stop and e.reach()[1] > start]
            effects = []
            t = sample_time if sample_time is not None else self.qtime[tid].get(b)
            if t is not None:
                tile = self.tiles[tid]
                for e in self._bin_events[b]:
                    p = e.active(t)
                    if p > 1e-4 and e.covers(tile, self.site, t):
                        effects.append((p, e.force_close, e.mq, e.me))
            self._effects[key] = effects
        return self._effects[key]

    # ------------------------------------------------------------------ 规划

    def replan(self, now: float, completed: set, exclude: set, coverage=None, required=None, terminal: float = 0.0,
               faults=None, repeats=None) -> None:
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
        recent_factor = self._recent_weather_factors(quantiles, now, bins) if self.p.get("plan_recent_weather") else {}
        efficiency = float(self.p["mean_jitter"])
        index = {b: k for k, b in enumerate(bins)}
        scale, shift = {}, {}
        repeat_scale = 1.0  # 复拍的科学分增量对覆盖奖励的放大：1 + w·E_now（完成数不变，均匀度不变）
        if coverage is not None and coverage[0] > 0:
            weight, base_total, counts = coverage
            n = len(counts)
            total = sum(counts.values())
            sum_sq = sum(c * c for c in counts.values())
            evenness = total * total / (n * sum_sq) if sum_sq else 0.0
            repeat_scale = 1.0 + weight * evenness
            for region, x in counts.items():
                after = (total + 1) ** 2 / (n * (sum_sq + 2 * x + 1))
                scale[region] = 1.0 + weight * after
                shift[region] = weight * base_total * (after - evenness)
        capacity = []
        bin_open = []  # 每个时段的开放率：没有天气事件时都等于 open_rate
        for b in bins:
            _n, start, stop, samples = self.bins[b]
            if self.events is None:
                rate = open_rate
            else:
                ahead = [t for t in samples if t + self.slot / 2.0 > now] or samples
                rate = sum(self._site_open(t, open_rate) for t in ahead) / len(ahead) if ahead else open_rate
            bin_open.append(rate)
            capacity.append((stop - max(start, now)) / TICK * rate)
        # 每块天区的 (时段, 期望价值) 列表
        rows = []
        v_max = 0.0
        min_bins = int(self.p["required_min_bins"])
        min_left = float(self.p["required_min_nights"]) * 86400.0
        repeat_min = float(self.p.get("plan_repeat_min_gain", 20.0))
        for tid, tile in self.tiles.items():
            if tid in exclude:
                continue
            prior = None
            if tid in completed:
                # 复拍（repeats）：已完成、入账分明显低于满效率潜力的天区按"期望实现分 − 已入账"参与定价，先用最佳几何粗筛
                prior = repeats.get(tid) if repeats else None
                if prior is None:
                    continue
                qbest = max(self.qmax[tid].values(), default=0.0)
                if tile["value"] * prior[1] * qbest * factor(qbest) * efficiency - prior[0] < repeat_min:
                    continue
            # 机会充裕的 REQUIRED 天区参与定价：时段价值加上（按开放率折算的）终局奖励，现在拍与将来拍只比时机
            bonus = terminal if required and tid in required and prior is None else 0.0
            if bonus and tile["until"] - now < min_left:
                continue
            fault = faults.get(tile["region"]) if faults else None
            pairs = []
            a, c = scale.get(tile["region"], 1.0), shift.get(tile["region"], 0.0)
            for b, q in self.qmax[tid].items():
                k = index.get(b)
                if k is None:
                    continue
                sample_time = None
                if b == first_bin and self.qsamples:
                    q, sample_time = self._remaining_bin_quality(tid, b, now)
                    if q <= 0.0:
                        continue
                gf = recent_factor.get(b) or night_factor.get(self.bins[b][0], factor)
                g = gf(q)
                value = tile["value"] * fault[0] if fault is not None and self.bins[b][1] < fault[1] else tile["value"]
                rate = bin_open[k]
                if prior is not None:
                    quality, reach = 1.0, 1.0
                    for p, closes, mq, me in (self._event_effects(tid, b, sample_time) if self.events is not None else ()):
                        if closes:
                            reach *= 1.0 - p
                        elif g > 0.0:
                            quality *= 1.0 - p + p * me * mq * gf(mq * q) / g
                    v = (value * prior[1] * q * g * efficiency * quality - prior[0]) * repeat_scale * rate * reach
                elif self.events is None:
                    v = (value * q * g * efficiency * a + c) * rate
                else:
                    # 天气事件：质量类（云、烟霾、寒潮）按概率混合 m_e·m_q·g(m_q·q)/g(q)，只作用于科学分；方向闭站（火箭）作用于整个时段价值
                    quality, reach = 1.0, 1.0
                    for p, closes, mq, me in self._event_effects(tid, b, sample_time):
                        if closes:
                            reach *= 1.0 - p
                        elif g > 0.0:
                            quality *= 1.0 - p + p * me * mq * gf(mq * q) / g
                    v = (value * q * g * efficiency * quality * a + c) * rate * reach
                if v <= 0:
                    continue
                pairs.append((k, v + bonus * rate) if bonus else (k, v))
                v_max = max(v_max, v)  # 步长只按科学分定，不被终局奖励放大
            if bonus and len(pairs) < min_bins:
                continue  # 机会不多的 REQUIRED 天区维持 μ = 0：一有窗口就拍
            if pairs:
                rows.append((tid, tile["ticks"], pairs))
        cap_max = max(1.0, max(capacity))
        iterations = int(self.p["plan_first_iterations"] if self.plans == 0 else self.p["plan_iterations"])
        solve = subgradient_numpy if np is not None and self.p.get("numpy_planner", True) else subgradient_python
        lam = solve(rows, capacity, v_max, cap_max, iterations, int(self.p["plan_lambda_average"]))
        mu = {}
        for tid, ticks, pairs in rows:
            best = max(v - ticks * lam[k] for k, v in pairs)
            if best > 0:
                mu[tid] = best
        self.mu = mu
        self.plans += 1
        elapsed = _time.monotonic() - started
        self.plan_seconds += elapsed
        print(f"observer: plan #{self.plans} [{solve.__name__[12:]}] bins={len(bins)} tiles={len(rows)} priced={len(mu)} "
              f"open_rate={open_rate:.3f} took={elapsed:.1f}s total={self.plan_seconds:.1f}s", file=sys.stderr, flush=True)


def subgradient_python(rows, capacity, v_max, cap_max, iterations, average=0):
    """次梯度求时段价格 λ：每块天区选约化收益 v − ticks·λ 最大且为正的时段，按需求与容量之差更新 λ。
    average > 0 时返回最后 average 次迭代 λ 的平均值（次梯度末次迭代在最优附近振荡）。"""
    lam = [0.0] * len(capacity)
    total = [0.0] * len(capacity)
    for it in range(iterations):
        demand = [0.0] * len(capacity)
        for _tid, ticks, pairs in rows:
            best_k, best = -1, 0.0
            for k, v in pairs:
                r = v - ticks * lam[k]
                if r > best:
                    best_k, best = k, r
            if best_k >= 0:
                demand[best_k] += ticks
        step = 0.5 / math.sqrt(1.0 + it) * (v_max / cap_max)
        for k in range(len(capacity)):
            lam[k] = max(0.0, lam[k] + step * (demand[k] - capacity[k]) / cap_max)
        if average > 0 and it >= iterations - average:
            for k in range(len(capacity)):
                total[k] += lam[k]
    if average > 0 and iterations > 0:
        count = min(average, iterations)
        return [x / count for x in total]
    return lam


def subgradient_numpy(rows, capacity, v_max, cap_max, iterations, average=0):
    """与 subgradient_python 逐位相同的向量化版本（平台 CPython 上快一个数量级以上）。

    - 约化收益逐元素计算 v − ticks·λ，乘、减的顺序与原式一致；没有的 (天区, 时段) 填 −inf；
    - argmax 取首个最大值：pairs 按时段递增排列（按夜、按时段顺序生成），与“严格大于才替换”的循环选中同一个时段；
    - 需求累加的 ticks 是小整数（曝光秒数 / 150），求和顺序无关；λ 逐元素更新。
    """
    count = len(capacity)
    values = np.full((len(rows), count), -np.inf)
    ticks = np.empty(len(rows))
    for i, (_tid, t, pairs) in enumerate(rows):
        ticks[i] = t
        values[i, [k for k, _v in pairs]] = [v for _k, v in pairs]
    cap = np.asarray(capacity, dtype=float)
    lam = np.zeros(count)
    total = np.zeros(count)
    index = np.arange(len(rows))
    reduced = np.empty_like(values)
    column = ticks[:, None]
    for it in range(iterations):
        np.multiply(column, lam, out=reduced)
        np.subtract(values, reduced, out=reduced)
        best = reduced.argmax(axis=1)
        chosen = reduced[index, best] > 0.0
        demand = np.bincount(best[chosen], weights=ticks[chosen], minlength=count)
        step = 0.5 / math.sqrt(1.0 + it) * (v_max / cap_max)
        lam = np.maximum(0.0, lam + step * (demand - cap) / cap_max)
        if average > 0 and it >= iterations - average:
            total += lam
    if average > 0 and iterations > 0:
        return (total / min(average, iterations)).tolist()
    return lam.tolist()


def _epoch(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
