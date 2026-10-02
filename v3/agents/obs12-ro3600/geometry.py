"""几何与观测日历：与官方环境（tile_geometry_simulator / observing_calendar）相同的低精度公式，只用标准库。

快照只给出当前时刻的几何；规划需要任意未来时刻的 airmass 与月光因子，以及整场每一夜的观测时段。
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

_ZENITH_RAW = 1.0 / (1.0 + 0.50572 * 96.07995 ** -1.6364)


def julian_days(epoch: float) -> float:
    """距 J2000.0 的日数。"""
    return epoch / 86400.0 + 2440587.5 - 2451545.0


def lst_deg(epoch: float, longitude_deg: float) -> float:
    return (280.46061837 + 360.98564736629 * julian_days(epoch) + longitude_deg) % 360.0


def sun_radec(epoch: float) -> tuple[float, float]:
    days = julian_days(epoch)
    mean_longitude = (280.460 + 0.9856474 * days) % 360.0
    mean_anomaly = math.radians((357.528 + 0.9856003 * days) % 360.0)
    longitude = math.radians((mean_longitude + 1.915 * math.sin(mean_anomaly) + 0.020 * math.sin(2 * mean_anomaly)) % 360.0)
    obliquity = math.radians(23.439 - 0.0000004 * days)
    ra = math.degrees(math.atan2(math.cos(obliquity) * math.sin(longitude), math.cos(longitude))) % 360.0
    return ra, math.degrees(math.asin(math.sin(obliquity) * math.sin(longitude)))


def moon_radec(epoch: float) -> tuple[float, float]:
    days = julian_days(epoch)
    mean_longitude = math.radians((218.316 + 13.176396 * days) % 360.0)
    mean_anomaly = math.radians((134.963 + 13.064993 * days) % 360.0)
    argument_latitude = math.radians((93.272 + 13.229350 * days) % 360.0)
    longitude = mean_longitude + math.radians(6.289) * math.sin(mean_anomaly)
    latitude = math.radians(5.128) * math.sin(argument_latitude)
    obliquity = math.radians(23.439 - 0.0000004 * days)
    x = math.cos(longitude) * math.cos(latitude)
    y = math.sin(longitude) * math.cos(latitude) * math.cos(obliquity) - math.sin(latitude) * math.sin(obliquity)
    z = math.sin(longitude) * math.cos(latitude) * math.sin(obliquity) + math.sin(latitude) * math.cos(obliquity)
    return math.degrees(math.atan2(y, x)) % 360.0, math.degrees(math.asin(z))


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cosine = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def airmass(altitude_deg: float) -> float:
    """Kasten–Young，按天顶归一化（天顶为 1.0）。"""
    if altitude_deg <= 0.0:
        return float("inf")
    zenith = 90.0 - altitude_deg
    return 1.0 / (math.cos(math.radians(zenith)) + 0.50572 * (96.07995 - zenith) ** -1.6364) / _ZENITH_RAW


class Site:
    def __init__(self, site: dict, lunar_model: dict) -> None:
        self.latitude = float(site["latitude_deg"])
        self.longitude = float(site["longitude_deg"])
        self.utc_offset = float(site.get("utc_offset_hours", 0.0))
        self.sun_limit = float(site.get("sun_altitude_limit_deg", -12.0))
        self.sin_lat = math.sin(math.radians(self.latitude))
        self.cos_lat = math.cos(math.radians(self.latitude))
        self.decay = float(lunar_model["angular_decay_scale_deg"])
        self.alt_exp = float(lunar_model["altitude_exponent"])
        self.max_penalty = float(lunar_model["maximum_penalty"])

    def altitude_deg(self, ra: float, dec: float, epoch: float) -> float:
        hour_angle = math.radians((lst_deg(epoch, self.longitude) - ra + 180.0) % 360.0 - 180.0)
        sin_alt = self.sin_lat * math.sin(math.radians(dec)) + self.cos_lat * math.cos(math.radians(dec)) * math.cos(hour_angle)
        return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))

    def moment(self, epoch: float) -> Moment:
        return Moment(self, epoch)

    # ------------------------------------------------------------------ 观测日历

    def _crossing(self, left: float, right: float, left_below: bool) -> float:
        while right - left > 1.0:
            middle = (left + right) / 2.0
            ra, dec = sun_radec(middle)
            if (self.altitude_deg(ra, dec, middle) <= self.sun_limit) == left_below:
                left = middle
            else:
                right = middle
        return float(int(right))  # 与官方一样截去不足一秒的部分

    def night_bounds(self, observing_date: date, slot_seconds: int, step: int = 300) -> tuple[float, float]:
        """某观测日的 (观测开始, 观测结束)：太阳高度穿过阈值的时刻按时隙网格向内取整。"""
        zone = timezone(timedelta(hours=self.utc_offset))
        start = datetime.combine(observing_date, time(hour=12), tzinfo=zone).timestamp()
        previous = start
        ra, dec = sun_radec(previous)
        previous_below = self.altitude_deg(ra, dec, previous) <= self.sun_limit
        dusk = dawn = None
        cursor = previous + step
        while cursor <= start + 86400:
            ra, dec = sun_radec(cursor)
            below = self.altitude_deg(ra, dec, cursor) <= self.sun_limit
            if below != previous_below:
                crossing = self._crossing(previous, cursor, previous_below)
                if below and dusk is None:
                    dusk = crossing
                elif not below and dusk is not None and dawn is None:
                    dawn = crossing
            previous, previous_below = cursor, below
            cursor += step
        if dusk is None or dawn is None:
            raise ValueError(f"no complete night on {observing_date}")
        return math.ceil(dusk / slot_seconds) * slot_seconds, math.floor(dawn / slot_seconds) * slot_seconds


class Moment:
    """某一时刻与天区无关的量，供批量计算多块天区复用。"""

    __slots__ = ("cos_lst", "moon_cos_dec", "moon_ra", "moon_sin_dec", "moon_weight", "sin_lst", "site")

    def __init__(self, site: Site, epoch: float) -> None:
        self.site = site
        lst = math.radians(lst_deg(epoch, site.longitude))
        self.cos_lst, self.sin_lst = math.cos(lst), math.sin(lst)
        sun_ra, sun_dec = sun_radec(epoch)
        moon_ra, moon_dec = moon_radec(epoch)
        illumination = (1.0 - math.cos(math.radians(separation_deg(sun_ra, sun_dec, moon_ra, moon_dec)))) / 2.0
        moon_alt = site.altitude_deg(moon_ra, moon_dec, epoch)
        self.moon_weight = site.max_penalty * illumination * math.sin(math.radians(max(0.0, moon_alt))) ** site.alt_exp
        self.moon_ra = math.radians(moon_ra)
        self.moon_sin_dec = math.sin(math.radians(moon_dec))
        self.moon_cos_dec = math.cos(math.radians(moon_dec))

    def quality(self, tile: TileGeometry) -> tuple[float, float, float]:
        """(高度°, airmass, 月光因子)。"""
        site = self.site
        cos_ha = self.cos_lst * tile.cos_ra + self.sin_lst * tile.sin_ra  # cos(LST − RA)
        sin_alt = site.sin_lat * tile.sin_dec + site.cos_lat * tile.cos_dec * cos_ha
        altitude = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
        cos_sep = tile.sin_dec * self.moon_sin_dec + tile.cos_dec * self.moon_cos_dec * math.cos(tile.ra - self.moon_ra)
        separation = math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))
        lunar = 1.0 - self.moon_weight * math.exp(-separation / site.decay)
        return altitude, airmass(altitude), max(0.0, min(1.0, lunar))


class TileGeometry:
    __slots__ = ("cos_dec", "cos_ra", "ra", "sin_dec", "sin_ra")

    def __init__(self, ra_deg: float, dec_deg: float) -> None:
        self.ra = math.radians(ra_deg)
        self.cos_ra, self.sin_ra = math.cos(self.ra), math.sin(self.ra)
        self.sin_dec, self.cos_dec = math.sin(math.radians(dec_deg)), math.cos(math.radians(dec_deg))
