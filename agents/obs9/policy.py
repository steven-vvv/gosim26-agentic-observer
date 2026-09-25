"""确定性观测策略：按“真实边际收益 / 曝光秒数”排序，只用标准库。

相对官方基线（kit/agent/scoring_preview.py 的当前收益贪心）的改动：

1. 覆盖均匀奖励：按 Jain 指数精确计算“多完成这一块”对 coverage_bonus 的边际（基线完全不计）。
2. 观测时机：天区当晚稍后会升到更低的 airmass 时，按 (best_airmass / airmass_now) ** timing_alpha 打折，
   让它在接近最佳时刻再拍。
3. 仪器故障：按分区跟踪“实现分 / 公式估值”比值。首次误报免费，所以第一次出现干净的低读数就上报；
   同时对疑似故障分区的科学分按估计乘数打折，不等平台一天后的回执。
4. NOVA 标签：干净读数的比值 ≥ nova_threshold 时单次即上报（正常读数在 0.9–1.0，nova 在 1.35–1.5）。

读数“干净”指：提交时该天区和站点都没有活跃天气事件，也不在预报的寒潮时段内。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

DEFAULTS = {
    "timing_alpha": 1.0,
    "timing_future_nights": False,  # 实测开启会掉分：时间稀缺时“明晚再拍”并不成立
    "plan": True,                   # 夜间价格规划（planner.py）；关闭时退回当晚时机因子
    "plan_beta": 1.0,
    "plan_coverage": True,          # 规划时把覆盖边际计入每个时段的价值
    "plan_every_seconds": 3600,     # 夜内每隔多久重新规划一次（离线实验：1 小时优于 2/4 小时与每夜一次）
    "budget_reduce_at": 0.60,       # 预计整场耗时超过时钟的这一比例：改为每夜只规划一次
    "budget_disable_at": 0.85,      # 超过这一比例：关闭规划，退回计算量很小的时机因子
    "plan_horizon_nights": 14,
    "plan_first_iterations": 300,
    "plan_iterations": 150,
    "plan_min_altitude": 30.0,
    "plan_allow_wait": False,       # 所有候选扣除机会价值后都为负时是否等待（决赛规模下时间稀缺，不等）
    "segment_expectation": True,    # 异常比值的分母按分段中点几何计算
    "refine_top_k": 12,             # 对粗排前 K 名按分段几何精算收益并挑选档位（0 关闭）
    "rollout_k": 5,                 # 对精算后前 K 名往后推演（≤1 关闭；离线实验 +0.4–0.8 个百分点）
    "rollout_seconds": 2400,
    "log_reads": False,             # 调试：把每次读数的比值写到 stderr
    "protect_required": True,
    "protect_max_interval": 3600,   # REQUIRED 窗口的可开始区间短于此（秒）才需要预留
    "protect_lookahead": 2700,      # 窗口在这么多秒内开启时启动保护
    "coverage": True,
    "mean_jitter": 0.95,
    "fault_threshold": 0.72,
    "fault_single_threshold": 0.72,  # 分段期望值下正常读数 P1≈0.85，0.72 以下基本只有故障
    "fault_window": 8,
    "fault_clear_threshold": 0.85,
    "nova_threshold": 1.28,
    "nova_max": 1.70,
    "reddening_band": [0.68, 0.86],
    "report_faults": True,
    "report_tags": True,
}

_EPOCH_CACHE: dict = {}


def epoch(value) -> float:
    """ISO-8601 UTC 字符串 → Unix 秒（带缓存，快照里同一时间串会反复出现）。"""
    cached = _EPOCH_CACHE.get(value)
    if cached is None:
        cached = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        if len(_EPOCH_CACHE) > 200000:
            _EPOCH_CACHE.clear()
        _EPOCH_CACHE[value] = cached
    return cached


def jain(counts) -> float:
    total = sum(counts)
    if total <= 0:
        return 0.0
    return total * total / (len(counts) * sum(c * c for c in counts))


def load_params(agent_dir: Path) -> dict:
    params = dict(DEFAULTS)
    path = agent_dir / "params.json"
    if path.exists():
        params.update(json.loads(path.read_text(encoding="utf-8")))
    if os.environ.get("OBS_PARAMS"):
        params.update(json.loads(os.environ["OBS_PARAMS"]))
    return params


class ObserverPolicy:
    def __init__(self, initial: dict, params: dict) -> None:
        self.p = params
        self.initial = initial
        self.planner = None
        self.planning = bool(params["plan"])
        self.planned_night = None
        self.planned_at = -1e18
        clocks = [float(v) for v in (initial.get("global_wallclock_seconds"), os.environ.get("SAC_WALLCLOCK_SECONDS")) if v]
        self.wallclock = min(clocks) if clocks else 3600.0  # 本地 runner 的 --wallclock 只体现在环境变量里
        self.clock_started = None
        self.hourly = True
        calendar = initial.get("calendar") or {}
        self.survey_span = None
        if calendar.get("first_night") and calendar.get("last_night"):
            first = epoch(f'{calendar["first_night"]}T12:00:00Z')
            last = epoch(f'{calendar["last_night"]}T12:00:00Z') + 86400
            self.survey_span = (first, last)
        contract = initial["scoring_contract"]
        score = contract["score_config"]
        self.dark = float(score["quality_thresholds"]["dark"])
        self.bright = float(score["quality_thresholds"]["bright"])
        self.bonus = {k: float(v) for k, v in score["program_bonus"].items()}
        penalties = score["penalties"]
        self.required_miss = float(penalties["required_miss"])
        self.flexible_short = float(penalties["flexible_shortfall_per_tile"])
        self.quota = int(score["flexible_quota_per_region"])
        self.coverage_weight = float(score.get("coverage_bonus_weight", 0.0)) if params["coverage"] else 0.0
        weather = contract["weather_score_interface"]
        self.airmass_exp = float(weather["airmass_exponent"])
        self.max_quality = float(weather["maximum_weather_quality"])

        catalog = initial["tile_catalog"]
        self.regions = sorted(str(r) for r in catalog["region_ids"])
        self.region_index = {r: i for i, r in enumerate(self.regions)}
        self.tile_region = {str(t["tile_id"]): str(t["region_id"]) for t in catalog["tiles"]}
        self.tile_until = {str(t["tile_id"]): epoch(t["available_until_utc"]) for t in catalog["tiles"]
                           if t.get("available_until_utc")}
        self.tile_exposure = {str(t["tile_id"]): float(t["nominal_exptime_seconds"]) for t in catalog["tiles"]}
        self.required = {str(t) for t in catalog.get("required_tile_ids") or []}

        self.completed: set = set()
        self.counts = [0] * len(self.regions)
        self.banked: dict = {}          # tile -> 最高实现分（基础分 + 加成）
        self.banked_base: dict = {}     # tile -> 最高实现分对应的基础分估计
        self.base_total = 0.0           # 已入账基础科学分估计，用于覆盖边际
        self.pending = None
        self.last_feedback = None
        self.tonight: dict = {}         # tile -> (best_time, best_airmass, window_end)
        self.forecasts: list = []

        # 故障与标签
        self.region_reads: dict = {r: [] for r in self.regions}
        self.suspect: dict = {}         # region -> 估计的效率乘数（自己检测到、尚未得到回执）
        self.fault_regions: set = set()
        self.fault_multiplier = 1.0
        self.fault_repair_until = 0.0
        self.fault_pending_since = None  # 最近一次故障上报的时间（等待平台回执）
        self.fault_misreports = 0
        self.low_since_report: dict = {}
        self.recent_reads: list = []    # 上次故障上报以来的干净读数比值（跨分区）
        self.reported_tags: set = set()
        self.tile_reads: dict = {}

    # ------------------------------------------------------------------ 状态同步

    def _sync_progress(self, snapshot: dict) -> None:
        progress = snapshot.get("progress") or {}
        ids = progress.get("completed_tile_ids") or []
        if len(ids) != len(self.completed):
            self.completed = {str(t) for t in ids}
            counts = [0] * len(self.regions)
            for tile in self.completed:
                index = self.region_index.get(self.tile_region.get(tile, ""))
                if index is not None:
                    counts[index] += 1
            self.counts = counts
        self.flexible_done = {str(k): int(v) for k, v in (progress.get("flexible_completed_by_region") or {}).items()}

    def _load_windows(self, snapshot: dict, now: float) -> None:
        night = snapshot.get("night_start")
        if not isinstance(night, dict):
            return
        tonight = {}
        for w in night.get("tile_windows") or []:
            end = epoch(w["window_end_utc"])
            if end <= now:
                continue
            entry = (epoch(w["best_time_utc"]), float(w["best_airmass"]), end, epoch(w["window_start_utc"]))
            tonight.setdefault(str(w["tile_id"]), []).append(entry)
        self.tonight = tonight

    def _under_cold_wave(self, now: float) -> bool:
        for f in self.forecasts:
            if f.get("condition") != "cold_wave":
                continue
            start = epoch(f["predicted_start_utc"]) - float(f.get("start_uncertainty_seconds") or 0)
            end = epoch(f["predicted_end_utc"]) + float(f.get("end_uncertainty_seconds") or 0)
            if start <= now < end:
                return True
        return False

    # ------------------------------------------------------------------ 反馈与异常

    def _consume_feedback(self, snapshot: dict, now: float) -> list:
        reports = []
        feedback = snapshot.get("tile_last_finished")
        if not isinstance(feedback, dict) or not feedback.get("tile_id"):
            return reports
        key = (str(feedback["tile_id"]), float(feedback.get("score") or 0.0))
        if key == self.last_feedback:
            return reports
        self.last_feedback = key
        pending, self.pending = self.pending, None
        if pending is None or pending["tile_id"] != key[0]:
            return reports
        tile, realized = key
        if realized > self.banked.get(tile, 0.0):
            self.banked[tile] = realized
            base = realized / (1.0 + pending["bonus"])
            self.base_total += base - self.banked_base.get(tile, 0.0)
            self.banked_base[tile] = base
        clean = pending["clean"] and not self._events_now(snapshot, tile, pending["region"])
        if self.p["log_reads"] and realized > 0 and pending["expected"] > 0:
            print(f"observer: read tile={tile} region={pending['region']} ratio={realized / pending['expected']:.4f} "
                  f"simple={realized / pending['simple']:.4f} clean={int(clean)}", file=sys.stderr, flush=True)
        if realized > 0 and pending["expected"] > 0 and clean:
            reports.extend(self._evidence(pending, realized / pending["expected"], now))
        return reports

    @staticmethod
    def _events_now(snapshot: dict, tile: str, region: str) -> bool:
        """曝光刚结束时该天区（或同分区的候选）是否有活跃天气事件：曝光中途开始的事件会伪装成故障读数。"""
        if (snapshot.get("current_site_weather") or {}).get("active_event_ids"):
            return True
        same_region = False
        for c in snapshot.get("candidate_tiles") or []:
            if str(c["tile_id"]) == tile:
                return bool(c["effective_weather"].get("active_event_ids"))
            if str(c["region_id"]) == region and c["effective_weather"].get("active_event_ids"):
                same_region = True
        return same_region

    def _evidence(self, pending: dict, ratio: float, now: float) -> list:
        reports = []
        tile, region = pending["tile_id"], pending["region"]
        p = self.p
        if p["report_tags"]:
            if p["nova_threshold"] <= ratio <= p["nova_max"] and (tile, "NOVA") not in self.reported_tags:
                self.reported_tags.add((tile, "NOVA"))
                reports.append({"kind": "NOVA", "tile_id": tile})
            low, high = p["reddening_band"]
            reads = self.tile_reads.setdefault(tile, [])
            reads.append(ratio)
            if (len(reads) >= 2 and all(low <= r <= high for r in reads[-2:]) and region not in self.suspect
                    and region not in self.fault_regions and (tile, "Reddening") not in self.reported_tags):
                self.reported_tags.add((tile, "Reddening"))
                reports.append({"kind": "Reddening", "tile_id": tile})

        reads = self.region_reads[region]
        reads.append(ratio)
        del reads[:-4]
        is_low = ratio <= p["fault_threshold"]
        if is_low:
            lows = [r for r in reads[-3:] if r <= p["fault_threshold"]]
            self.suspect[region] = min(1.0, max(0.3, sum(lows) / len(lows) / p["mean_jitter"]))
            self.low_since_report[region] = self.low_since_report.get(region, 0) + 1
        elif ratio >= p["fault_clear_threshold"]:
            self.suspect.pop(region, None)

        self.recent_reads.append(ratio)
        del self.recent_reads[:-int(p["fault_window"])]
        if p["report_faults"] and is_low and now >= self.fault_repair_until:
            confirmed = sum(r <= p["fault_threshold"] for r in self.recent_reads) >= 2
            if self.fault_pending_since is None:
                # 首次误报免费：单次读数足够低就立即上报；之后需要近期任意分区的 2 次低读数确认
                if (self.fault_misreports == 0 and ratio <= p["fault_single_threshold"]) or confirmed:
                    reports.append(self._fault_report(now))
            elif confirmed and now - self.fault_pending_since >= 6 * 3600:
                # 已报过但证据又在累积：若第一次是误报而故障现在才开始，这次会被判为正确
                reports.append(self._fault_report(now))
        return reports

    def _fault_report(self, now: float) -> dict:
        self.fault_pending_since = now
        self.low_since_report = {}
        self.recent_reads = []
        return {"kind": "Instrument_Failure"}

    def _budget(self, now: float) -> None:
        """按“已用墙钟 ÷ 巡天进度”推算整场耗时，超出预算比例就降级规划，避免时钟耗尽。"""
        if self.clock_started is None:
            self.clock_started = time.monotonic()
            return
        if self.survey_span is None:
            return
        progress = (now - self.survey_span[0]) / (self.survey_span[1] - self.survey_span[0])
        if progress < 0.05:
            return
        projected = (time.monotonic() - self.clock_started) / progress
        if self.planning and projected > self.p["budget_disable_at"] * self.wallclock:
            print(f"observer: projected {projected:.0f}s > {self.p['budget_disable_at']:.0%} of clock; planning off",
                  file=sys.stderr, flush=True)
            self.planning = False
        elif self.hourly and projected > self.p["budget_reduce_at"] * self.wallclock:
            print(f"observer: projected {projected:.0f}s > {self.p['budget_reduce_at']:.0%} of clock; nightly planning only",
                  file=sys.stderr, flush=True)
            self.hourly = False

    def _plan(self, snapshot: dict, now: float) -> None:
        self._budget(now)
        if not self.planning:
            return
        try:
            if self.planner is None:
                from planner import Planner
                self.planner = Planner(self.initial, self.p)
            self.planner.observe_weather(snapshot)
            night = (snapshot.get("cursor") or {}).get("night_id")
            hourly_due = self.hourly and now - self.planned_at >= float(self.p["plan_every_seconds"])
            if night != self.planned_night or hourly_due:
                self.planned_night, self.planned_at = night, now
                exclude = self.required | set(self._request_options(snapshot))
                counts = dict(zip(self.regions, self.counts))
                coverage = (self.coverage_weight, self.base_total, counts) if self.p["plan_coverage"] else None
                self.planner.replan(now, self.completed, exclude, coverage)
        except Exception:  # noqa: BLE001  规划失败不能拖垮决策：退回当晚时机因子
            traceback.print_exc(file=sys.stderr)
            self.planning = False
            self.planner = None

    def _consume_fault_status(self, snapshot: dict, now: float) -> None:
        status = snapshot.get("fault_status")
        if not isinstance(status, dict):
            return
        if status.get("status") == "fault":
            payload = status.get("spatial_scope_payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            if status.get("spatial_scope_type") == "REGION_SET":
                self.fault_regions = {str(r) for r in payload.get("region_ids", [])}
            self.fault_multiplier = float(status.get("instrument_efficiency_multiplier") or 1.0)
            self.fault_repair_until = epoch(status["repair_complete_utc"])
            self.fault_pending_since = None
        elif status.get("status") == "normal":
            self.fault_pending_since = None
            self.fault_misreports += 1
            self.suspect.clear()
            self.low_since_report = {}
            self.recent_reads = []

    def _segments(self, c: dict, weather: dict, now: float, snapshot: dict):
        """按评分器的方式把这次曝光按时隙边界切段：[(时长占比, 不含效率的质量)]，每段取中点的 airmass 与月光因子；
        天气用提交时的有效天气。没有几何模块时返回 None。"""
        planner = self.planner if self.planning else None
        tile = planner.tiles.get(str(c["tile_id"])) if planner is not None else None
        if tile is None:
            return None
        w0 = float(weather["transparency"]) * float(weather["sky_quality"]) / float(weather["seeing_arcsec"])
        exposure = float(c["nominal_exptime_seconds"])
        slot = float(planner.slot)
        offset = float((snapshot.get("cursor") or {}).get("slot_offset_seconds") or 0.0)
        start, remaining, slot_end, segments = now, exposure, now - offset + slot, []
        while remaining > 1e-6:
            seconds = min(remaining, slot_end - start)
            _alt, am, lunar = planner.site.moment(start + seconds / 2.0).quality(tile["geo"])
            segments.append((seconds / exposure, min(w0 / am ** self.airmass_exp, self.max_quality) * lunar))
            remaining -= seconds
            start += seconds
            slot_end += slot
        return segments

    def _segment_score(self, segments, program: str) -> float:
        """分段质量 → 单位天区价值的得分（含该档位加成，只在分段档位与所选档位一致时计入）。"""
        return sum(f * q * (1.0 + (self.bonus[program] if self._band(q) == program else 0.0)) for f, q in segments)

    def _expected_score(self, c: dict, program: str, weather: dict, now: float, snapshot: dict) -> float:
        """估计这次曝光的得分（不含效率与异常），作为异常比值的分母。分段几何比只用开始时刻准，比值里只剩天气漂移与仪器侧因素。"""
        value = float(c["tile_science_value"])
        segments = self._segments(c, weather, now, snapshot) if self.p["segment_expectation"] else None
        if segments is None:
            return value * self._quality(weather, c["geometry"]) * (1.0 + self.bonus[program])
        return value * self._segment_score(segments, program)

    def _region_multiplier(self, region: str, now: float) -> float:
        if region in self.fault_regions and now < self.fault_repair_until:
            return self.fault_multiplier
        if self.fault_regions and now >= self.fault_repair_until:
            self.fault_regions = set()
            self.suspect.clear()
        return self.suspect.get(region, 1.0)

    # ------------------------------------------------------------------ 候选估值

    def _quality(self, weather: dict, geometry: dict) -> float:
        airmass = float(geometry["airmass"])
        atmospheric = float(weather["transparency"]) * float(weather["sky_quality"]) / (
            float(weather["seeing_arcsec"]) * airmass ** self.airmass_exp)
        return min(atmospheric, self.max_quality) * float(geometry["lunar_quality_factor"])

    def _band(self, quality: float) -> str:
        if quality >= self.dark:
            return "DARK"
        if quality >= self.bright:
            return "BRIGHT"
        return "BACKUP"

    def _request_options(self, snapshot: dict) -> dict:
        options: dict = {}
        for request in snapshot.get("active_requests") or []:
            if request.get("is_complete"):
                continue
            remaining = max(1, int(request["required_tile_count"]) - int(request.get("satisfied_tile_count", 0)))
            value = (float(request["completion_reward"]) + float(request["miss_penalty"])) / remaining
            for item in request.get("tile_requirements") or []:
                if int(item.get("remaining_visits", item.get("required_visits", 0))) > 0:
                    options.setdefault(str(item["tile_id"]), []).append((str(request["request_id"]), value))
        return options

    def _timing(self, tile: str, airmass: float, now: float, exposure: float) -> float:
        alpha = self.p["timing_alpha"]
        if alpha <= 0:
            return 1.0
        for best_time, best_airmass, end, start in self.tonight.get(tile, ()):
            if not (start <= now < end) or airmass <= best_airmass:
                continue
            later_tonight = best_time > now + exposure / 2 and best_time + exposure / 2 <= end
            later_nights = self.p["timing_future_nights"] and self.tile_until.get(tile, 0.0) > now + 86400
            if later_tonight or later_nights:
                return (best_airmass / airmass) ** alpha
            return 1.0
        return 1.0

    def _required_deadline(self, now: float):
        """即将开启的 REQUIRED 短窗口：返回本次曝光最晚要结束的时刻，以及受保护的天区。"""
        if not self.p["protect_required"]:
            return None, set()
        deadline, protected = None, set()
        for tile in self.required - self.completed:
            exposure = self.tile_exposure.get(tile, 0.0)
            for _best, _airmass, end, start in self.tonight.get(tile, ()):
                latest = end - exposure
                if start > now and latest - start < self.p["protect_max_interval"] and start - now <= self.p["protect_lookahead"]:
                    protected.add(tile)
                    deadline = latest if deadline is None else min(deadline, latest)
        return deadline, protected

    def _choose(self, snapshot: dict, now: float, v3: bool, deadline=None, protected=frozenset()):
        counts = self.counts
        n = len(counts)
        total_done = sum(counts)
        sum_sq = sum(c * c for c in counts)
        evenness = jain(counts)
        weight = self.coverage_weight
        requests = self._request_options(snapshot)
        mu = self.planner.mu if self.planning and self.planner is not None else None
        entries = []
        for c in snapshot.get("candidate_tiles") or []:
            weather = c["effective_weather"]
            if not weather.get("is_observable"):
                continue
            exposure = float(c["nominal_exptime_seconds"])
            if now + exposure > epoch(c["window_end_utc"]):
                continue
            tile = str(c["tile_id"])
            region = str(c["region_id"])
            done = bool(c.get("already_completed"))
            options = requests.get(tile)
            if done and not v3 and not options:
                continue  # 练习场景（v1 协议）里重复观测是无效动作
            quality = self._quality(weather, c["geometry"])
            band = self._band(quality)
            multiplier = self._region_multiplier(region, now)
            base = float(c["tile_science_value"]) * quality * self.p["mean_jitter"] * multiplier
            realized = base * (1.0 + self.bonus[band])
            if done:
                science = max(0.0, realized - self.banked.get(tile, realized))
                coverage = weight * max(0.0, base - self.banked_base.get(tile, base)) * evenness
                terminal = 0.0
            else:
                science = realized
                coverage = 0.0
                if weight > 0:
                    x = counts[self.region_index[region]]
                    after = (total_done + 1) ** 2 / (n * (sum_sq + 2 * x + 1))
                    coverage = weight * ((self.base_total + base) * after - self.base_total * evenness)
                if c.get("scheduling_class") == "REQUIRED":
                    terminal = self.required_miss
                elif self.flexible_done.get(region, 0) < self.quota:
                    terminal = self.flexible_short
                else:
                    terminal = 0.0
            if deadline is not None and now + exposure > deadline and tile not in protected:
                continue  # 会占住即将开启的 REQUIRED 短窗口
            if mu is not None:
                timing, opportunity = 1.0, self.p["plan_beta"] * mu.get(tile, 0.0) if not options else 0.0
            else:
                timing = 1.0 if tile in self.required and not done else self._timing(tile, float(c["geometry"]["airmass"]), now, exposure)
                opportunity = 0.0
            for request_id, request_value in options or (("", 0.0),):
                gain = science + coverage + terminal + request_value
                index = (gain - opportunity) / exposure * timing
                context = (done, region, multiplier, terminal, request_value, opportunity, timing, exposure, coverage)
                entries.append(((index, gain, tile, request_id), c, band, weather, quality, context))
        if not entries:
            return None
        entries.sort(key=lambda entry: entry[0], reverse=True)
        top = int(self.p["refine_top_k"])
        if top <= 0 or self.planner is None or not self.planning:
            return entries[0][:5]
        refined_entries = []
        for key, c, band, weather, quality, context in entries[:top]:
            refined = self._refine(c, weather, now, snapshot, context, key[3], counts, total_done, sum_sq, evenness)
            if refined is None:
                refined = (key, band)
            refined_entries.append((refined[0], c, refined[1], weather, quality))
        refined_entries.sort(key=lambda entry: entry[0], reverse=True)
        if int(self.p["rollout_k"]) > 1 and len(refined_entries) > 1:
            return self._rollout(refined_entries[: int(self.p["rollout_k"])], entries, now)
        return refined_entries[0]

    def _rollout(self, finalists, entries, now):
        """对前几名分别往后推演一段时间（沿用 (收益 − μ)/曝光 规则，天气按当前有效天气持续、不考虑关闭），
        按单位时间累计的 (收益 − μ) 选择。候选池取当前快照的候选，每一步按曝光中点重算几何。"""
        planner = self.planner
        horizon = float(self.p["rollout_seconds"])
        min_alt = float(self.p["plan_min_altitude"])
        pool = []
        for key, c, _band, weather, _quality, context in entries:
            done, _region, multiplier, terminal, request_value, opportunity, _timing, exposure, coverage = context
            tile = str(c["tile_id"])
            geo = planner.tiles.get(tile)
            if geo is None:
                continue
            w0 = float(weather["transparency"]) * float(weather["sky_quality"]) / float(weather["seeing_arcsec"])
            pool.append((tile, key[3], exposure, epoch(c["window_end_utc"]), geo["geo"],
                         float(c["tile_science_value"]) * self.p["mean_jitter"] * multiplier, w0,
                         self.banked.get(tile) if done else None, coverage + terminal + request_value, opportunity))
        best, best_rate = finalists[0], None
        for finalist in finalists:
            (_index, gain, tile, request_id) = finalist[0]
            first = next((item for item in pool if item[0] == tile and item[1] == request_id), None)
            if first is None:
                continue
            taken = {tile}
            reward = gain - first[9]
            clock = now + first[2]
            while clock < now + horizon:
                moments = {}
                pick, pick_score, pick_gain = None, None, 0.0
                for item in pool:
                    if item[0] in taken or clock + item[2] > item[3]:
                        continue
                    moment = moments.get(item[2])
                    if moment is None:
                        moment = moments[item[2]] = planner.site.moment(clock + item[2] / 2.0)
                    altitude, am, lunar = moment.quality(item[4])
                    if altitude < min_alt:
                        continue
                    q = min(item[6] / am ** self.airmass_exp, self.max_quality) * lunar
                    realized = item[5] * q * (1.0 + self.bonus[self._band(q)])
                    science = realized if item[7] is None else max(0.0, realized - item[7])
                    g = science + item[8]
                    score = (g - item[9]) / item[2]
                    if pick_score is None or score > pick_score:
                        pick, pick_score, pick_gain = item, score, g
                if pick is None:
                    break
                taken.add(pick[0])
                reward += pick_gain - pick[9]
                clock += pick[2]
            rate = reward / max(1.0, clock - now)
            if best_rate is None or rate > best_rate:
                best, best_rate = finalist, rate
        return best

    def _refine(self, c, weather, now, snapshot, context, request_id, counts, total_done, sum_sq, evenness):
        """用分段几何重新估计候选的收益，并选择分段加成总和最大的档位。"""
        done, region, multiplier, terminal, request_value, opportunity, timing, exposure, _coverage = context
        segments = self._segments(c, weather, now, snapshot)
        if not segments:
            return None
        value = float(c["tile_science_value"]) * self.p["mean_jitter"] * multiplier
        program = max(("DARK", "BRIGHT", "BACKUP"), key=lambda p: (self._segment_score(segments, p), p))
        realized = value * self._segment_score(segments, program)
        base = value * sum(f * q for f, q in segments)
        tile = str(c["tile_id"])
        if done:
            science = max(0.0, realized - self.banked.get(tile, realized))
            coverage = self.coverage_weight * max(0.0, base - self.banked_base.get(tile, base)) * evenness
        else:
            science = realized
            coverage = 0.0
            if self.coverage_weight > 0:
                x = counts[self.region_index[region]]
                after = (total_done + 1) ** 2 / (len(counts) * (sum_sq + 2 * x + 1))
                coverage = self.coverage_weight * ((self.base_total + base) * after - self.base_total * evenness)
        gain = science + coverage + terminal + request_value
        return ((gain - opportunity) / exposure * timing, gain, tile, request_id), program

    # ------------------------------------------------------------------ 决策入口

    def decide(self, snapshot: dict) -> dict:
        now = epoch(snapshot["cursor"]["timestamp_utc"])
        v3 = snapshot.get("schema_version") == "decision-snapshot-v3"
        self._sync_progress(snapshot)
        self._load_windows(snapshot, now)
        self._plan(snapshot, now)
        weekly = snapshot.get("weekly")
        if isinstance(weekly, dict) and weekly.get("weather_forecast") is not None:
            self.forecasts = list(weekly["weather_forecast"])
        reports = []
        if v3:
            self._consume_fault_status(snapshot, now)
            reports = self._consume_feedback(snapshot, now)

        deadline, protected = self._required_deadline(now)
        best = self._choose(snapshot, now, v3, deadline, protected)
        if best is None and deadline is not None:
            cursor = snapshot["cursor"]
            slot = float((snapshot.get("current_site_weather") or {}).get("duration_seconds") or 900)
            next_boundary = now + slot - float(cursor.get("slot_offset_seconds") or 0) % slot
            if next_boundary > deadline:
                best = self._choose(snapshot, now, v3)  # 等待也赶不上这个窗口，保护无意义
        planned = self.planning and self.planner is not None and not self.p["plan_allow_wait"]
        if best is None or (best[0][1] <= 0 if planned else best[0][0] <= 0):
            self.pending = None
            decision = {"action": "wait", "tile_id": "", "program": "", "request_id": "",
                        "reason": "no candidate with positive marginal gain"}
        else:
            (index, gain, tile, request_id), c, band, weather, _quality = best
            site = snapshot.get("current_site_weather") or {}
            clean = not weather.get("active_event_ids") and not site.get("active_event_ids") and not self._under_cold_wave(now)
            # 比值的分母用不含效率与故障的公开公式估值，与平台 tile_last_finished 的口径一致
            expected = self._expected_score(c, band, weather, now, snapshot)
            simple = float(c["tile_science_value"]) * self._quality(weather, c["geometry"]) * (1.0 + self.bonus[band])
            self.pending = {"tile_id": tile, "region": str(c["region_id"]), "bonus": self.bonus[band],
                            "expected": expected, "simple": simple, "clean": clean}
            decision = {"action": "observe", "tile_id": tile, "program": band, "request_id": request_id,
                        "reason": f"marginal gain {gain:.1f} ({index * 3600:.1f}/h)"}
        decision["decision_source"] = "deterministic"
        if reports:
            decision["reports"] = reports
        return decision
