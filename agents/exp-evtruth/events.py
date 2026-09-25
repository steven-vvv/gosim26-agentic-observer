"""天气事件对规划的影响：把每周预报（或本地上限试验用的真实事件）换算成两样东西——
每个时刻的站点开放率（全站强制闭站的降雨、龙卷风），以及每个 (天区, 时刻) 的期望质量乘数（云、烟霾、寒潮；
火箭发射按方向闭站）。

预报的起止时间误差按正态处理：时隙中点 t 所在的时隙与事件重叠的概率
≈ probability · Φ((t + 半时隙 − 开始)/u) · Φ((结束 − t + 半时隙)/u)。
46 个本地场景上，预报换算出的全站闭站概率在高概率段偏低（p ≥ 0.6 时实际闭站更多），开放率按标定表换算。
"""
from __future__ import annotations

import bisect
import csv
import json
import math
from datetime import datetime

from geometry import separation_deg

# 公开的决赛 weather_config.json：各类事件的 (视宁度, 透过率, 天光, 仪器效率) 配置乘数与是否强制闭站；
# 生成器里真实乘数 = 1 + severity·(配置乘数 − 1)，预报给出的 severity 是真实值加噪声
EVENT_CONFIG = {
    "rainy": (1.8, 0.25, 0.35, 1.0, True),
    "cloudy": (1.25, 0.55, 0.65, 1.0, False),
    "smoggy": (1.15, 0.68, 0.72, 1.0, False),
    "rocket_launch": (1.0, 1.0, 1.0, 1.0, True),
    "cold_wave": (1.05, 0.96, 0.98, 0.68, False),
    "tornado": (2.0, 0.15, 0.25, 0.8, True),
}
# 预报换算的全站闭站概率 p → 实际开放率（46 个本地场景、每周预报的有效期内；p ≈ 0 即预报没有提到的时段）
OPEN_BY_P = ((0.0, 0.931), (0.1, 0.931), (0.3, 0.715), (0.5, 0.468), (0.7, 0.178), (0.9, 0.042), (1.0, 0.0))
TRUE_BACKGROUND_OPEN = 0.957  # 真实事件以外时隙的开放率（背景关闭）；只用于上限试验
HALF_SLOT = 450.0


def _epoch(value) -> float:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def open_from_probability(p: float) -> float:
    xs = [x for x, _y in OPEN_BY_P]
    k = min(len(xs) - 2, max(0, bisect.bisect_right(xs, p) - 1))
    (x0, y0), (x1, y1) = OPEN_BY_P[k], OPEN_BY_P[k + 1]
    return y0 + (y1 - y0) * (min(max(p, x0), x1) - x0) / (x1 - x0)


class Event:
    __slots__ = ("end", "force_close", "me", "mq", "payload", "prob", "scope", "start", "unc")

    def __init__(self, start, end, unc, prob, scope, payload, force_close, mq, me) -> None:
        self.start, self.end, self.unc, self.prob = float(start), float(end), float(unc), float(prob)
        self.scope, self.payload, self.force_close = str(scope), payload or {}, bool(force_close)
        self.mq, self.me = float(mq), float(me)  # 质量乘数 T·S/seeing（决定档位）与仪器效率乘数

    def active(self, t: float) -> float:
        """时隙中点 t 所在时隙处在事件内的概率。"""
        if self.unc <= 0.0:
            return self.prob if self.start < t + HALF_SLOT and self.end > t - HALF_SLOT else 0.0
        return self.prob * _phi((t + HALF_SLOT - self.start) / self.unc) * _phi((self.end - t + HALF_SLOT) / self.unc)

    def reach(self) -> tuple[float, float]:
        """可能起作用的时间范围（起止各放宽 3 个标准差）。"""
        pad = 3.0 * self.unc + HALF_SLOT
        return self.start - pad, self.end + pad

    def covers(self, tile: dict, site, t: float) -> bool:
        """天区在时刻 t 是否处在事件的空间范围内（判定方式与官方 WeatherSimulator._applies 相同）。"""
        p = self.payload
        if self.scope == "ALL":
            return True
        if self.scope == "REGION_SET":
            return tile["region"] in p.get("region_ids", ())
        if self.scope == "TILE_SET":
            return tile["id"] in p.get("tile_ids", ())
        if self.scope == "SKY_CAP_ICRS":
            return separation_deg(tile["ra"], tile["dec"], float(p["ra_deg"]), float(p["dec_deg"])) <= float(p["radius_deg"])
        if self.scope == "HORIZON_SECTOR":
            altitude, azimuth = site.alt_az(tile["ra"], tile["dec"], t)
            if not float(p["min_altitude_deg"]) <= altitude <= float(p["max_altitude_deg"]):
                return False
            a0, a1 = float(p["azimuth_start_deg"]), float(p["azimuth_end_deg"])
            return a0 <= azimuth <= a1 if a0 <= a1 else azimuth >= a0 or azimuth <= a1
        return False


def _payload(value) -> dict:
    if isinstance(value, str):
        return json.loads(value) if value.strip() else {}
    return dict(value or {})


def from_forecast(item: dict):
    """每周发布里的一条预报 → Event；不认识的事件类型返回 None。"""
    config = EVENT_CONFIG.get(str(item.get("condition")))
    if config is None:
        return None
    severity = min(1.0, max(0.0, float(item.get("severity") or 0.0)))
    seeing, transparency, sky, efficiency = (1.0 + severity * (c - 1.0) for c in config[:4])
    unc = 0.5 * (float(item.get("start_uncertainty_seconds") or 0.0) + float(item.get("end_uncertainty_seconds") or 0.0))
    return Event(_epoch(item["predicted_start_utc"]), _epoch(item["predicted_end_utc"]), max(unc, 1.0),
                 float(item.get("probability") or 0.0), item.get("spatial_scope_type", "ALL"),
                 _payload(item.get("spatial_scope_payload")), config[4], transparency * sky / seeing, efficiency)


def load_truth(path) -> list:
    """本地上限试验：场景目录里的真实事件（weather_events.csv），概率 1、没有时间误差。仪器故障不算天气事件。"""
    events = []
    with open(path, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["condition"] == "instrument_fault":
                continue
            mq = float(row["transparency_multiplier"]) * float(row["sky_quality_multiplier"]) / float(row["seeing_multiplier"])
            events.append(Event(_epoch(row["actual_start_utc"]), _epoch(row["actual_end_utc"]), 0.0, 1.0, row["spatial_scope_type"],
                                _payload(row["spatial_scope_payload"]), row["force_close"] == "true", mq,
                                float(row["instrument_efficiency_multiplier"])))
    return events
