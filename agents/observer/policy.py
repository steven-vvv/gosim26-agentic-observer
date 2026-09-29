"""确定性观测策略：按“真实边际收益 / 曝光秒数”排序，规划器可选 NumPy。

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
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import events as events_mod
from model_advisor import ModelAdvisor

DEFAULTS = {
    "timing_alpha": 1.0,
    "timing_future_nights": False,  # 实测开启会掉分：时间稀缺时“明晚再拍”并不成立
    "plan": True,                   # 夜间价格规划（planner.py）；关闭时退回当晚时机因子
    "plan_beta": 1.0,
    "plan_coverage": True,          # 规划时把覆盖边际计入每个时段的价值
    "plan_every_seconds": 1800,     # 夜内每隔多久重新规划一次（真实系统 46 场：半小时 +0.11%，与 60 分钟推演合计 +0.145%）
    "budget_reduce_at": 0.60,       # 预计整场耗时超过时钟的这一比例：改为每夜只规划一次
    "budget_disable_at": 0.85,      # 超过这一比例：关闭规划，退回计算量很小的时机因子
    "budget_frequent_at": 0.50,     # 超过这一比例：夜内重规划间隔不短于 1 小时（plan_every_seconds 小于 1 小时时才有影响）
    "plan_horizon_nights": 14,
    "plan_persistence": False,      # 规划时按天气持续性调整各夜的天气分布（夜级 AR(1)）；离线 +0.19%，真实系统 −0.23%，不用
    "persistence_phi": 0.82,        # 公开的决赛天气配置里的 night_correlation
    "plan_recent_weather": True,   # 同夜规划按最新未受事件影响的天气观测修正未来价值；92 场 +0.053%
    "plan_recent_weather_hours": 10.0,
    "plan_recent_weather_floor": 0.82,  # 夜级天气状态带来的同夜相关性下限
    "plan_recent_weather_smoothing_slots": 1,  # 实验：用最近若干无事件时隙估计同夜天气状态
    "plan_recent_weather_conditional": False,  # 按相关系数收窄条件天气分布；实验参数
    "plan_clean_weather_quantiles": False,  # 只用无站点事件的天气估计长期分位数；实验参数
    "plan_trim_current_bin": False,  # 半小时重规划时，当前小时只用尚未过去的几何采样点
    "repeat_exact_coverage": False,  # 复拍仅在总分刷新纪录时结算覆盖分；基础分增量可为负
    "plan_first_iterations": 300,
    "plan_iterations": 150,
    "plan_min_altitude": 30.0,
    "numpy_planner": True,          # numpy 可用时规划器走向量化路径（与纯 Python 逐位相同）
    "plan_numpy_geometry": True,   # numpy 批算规划几何；缺少 numpy 时使用标量路径
    "plan_required": True,          # 机会充裕的 REQUIRED 天区参与定价（其余一有窗口就拍；46 场 +0.195%，零缺失）
    "required_min_bins": 40,        # 视野内可行时段至少这么多个
    "required_min_nights": 5.0,     # 且离可用期结束至少这么多夜
    "plan_lambda_average": 0,       # >0：用最后这么多次迭代的平均 λ 定价
    "plan_fault_aware": False,      # 规划时把已确认 / 疑似故障分区在修复前的时段价值乘以故障乘数（46 场约 0，不用）
    "plan_allow_wait": False,       # 所有候选扣除机会价值后都为负时是否等待（决赛规模下时间稀缺，不等）
    "plan_events": "forecast",      # 规划时计入天气事件（"truth_weekly" 只用于诊断）：每周预报（46 场 +0.121%，标准误 0.048）；"truth" 读真实事件只用于本地上限试验（+0.487%）；"off" 与 obs15 逐位相同
    "plan_repeats": False,          # 已完成、入账分明显低于潜力的天区（故障期、寒潮期、坏天气时拍的）以复拍增益参与定价
    "plan_repeat_min_gain": 20.0,   # 最佳几何下的潜在增益至少这么多分才参与（控制规划规模）
    "plan_events_kinds": "all",     # 本地试验：规划器只看 "close"（闭站类）或 "quality"（质量类）事件
    "plan_events_truth_root": "",   # truth 模式：场景根目录，事件文件为 <根目录>/<SAC_SCENARIO>/outputs/reference/weather_events.csv
    "segment_expectation": True,    # 异常比值的分母按分段中点几何计算
    "refine_top_k": 12,             # 对粗排前 K 名按分段几何精算收益并挑选档位（0 关闭）
    "rollout_k": 5,                 # 对精算后前 K 名往后推演（≤1 关闭；离线实验 +0.4–0.8 个百分点）
    "rollout_seconds": 3600,        # 真实系统 46 场：40 → 60 分钟 +0.09%
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
    "dip_filter": True,             # 其他分区也普遍偏低时不把低读数当作故障/红化证据
    "dip_window": 8,
    "dip_median": 0.85,
    "report_faults": True,
    "report_tags": True,
    # 精确读数：曝光结束后按各段所在时隙的真实有效天气重算期望（事后期望），比值只剩仪器侧因素；
    # 30 个决赛规模场景上普通 [0.900, 1.000]、红化 [0.720, 0.800]、新星 [1.353, 1.499]、故障 ≤ 0.70，互不重叠
    "exact_reads": True,            # 有精确读数时按它上报标签
    "exact_faults": True,           # 故障证据也用精确读数（单次即确认，不经全局下沉过滤）
    "exact_fault_threshold": 0.705,
    "exact_nova_min": 1.30,
    "exact_reddening_band": [0.71, 0.81],
    "single_fault": True,           # 每场至多一次故障：确认后不再上报
    "cold_tags": True,              # 寒潮期读数按事件归一化后上报标签（乘数 = 同一事件此前读数的中位数 / 0.95）
    "cold_min_reads": 3,
    "cold_reddening_band": [0.73, 0.79],
    "cold_faults": True,            # 归一化后的寒潮期读数也作为故障证据（46 场 +0.048%，检出延迟中位数 14.9 → 2.9 小时）
    "cold_online": True,            # 寒潮期在线估值乘以效率乘数（快照里没有仪器效率，否则现在拍的价值被高估约 1/0.76）；92 场 +0.504%（标准误 0.053）
    "cold_default_factor": 0.75,    # 既没有读数也没有预报时的乘数：1 + severity·(0.68 − 1)，severity ~ U(0.55, 1) 的均值
    "closure_risk": False,          # 跨时隙边界的曝光按“下一时隙关闭”的风险折算收益与耗时
    "closure_prior": 0.014,         # 开 → 关的每时隙概率先验（公开的决赛天气配置：10–11 月约 0.0136–0.014）
    "closure_risk_forecast": False, # 只在预报显示下一时隙闭站风险明显时，才对跨时隙曝光做中断风险折算
    "closure_risk_min": 0.1,        # 曝光跨过的边界里最大的“下一时隙关闭”概率超过它才折算（平时与不折算逐位相同）
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
        self.advisor = ModelAdvisor.from_env(Path(__file__).with_name("llm.config.json"))
        self.advised_week = None
        self.base_plan_horizon = int(params["plan_horizon_nights"])
        self.base_plan_every = float(params["plan_every_seconds"])
        self.advised_every = self.base_plan_every
        self.weekly_plan = None
        self.weekly_plan_source = "deterministic"
        self.on_plan = None
        self.on_event = None
        self.action_advised_night = None
        self._model_candidates = []
        self._model_weekly = {}
        self._model_night_start = {}
        clocks = [float(v) for v in (initial.get("global_wallclock_seconds"), os.environ.get("SAC_WALLCLOCK_SECONDS")) if v]
        self.wallclock = min(clocks) if clocks else 3600.0  # 本地 runner 的 --wallclock 只体现在环境变量里
        self.clock_started = None
        self.hourly = True
        self.decisions = 0
        self.frequent = True            # 允许短于 1 小时的夜内重规划间隔（时间紧时由 _budget 关掉）
        self.budget_mark = (0.0, 0.0)   # 外推起点：第一次检查或上次降级时的 (已用墙钟, 巡天进度)
        self.budget_started = False
        calendar = initial.get("calendar") or {}
        self.survey_span = None
        if calendar.get("first_night") and calendar.get("last_night"):
            first = epoch(f'{calendar["first_night"]}T12:00:00Z')
            last = epoch(f'{calendar["last_night"]}T12:00:00Z') + 86400
            self.survey_span = (first, last)
        contract = initial["scoring_contract"]
        score = contract["score_config"]
        # 与官方 contracts.anomaly_mechanics_enabled 同一口径；快照 schema 不认识时据此决定是否启用异常机制
        self.mechanics = any(k in score for k in ("repeat_observation", "reporting", "anomaly_tags", "fault_response"))
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
        self._pending_events = None     # 还没交给规划器的天气事件 (事件列表, 模式, 有效期末)

        # 故障与标签
        self.region_reads: dict = {r: [] for r in self.regions}
        self.suspect: dict = {}         # region -> 估计的效率乘数（自己检测到、尚未得到回执）
        self.fault_regions: set = set()
        self.fault_multiplier = 1.0
        self.fault_repair_until = 0.0
        self.fault_pending_since = None  # 最近一次故障上报的时间（等待平台回执）
        self.fault_misreports = 0
        self.fault_confirmed = False    # 平台已确认过故障（每场至多一次）
        self.low_since_report: dict = {}
        self.recent_reads: list = []    # 上次故障上报以来的干净读数比值（跨分区）
        self.global_reads: list = []    # 最近的干净读数 (分区, 比值)，用于识别全局性下沉（如漏报的寒潮）
        self.reported_tags: set = set()
        self.tile_reads: dict = {}
        self.cold_reads: dict = {}      # 寒潮事件 → 该事件内的读数（估计效率乘数）
        self.cold_clean: dict = {}      # 同上，但不含疑似 / 已确认故障分区的读数（在线估值用，故障会把中位数拉低）
        self.cold_factor = 1.0          # 本次决策的寒潮效率乘数（cold_online）
        self.last_site = None           # (时隙 ID, 夜 ID, 是否可观测)：估计开 → 关的转移概率
        self.open_transitions = 0
        self.closures = 0

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

    def _diagnostic(self, event: str, payload: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event, payload)
            except Exception as exc:  # noqa: BLE001
                print(f"observer: diagnostic callback failed: {type(exc).__name__}", file=sys.stderr, flush=True)
        else:
            print(f"observer: telemetry {json.dumps({'event': event, **payload}, separators=(',', ':'))}",
                  file=sys.stderr, flush=True)

    def _forecast_events(self) -> list:
        """每周预报 → 规划用的事件；格式不对的条目跳过（只记一次）。"""
        events = []
        for item in self.forecasts:
            try:
                event = events_mod.from_forecast(item)
            except Exception as exc:  # noqa: BLE001
                self._diagnostic("warning", {"stage": "forecast", "reason": type(exc).__name__})
                continue
            if event is not None:
                events.append(event)
        return events

    def _cold_factor(self, snapshot: dict, now: float) -> float:
        """寒潮期在线估值的效率乘数。站点可观测却有生效事件只可能是寒潮（唯一带效率乘数、又不闭站的全站事件）。

        依次用：同一事件此前读数（不含故障分区）的中位数 / 0.95 → 覆盖此刻的寒潮预报的 1 + severity·(0.68 − 1) → 默认值。"""
        site = snapshot.get("current_site_weather") or {}
        ids = sorted(str(e) for e in site.get("active_event_ids") or [])
        if not ids or not site.get("is_observable"):
            return 1.0
        prior = self.cold_clean.get("+".join(ids))
        if prior and len(prior) >= int(self.p["cold_min_reads"]):
            ordered = sorted(prior)
            middle = len(ordered) // 2
            median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
            return min(1.0, max(0.5, median / float(self.p["mean_jitter"])))
        for f in self.forecasts:
            if f.get("condition") != "cold_wave":
                continue
            try:
                start = epoch(f["predicted_start_utc"]) - float(f.get("start_uncertainty_seconds") or 0)
                end = epoch(f["predicted_end_utc"]) + float(f.get("end_uncertainty_seconds") or 0)
                severity = min(1.0, max(0.0, float(f.get("severity") or 0.0)))
            except Exception:  # noqa: BLE001, S112  格式不对的预报条目跳过（_forecast_events 已记过警告）
                continue
            if start <= now < end:
                return 1.0 + severity * (0.68 - 1.0)
        return float(self.p["cold_default_factor"])

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

    def _track_closures(self, snapshot: dict) -> None:
        """统计同一夜内相邻时隙“开 → 关”的转移，估计下一时隙关闭的概率。"""
        site = snapshot.get("current_site_weather") or {}
        cursor = snapshot.get("cursor") or {}
        slot, night = cursor.get("slot_id"), cursor.get("night_id")
        if slot is None or (self.last_site and self.last_site[0] == slot):
            return
        observable = bool(site.get("is_observable"))
        if self.last_site and self.last_site[1] == night and self.last_site[2]:
            self.open_transitions += 1
            self.closures += 0 if observable else 1
        self.last_site = (slot, night, observable)

    def _closure_probability(self) -> float:
        prior = float(self.p["closure_prior"])
        return (self.closures + prior * 200.0) / (self.open_transitions + 200.0)

    def _risk_adjust(self, net: float, exposure: float, durations, risks=None) -> tuple:
        """跨时隙边界的曝光：下一时隙关闭就中断（已用时间作废，天区不消耗，净值记 0）。返回 (期望净值, 期望耗时)。
        risks 给出每个边界各自的关闭概率；不给时都用背景关闭率。"""
        p = self._closure_probability()
        survive, elapsed, expected_time = 1.0, 0.0, 0.0
        for k, seconds in enumerate(durations[:-1]):
            if risks is not None:
                p = risks[k]
            elapsed += seconds
            expected_time += survive * p * elapsed
            survive *= 1.0 - p
        return survive * net, expected_time + survive * exposure

    def _boundary_risks(self, start: float, durations):
        """曝光跨过的每个时隙边界上“下一时隙关闭”的概率：背景关闭率，叠加预报开放率在边界前后的下降（降雨、龙卷风开始）。
        没有可用的预报时返回 None。"""
        planner = self.planner if self.planning else None
        if planner is None or planner.events is None or planner.events_mode != "forecast":
            return None
        try:
            background = self._closure_probability()
            rate = planner._open_rate()
            risks, t = [], start
            for seconds in durations[:-1]:
                t += seconds
                p = background
                if t < planner.events_until:
                    before, after = planner._site_open(t - 450.0, rate), planner._site_open(t + 450.0, rate)
                    if before > 0.0:
                        p = 1.0 - (1.0 - background) * min(1.0, after / before)
                risks.append(p)
            return risks
        except Exception as exc:  # noqa: BLE001  风险估计出错就不折算
            self._diagnostic("policy_error", {"stage": "boundary_risk", "reason": type(exc).__name__})
            return None

    def _forecast_risks(self, start: float, durations):
        """closure_risk_forecast：最大的边界关闭概率超过阈值时返回各边界的概率，否则返回 None（不折算）。"""
        if not self.p["closure_risk_forecast"] or len(durations) < 2:
            return None
        risks = self._boundary_risks(start, durations)
        return risks if risks and max(risks) > float(self.p["closure_risk_min"]) else None

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
        posterior = self._posterior_expected(pending, snapshot, now) if self.p["exact_reads"] or self.p["log_reads"] else None
        if self.p["log_reads"] and realized > 0 and pending["expected"] > 0:
            region = pending["region"]
            cold = sorted(set(pending.get("site_event_ids") or [])
                          | {str(e) for e in (snapshot.get("current_site_weather") or {}).get("active_event_ids") or []})
            cold_expected = self._posterior_expected(pending, snapshot, now, allow_site_events=True) if cold else None
            self._diagnostic("feedback_read", {
                "timestamp": now, "tile_id": tile, "region": region,
                "ratio": realized / pending["expected"], "simple": realized / pending["simple"],
                "clean": clean, "dip": self._dip_active(region), "suspect": region in self.suspect,
                "fault_region": region in self.fault_regions, "reads": len(self.tile_reads.get(tile, [])),
                "segments": len(pending["geometry"] or ()), "exact": realized / posterior if posterior else None,
                "cold_event_ids": cold, "cold_exact": realized / cold_expected if cold_expected else None,
            })
        if self.p["cold_tags"] and realized > 0 and not posterior:
            reports.extend(self._cold_evidence(pending, snapshot, now, realized))
        exact = realized / posterior if posterior and realized > 0 and self.p["exact_reads"] else None
        if exact is not None and self.p["exact_faults"]:
            reports.extend(self._evidence(pending, exact, now, exact=True))
            return reports
        if realized > 0 and pending["expected"] > 0 and clean:
            reports.extend(self._evidence(pending, realized / pending["expected"], now))
        if exact is not None and self.p["report_tags"]:
            reports.extend(self._tag_reports(pending, exact, exact=True))
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

    def _dip_active(self, region: str) -> bool:
        """其他分区最近的干净读数中位数是否偏低（只读，不改状态）。"""
        others = [r for reg, r in self.global_reads if reg != region][-int(self.p["dip_window"]):]
        if len(others) < 3:
            return False
        others.sort()
        return others[len(others) // 2] < self.p["dip_median"]

    def _global_dip(self, region: str, ratio: float) -> bool:
        """其他分区最近的读数也普遍偏低时，这次偏低是全局性的（寒潮等），不作为故障或红化的证据。"""
        dip = self._dip_active(region)
        self.global_reads.append((region, ratio))
        del self.global_reads[:-32]
        return dip

    def _cold_evidence(self, pending: dict, snapshot: dict, now: float, realized: float) -> list:
        """寒潮（站点可观测时仍有生效事件）只压低效率，单个事件内乘数不变：用同一事件此前读数的中位数估计它，
        读数除以乘数后按精确读数的口径判断标签（46 场离线：红化补报对 25 错 1、新星对 34 错 2，每场 +118 分）。"""
        site = snapshot.get("current_site_weather") or {}
        events = sorted(set(pending.get("site_event_ids") or []) | {str(e) for e in site.get("active_event_ids") or []})
        if not events:
            return []
        expected = self._posterior_expected(pending, snapshot, now, allow_site_events=True)
        if not expected:
            return []
        ratio = realized / expected
        prior = self.cold_reads.setdefault("+".join(events), [])
        reports = []
        if len(prior) >= int(self.p["cold_min_reads"]):
            ordered = sorted(prior)
            middle = len(ordered) // 2
            median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
            normalized = ratio / (median / float(self.p["mean_jitter"]))
            tile, region = pending["tile_id"], pending["region"]
            if self.p["report_tags"] and region not in self.suspect and region not in self.fault_regions:
                low, high = self.p["cold_reddening_band"]
                if low <= normalized <= high and (tile, "Reddening") not in self.reported_tags:
                    self.reported_tags.add((tile, "Reddening"))
                    reports.append({"kind": "Reddening", "tile_id": tile})
                elif self.p["exact_nova_min"] <= normalized <= self.p["nova_max"] and (tile, "NOVA") not in self.reported_tags:
                    self.reported_tags.add((tile, "NOVA"))
                    reports.append({"kind": "NOVA", "tile_id": tile})
            if self.p["cold_faults"]:
                reports.extend(self._fault_evidence(pending["region"], normalized, now, exact=True))
        prior.append(ratio)
        if pending["region"] not in self.suspect and pending["region"] not in self.fault_regions:
            self.cold_clean.setdefault("+".join(events), []).append(ratio)
        return reports

    def _tag_reports(self, pending: dict, ratio: float, exact: bool = False) -> list:
        """标签上报。普通比值：NOVA 单读、红化要同一天区两次读数都在区间内；精确比值：两者都单读即报。"""
        reports = []
        tile, region = pending["tile_id"], pending["region"]
        p = self.p
        if exact:
            nova = p["exact_nova_min"] <= ratio <= p["nova_max"]
            low, high = p["exact_reddening_band"]
            reddening = low <= ratio <= high
        else:
            nova = p["nova_threshold"] <= ratio <= p["nova_max"]
            low, high = p["reddening_band"]
            reads = self.tile_reads.setdefault(tile, [])
            reads.append(ratio)
            reddening = len(reads) >= 2 and all(low <= r <= high for r in reads[-2:])
        if nova and (tile, "NOVA") not in self.reported_tags:
            self.reported_tags.add((tile, "NOVA"))
            reports.append({"kind": "NOVA", "tile_id": tile})
        if (reddening and region not in self.suspect and region not in self.fault_regions
                and (tile, "Reddening") not in self.reported_tags):
            self.reported_tags.add((tile, "Reddening"))
            reports.append({"kind": "Reddening", "tile_id": tile})
        return reports

    def _evidence(self, pending: dict, ratio: float, now: float, exact: bool = False) -> list:
        reports = []
        region = pending["region"]
        p = self.p
        dip = p["dip_filter"] and self._global_dip(region, ratio)
        if dip and not exact and ratio < p["nova_threshold"]:
            return reports  # 精确读数不受寒潮影响（曝光期间站点有生效事件时不算精确），不需要下沉过滤
        if p["report_tags"]:
            reports.extend(self._tag_reports(pending, ratio, exact))
        reports.extend(self._fault_evidence(region, ratio, now, exact))
        return reports

    def _fault_evidence(self, region: str, ratio: float, now: float, exact: bool = False) -> list:
        reports = []
        p = self.p
        reads = self.region_reads[region]
        reads.append(ratio)
        del reads[:-4]
        is_low = ratio <= (p["exact_fault_threshold"] if exact else p["fault_threshold"])
        if is_low:
            lows = [r for r in reads[-3:] if r <= p["fault_threshold"]]
            self.suspect[region] = min(1.0, max(0.3, sum(lows) / len(lows) / p["mean_jitter"]))
            self.low_since_report[region] = self.low_since_report.get(region, 0) + 1
        elif ratio >= p["fault_clear_threshold"]:
            self.suspect.pop(region, None)

        self.recent_reads.append(ratio)
        del self.recent_reads[:-int(p["fault_window"])]
        if p["report_faults"] and is_low and now >= self.fault_repair_until and not (p["single_fault"] and self.fault_confirmed):
            # 精确读数的低值只可能来自故障：单次即确认
            confirmed = exact or sum(r <= p["fault_threshold"] for r in self.recent_reads) >= 2
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
        """按“已用墙钟 ÷ 巡天进度”推算整场耗时，超出预算比例就降级规划，避免时钟耗尽。

        降级顺序：超过 85% 直接关闭规划；否则先把短于 1 小时的重规划间隔退回 1 小时（超过 budget_frequent_at），
        仍然太慢再改为每夜规划一次（超过 60%）。规划开销前重后轻，每次降级后改按“降级以来的速度”外推。
        第一次检查（进度 5%）只做关闭规划这道保护：开局的一次性开销（几何预计算、首次 300 次迭代）会让外推偏高
        一倍左右，此后从这里起算。
        """
        if self.clock_started is None:
            self.clock_started = time.monotonic()
            return
        if self.survey_span is None:
            return
        progress = (now - self.survey_span[0]) / (self.survey_span[1] - self.survey_span[0])
        mark_elapsed, mark_progress = self.budget_mark
        if progress - mark_progress < 0.05:
            return
        elapsed = time.monotonic() - self.clock_started
        projected = mark_elapsed + (elapsed - mark_elapsed) / (progress - mark_progress) * (1.0 - mark_progress)
        if not self.budget_started:
            self.budget_started = True
            self.budget_mark = (elapsed, progress)
            if not (self.planning and projected > self.p["budget_disable_at"] * self.wallclock):
                return
        if self.planning and projected > self.p["budget_disable_at"] * self.wallclock:
            self._diagnostic("planning_state", {"enabled": False, "reason": "wallclock_budget",
                                                "projected_seconds": projected, "mode": "off"})
            self.planning = False
        elif (self.frequent and float(self.p["plan_every_seconds"]) < 3600.0
              and projected > self.p["budget_frequent_at"] * self.wallclock):
            self._diagnostic("planning_state", {"enabled": True, "reason": "wallclock_budget",
                                                "projected_seconds": projected, "mode": "hourly"})
            self.frequent = False
        elif self.hourly and projected > self.p["budget_reduce_at"] * self.wallclock:
            self._diagnostic("planning_state", {"enabled": True, "reason": "wallclock_budget",
                                                "projected_seconds": projected, "mode": "nightly"})
            self.hourly = False
        else:
            return
        self.budget_mark = (elapsed, progress)

    def _plan(self, snapshot: dict, now: float) -> None:
        self._budget(now)
        if not self.planning:
            return
        try:
            if self.planner is None:
                from planner import Planner
                self.planner = Planner(self.initial, self.p)
                self.planner.on_event = self._diagnostic
                if self.p["plan_events"] == "truth":
                    self._pending_events = (self._truth_events(), "truth", math.inf)
            if self._pending_events is not None:
                events, mode, until = self._pending_events
                self._pending_events = None
                kinds = self.p["plan_events_kinds"]  # 本地试验：只让规划器看闭站类或质量类事件
                events = [e for e in events if kinds == "all" or (kinds == "close") == e.force_close]
                self.planner.set_events(events, mode, until)
            self.planner.observe_weather(snapshot)
            night = (snapshot.get("cursor") or {}).get("night_id")
            interval = self.advised_every or float(self.p["plan_every_seconds"])
            every = interval if self.frequent else max(3600.0, interval)
            hourly_due = self.hourly and now - self.planned_at >= every
            if night != self.planned_night or hourly_due:
                self.planned_night, self.planned_at = night, now
                try:
                    self._replan(snapshot, now)
                except Exception as exc:  # 天气事件出错时只丢掉事件，规划照常
                    if self.planner.events is None:
                        raise
                    self._diagnostic("policy_error", {"stage": "planning_events", "reason": type(exc).__name__,
                                                      "fallback": "planning_without_events"})
                    self.planner.events = None
                    self._replan(snapshot, now)
        except Exception as exc:  # noqa: BLE001  规划失败不能拖垮决策：退回当晚时机因子
            self._diagnostic("policy_error", {"stage": "planning", "reason": type(exc).__name__,
                                              "fallback": "night_timing"})
            self.planning = False
            self.planner = None

    def _replan(self, snapshot: dict, now: float) -> None:
        requests = set(self._request_options(snapshot))
        exclude = requests if self.p["plan_required"] else self.required | requests
        counts = dict(zip(self.regions, self.counts))
        coverage = (self.coverage_weight, self.base_total, counts) if self.p["plan_coverage"] else None
        faults = self._fault_map(now) if self.p["plan_fault_aware"] else None
        repeats = self._repeat_candidates() if self.p["plan_repeats"] and self.mechanics else None
        if self.p["plan_required"]:
            self.planner.replan(now, self.completed, exclude, coverage, required=self.required,
                                terminal=self.required_miss, faults=faults, repeats=repeats)
        else:
            self.planner.replan(now, self.completed, exclude, coverage, faults=faults, repeats=repeats)

    def _repeat_candidates(self) -> dict:
        """复拍候选：{天区: (已入账的实现分, 标签乘数)}。已上报的标签按其乘数调整潜力（红化 0.8、新星 1.5）。"""
        tags = {}
        for tile, kind in self.reported_tags:
            tags[tile] = 0.8 if kind == "Reddening" else 1.5
        return {tile: (score, tags.get(tile, 1.0)) for tile, score in self.banked.items() if tile in self.completed}

    def _truth_events(self) -> list:
        """本地上限试验：在场景根目录里按 scenario_id（即 SAC_SCENARIO）找到本场景，读真实事件。找不到就不用事件。"""
        wanted = os.environ.get("SAC_SCENARIO", "")
        for config in sorted(Path(str(self.p["plan_events_truth_root"])).glob("*/config/scenario_config.json")):
            if json.loads(config.read_text(encoding="utf-8")).get("scenario_id") == wanted:
                return events_mod.load_truth(config.parent.parent / "outputs" / "reference" / "weather_events.csv")
        self._diagnostic("warning", {"stage": "truth_experiment", "reason": "scenario_missing", "scenario": wanted})
        return []

    def _fault_map(self, now: float) -> dict:
        """规划用的故障：已确认的分区用公布的乘数直到修复；自己检测到、尚未确认的按估计乘数计到两天半之后。"""
        faults = {region: (m, now + 2.5 * 86400.0) for region, m in self.suspect.items()}
        if self.fault_regions and now < self.fault_repair_until:
            faults.update({region: (self.fault_multiplier, self.fault_repair_until) for region in self.fault_regions})
        return faults

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
            self.fault_confirmed = True
        elif status.get("status") == "normal":
            self.fault_pending_since = None
            self.fault_misreports += 1
            self.suspect.clear()
            self.low_since_report = {}
            self.recent_reads = []

    def _segment_geometry(self, c: dict, now: float, snapshot: dict):
        """按评分器的方式把这次曝光按时隙边界切段：[(时长占比, airmass, 月光因子)]，每段取中点几何。没有几何模块时返回 None。"""
        planner = self.planner if self.planning else None
        tile = planner.tiles.get(str(c["tile_id"])) if planner is not None else None
        if tile is None:
            return None
        exposure = float(c["nominal_exptime_seconds"])
        slot = float(planner.slot)
        offset = float((snapshot.get("cursor") or {}).get("slot_offset_seconds") or 0.0)
        start, remaining, slot_end, segments = now, exposure, now - offset + slot, []
        while remaining > 1e-6:
            seconds = min(remaining, slot_end - start)
            _alt, am, lunar = planner.site.moment(start + seconds / 2.0).quality(tile["geo"])
            segments.append((seconds / exposure, am, lunar))
            remaining -= seconds
            start += seconds
            slot_end += slot
        return segments

    def _segment_quality(self, weather: dict, am: float, lunar: float) -> float:
        w0 = float(weather["transparency"]) * float(weather["sky_quality"]) / float(weather["seeing_arcsec"])
        return min(w0 / am ** self.airmass_exp, self.max_quality) * lunar

    def _segments(self, c: dict, weather: dict, now: float, snapshot: dict):
        """[(时长占比, 不含效率的质量)]；天气用提交时的有效天气。没有几何模块时返回 None。"""
        geometry = self._segment_geometry(c, now, snapshot)
        if geometry is None:
            return None
        return [(f, self._segment_quality(weather, am, lunar)) for f, am, lunar in geometry]

    def _posterior_expected(self, pending: dict, snapshot: dict, now: float, allow_site_events: bool = False):
        """曝光结束后的事后期望：每段用它所在时隙的真实有效天气（第二段取自这次快照里该天区的有效天气）。

        期望里只缺仪器侧（效率抖动 × 故障 × 标签），所以比值落在：普通 [0.90, 1.00]、红化 [0.72, 0.80]、
        故障 ≤ 0.70、新星 [1.35, 1.50]。跨三个时隙、正好结束在时隙边界、或曝光期间站点有生效事件（只可能是寒潮，
        效率不进快照）时拿不到精确值，返回 None。
        """
        geometry = pending.get("geometry")
        site_events = pending["site_events"] or (snapshot.get("current_site_weather") or {}).get("active_event_ids")
        if not geometry or (site_events and not allow_site_events):
            return None
        weathers = [pending["weather"]]
        if len(geometry) > 1:
            slot_start = now - float((snapshot.get("cursor") or {}).get("slot_offset_seconds") or 0.0)
            if len(geometry) > 2 or abs(slot_start - (pending["slot_start"] + pending["slot_seconds"])) > 1.0:
                return None
            c = next((c for c in snapshot.get("candidate_tiles") or [] if str(c["tile_id"]) == pending["tile_id"]), None)
            if c is None or not c["effective_weather"].get("is_observable"):
                return None
            weathers.append(c["effective_weather"])
        program = pending["program"]
        total = 0.0
        for (f, am, lunar), weather in zip(geometry, weathers):
            q = self._segment_quality(weather, am, lunar)
            total += f * q * (1.0 + (self.bonus[program] if self._band(q) == program else 0.0))
        return pending["value"] * total

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

    def _repeat_gain(self, tile: str, realized: float, base: float, evenness: float) -> tuple[float, float]:
        banked = self.banked.get(tile, realized)
        if not self.p["repeat_exact_coverage"]:
            return max(0.0, realized - banked), self.coverage_weight * max(
                0.0, base - self.banked_base.get(tile, base)) * evenness
        if realized <= banked:
            return 0.0, 0.0
        return realized - banked, self.coverage_weight * (base - self.banked_base.get(tile, base)) * evenness

    def _choose(self, snapshot: dict, now: float, v3: bool, deadline=None, protected=frozenset()):
        self._model_candidates = []
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
            base = float(c["tile_science_value"]) * quality * self.p["mean_jitter"] * multiplier * self.cold_factor
            realized = base * (1.0 + self.bonus[band])
            if done:
                science, coverage = self._repeat_gain(tile, realized, base, evenness)
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
            self._model_candidates = [entry[:5] for entry in entries[:2]]
            return entries[0][:5]
        refined_entries = []
        for key, c, band, weather, quality, context in entries[:top]:
            refined = self._refine(c, weather, now, snapshot, context, key[3], counts, total_done, sum_sq, evenness)
            if refined is None:
                refined = (key, band)
            refined_entries.append((refined[0], c, refined[1], weather, quality))
        refined_entries.sort(key=lambda entry: entry[0], reverse=True)
        if int(self.p["rollout_k"]) > 1 and len(refined_entries) > 1:
            best = self._rollout(refined_entries[: int(self.p["rollout_k"])], entries, now)
        else:
            best = refined_entries[0]
        self._model_candidates = [best] + [entry for entry in refined_entries
                                           if entry[0][2:4] != best[0][2:4]][:1]
        return best

    def _model_context(self, snapshot: dict, *, deterministic_choice: str | None = None,
                       candidates: list[dict] | None = None) -> dict:
        """Build a bounded, explicit snapshot for the optional model call.

        The controller still computes the operation before this method is
        called. The model sees the live cursor, weather, progress, requests,
        and the candidate facts used by that calculation, with no hidden
        mutable state or credentials.
        """
        cursor = snapshot.get("cursor") or {}
        progress = snapshot.get("progress") or {}
        tiles = list(snapshot.get("candidate_tiles") or [])
        requests = list(snapshot.get("active_requests") or [])
        night_start = snapshot.get("night_start") or self._model_night_start
        weekly = snapshot.get("weekly") or self._model_weekly
        selected_tiles = {str(item["tile_id"]) for item in candidates or [] if item.get("tile_id")}

        def relevant_sample(items: list, limit: int) -> list:
            relevant = [item for item in items if isinstance(item, dict) and
                        str(item.get("tile_id")) in selected_tiles]
            others = [item for item in items if not isinstance(item, dict) or
                      str(item.get("tile_id")) not in selected_tiles]
            return (relevant + others)[:limit]

        def bounded_publication(value: object, limits: dict[str, int]) -> dict:
            if not isinstance(value, dict):
                return {}
            result = {key: item for key, item in value.items() if key not in limits}
            for key, limit in limits.items():
                items = value.get(key)
                if isinstance(items, list):
                    result[key] = relevant_sample(items, limit) if key == "tile_windows" else items[:limit]
                    result[f"{key}_omitted"] = max(0, len(items) - limit)
                    if key == "tile_windows":
                        by_night_region: dict[str, dict[str, int]] = {}
                        for item in items:
                            if isinstance(item, dict):
                                night = str(item.get("night_id", "unknown"))
                                region = str(item.get("region_id", "unknown"))
                                group = by_night_region.setdefault(night, {})
                                group[region] = group.get(region, 0) + 1
                        result["tile_window_counts_by_night_region"] = by_night_region
            return result

        catalog = self.initial.get("tile_catalog") or {}
        context = {
            "schema": "gosim26.observer.live_state.v1",
            "scoring_contract": self.initial.get("scoring_contract") or {},
            "site": self.initial.get("site") or {},
            "calendar": self.initial.get("calendar") or {},
            "catalog_summary": {"tile_count": len(catalog.get("tiles") or []),
                                "region_ids": catalog.get("region_ids") or [],
                                "required_tile_ids": catalog.get("required_tile_ids") or []},
            "snapshot_schema": snapshot.get("schema_version"),
            "weekly": bounded_publication(weekly, {
                "weather_forecast": 64,
                "tile_windows": 64,
                "observation_requests": 32,
            }),
            "night_start": bounded_publication(night_start, {"tile_windows": 64}),
            "progress": progress,
            "active_requests": requests[:32],
            "active_requests_omitted": max(0, len(requests) - 32),
            "candidate_tiles": relevant_sample(tiles, 64),
            "candidate_tiles_omitted": max(0, len(tiles) - 64),
            "tile_last_finished": snapshot.get("tile_last_finished"),
            "fault_status": snapshot.get("fault_status"),
            "current_site_weather": snapshot.get("current_site_weather") or {},
            "night_id": cursor.get("night_id"),
            "slot_id": cursor.get("slot_id"),
            "timestamp_utc": cursor.get("timestamp_utc"),
            "slot_offset_seconds": cursor.get("slot_offset_seconds"),
            "decision_sequence": snapshot.get("decision_sequence"),
            "deterministic_choice": deterministic_choice,
            "deterministic_candidates": candidates or [],
        }
        return context

    def _choose_weekly_plan(self, snapshot: dict) -> None:
        weekly = snapshot.get("weekly")
        if not isinstance(weekly, dict) or weekly.get("weather_forecast") is None:
            return
        publication = weekly.get("issued_at_utc") or (snapshot.get("cursor") or {}).get("night_id")
        if publication == self.advised_week:
            return
        self.advised_week = publication
        self.p["plan_horizon_nights"] = self.base_plan_horizon
        self.advised_every = self.base_plan_every
        self.weekly_plan_source = "deterministic"
        alternatives = [
            {"id": "full_horizon", "horizon_nights": self.base_plan_horizon,
             "replan_interval_seconds": self.base_plan_every},
            {"id": "week_focus", "horizon_nights": min(7, self.base_plan_horizon),
             "replan_interval_seconds": max(3600.0, self.base_plan_every)},
        ]
        selected = alternatives[0]
        elapsed = max(0.0, time.monotonic() - self.clock_started) if self.clock_started is not None else 0.0
        pressure = elapsed / max(1.0, self.wallclock)
        preferred = max(0.90, min(0.995, 0.995 - pressure * 0.08))
        if self.planning and alternatives[0] != alternatives[1]:
            options = [
                {**alternatives[0], "prior_probability": round(preferred, 4),
                 "tradeoff": "more future nights considered; more replanning CPU"},
                {**alternatives[1], "prior_probability": round(1.0 - preferred, 4),
                 "tradeoff": "fewer future nights considered; less replanning CPU"},
            ]
            context = self._model_context(snapshot, deterministic_choice="full_horizon", candidates=options)
            context.update({"elapsed_wall_seconds": round(elapsed, 1),
                            "wallclock_budget_seconds": self.wallclock,
                            "completed_tiles": len(self.completed),
                            "forecast_events": len(self.forecasts),
                            "prior_basis": "bounded heuristic from observed wallclock pressure, not calibrated win rates"})
            choice = self.advisor.choose("weekly_planning", options, context)
            if choice == "week_focus":
                selected = alternatives[1]
                self.weekly_plan_source = "model"
            elif choice == "full_horizon":
                self.weekly_plan_source = "model"
        self.p["plan_horizon_nights"] = selected["horizon_nights"]
        self.advised_every = selected["replan_interval_seconds"]
        self.weekly_plan = {**selected, "publication_utc": publication,
                            "forecast_events": len(self.forecasts)}
        if self.on_plan is not None:
            try:
                self.on_plan({**self.weekly_plan, "source": self.weekly_plan_source,
                              "decision_sequence": snapshot.get("decision_sequence")})
            except Exception as exc:  # noqa: BLE001
                self._diagnostic("warning", {"stage": "weekly_plan_diagnostics", "reason": type(exc).__name__})

    def _select_action_with_model(self, snapshot: dict, best):
        night = (snapshot.get("cursor") or {}).get("night_id")
        if night == self.action_advised_night or not self.advisor.enabled:
            return best, "deterministic"
        candidates = [entry for entry in self._model_candidates if entry[0][1] > 0 and entry[0][0] > 0]
        if len(candidates) < 2:
            return best, "deterministic"
        first, second = candidates[:2]
        margin = abs(first[0][0] - second[0][0]) / max(abs(first[0][0]), abs(second[0][0]), 1e-9)
        self.action_advised_night = night
        preferred = min(0.999, 0.99 + 0.009 * margin)
        options = []
        for index, entry in enumerate((first, second)):
            key, tile, band, _weather, _quality = entry
            options.append({"id": f"C{index}", "tile_id": key[2], "program": band,
                            "request_id": key[3], "expected_gain": round(key[1], 2),
                            "priority_per_hour": round(key[0] * 3600.0, 2),
                            "prior_probability": round(preferred if index == 0 else 1.0 - preferred, 4),
                            "eligible": index == 0 or margin <= 0.05,
                            "seconds": tile["nominal_exptime_seconds"]})
        context = self._model_context(snapshot, deterministic_choice="C0", candidates=options)
        context.update({"completed_tiles": len(self.completed),
                        "active_request_count": len(snapshot.get("active_requests") or []),
                        "deterministic_choice_reason": "highest computed marginal gain",
                        "relative_priority_gap": round(margin, 6),
                        "eligible_choices": ["C0", "C1"] if margin <= 0.05 else ["C0"]})
        choice = self.advisor.choose("action_decision", options, context)
        if choice == "C1" and margin > 0.05:
            return best, "deterministic"
        if choice in {"C0", "C1"}:
            return candidates[int(choice[1])], "model"
        return best, "deterministic"

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
                         float(c["tile_science_value"]) * self.p["mean_jitter"] * multiplier * self.cold_factor, w0,
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
            if self.p["closure_risk"] or self.p["closure_risk_forecast"]:
                segments = self._segment_geometry(finalist[1], now, self._snapshot) or []
                durations = [f * first[2] for f, _am, _lunar in segments]
                risks = self._forecast_risks(now, durations) if not self.p["closure_risk"] else None
                if len(segments) > 1 and (self.p["closure_risk"] or risks is not None):
                    reward, spent = self._risk_adjust(reward, first[2], durations, risks)
                    clock = now + spent
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
        value = float(c["tile_science_value"]) * self.p["mean_jitter"] * multiplier * self.cold_factor
        program = max(("DARK", "BRIGHT", "BACKUP"), key=lambda p: (self._segment_score(segments, p), p))
        realized = value * self._segment_score(segments, program)
        base = value * sum(f * q for f, q in segments)
        tile = str(c["tile_id"])
        if done:
            science, coverage = self._repeat_gain(tile, realized, base, evenness)
        else:
            science = realized
            coverage = 0.0
            if self.coverage_weight > 0:
                x = counts[self.region_index[region]]
                after = (total_done + 1) ** 2 / (len(counts) * (sum_sq + 2 * x + 1))
                coverage = self.coverage_weight * ((self.base_total + base) * after - self.base_total * evenness)
        gain = science + coverage + terminal + request_value
        if len(segments) > 1 and (self.p["closure_risk"] or self.p["closure_risk_forecast"]):
            durations = [f * exposure for f, _q in segments]
            risks = self._forecast_risks(now, durations) if not self.p["closure_risk"] else None
            if self.p["closure_risk"] or risks is not None:
                net, expected_time = self._risk_adjust(gain - opportunity, exposure, durations, risks)
                return (net / expected_time * timing, gain, tile, request_id), program
        return ((gain - opportunity) / exposure * timing, gain, tile, request_id), program

    # ------------------------------------------------------------------ 决策入口

    def decide(self, snapshot: dict) -> dict:
        now = epoch(snapshot["cursor"]["timestamp_utc"])
        schema = snapshot.get("schema_version")
        v3 = schema == "decision-snapshot-v3" if schema in ("decision-snapshot-v2", "decision-snapshot-v3") else self.mechanics
        self.decisions += 1
        self._sync_progress(snapshot)
        self._snapshot = snapshot
        if isinstance(snapshot.get("weekly"), dict):
            self._model_weekly = snapshot["weekly"]
        if isinstance(snapshot.get("night_start"), dict):
            self._model_night_start = snapshot["night_start"]
        if self.p["closure_risk"] or self.p["closure_risk_forecast"]:
            self._track_closures(snapshot)
        self._load_windows(snapshot, now)
        weekly = snapshot.get("weekly")
        if isinstance(weekly, dict) and weekly.get("weather_forecast") is not None:
            self.forecasts = list(weekly["weather_forecast"])
            if self.p["plan_events"] == "forecast":
                self._pending_events = (self._forecast_events(), "forecast",
                                        epoch(weekly.get("issued_at_utc") or snapshot["cursor"]["timestamp_utc"]) + 7 * 86400.0)
            elif self.p["plan_events"] == "truth_weekly":
                # 诊断：只给规划器当期发布本该覆盖的真实事件（发布后 7 天内、时间精确），区分预报噪声与"每周一次"的结构限制
                issued = epoch(weekly.get("issued_at_utc") or snapshot["cursor"]["timestamp_utc"])
                until = issued + 7 * 86400.0
                self._pending_events = ([e for e in self._truth_events() if e.end > issued and e.start < until], "truth", until)
        self._choose_weekly_plan(snapshot)
        self._plan(snapshot, now)
        reports = []
        if v3:
            self._consume_fault_status(snapshot, now)
            reports = self._consume_feedback(snapshot, now)

        self.cold_factor = self._cold_factor(snapshot, now) if self.p["cold_online"] else 1.0
        deadline, protected = self._required_deadline(now)
        best = self._choose(snapshot, now, v3, deadline, protected)
        if best is None and deadline is not None:
            cursor = snapshot["cursor"]
            slot = float((snapshot.get("current_site_weather") or {}).get("duration_seconds") or 900)
            next_boundary = now + slot - float(cursor.get("slot_offset_seconds") or 0) % slot
            if next_boundary > deadline:
                best = self._choose(snapshot, now, v3)  # 等待也赶不上这个窗口，保护无意义
        source = "deterministic"
        if best is not None:
            best, source = self._select_action_with_model(snapshot, best)
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
                            "expected": expected, "simple": simple, "clean": clean, "program": band,
                            "value": float(c["tile_science_value"]), "weather": weather,
                            "geometry": self._segment_geometry(c, now, snapshot) if self.p["segment_expectation"] else None,
                            "slot_start": now - float((snapshot.get("cursor") or {}).get("slot_offset_seconds") or 0.0),
                            "slot_seconds": float(site.get("duration_seconds") or 900.0),
                            "site_events": bool(site.get("active_event_ids")),
                            "site_event_ids": [str(e) for e in site.get("active_event_ids") or []]}
            decision = {"action": "observe", "tile_id": tile, "program": band, "request_id": request_id,
                        "reason": f"marginal gain {gain:.1f} ({index * 3600:.1f}/h)"}
        decision["decision_source"] = source
        if reports:
            decision["reports"] = reports
        return decision
