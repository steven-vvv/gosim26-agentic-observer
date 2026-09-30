"""Online weather and instrument evidence from public, possibly censored scores."""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from .geometry import altaz, horizontal_vectors, sidereal
from .terrain import TerrainModel

DIRECTIONS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}

# Announced sector events (generator): the centre lies uniformly within 22.5 deg of the
# compass point, the width and top altitude are uniform in the ranges below, and weather
# multipliers scale with a severity uniform in [0.55, 1].
SECTOR_SHAPES = {
    "weather": {"width": (40.0, 120.0), "top": (40.0, 80.0)},
    "rocket_launch": {"width": (40.0, 80.0), "top": (30.0, 60.0)},
}
SECTOR_MULTIPLIERS = {"overcast": (0.55, 0.65, 1.25), "haze": (0.68, 0.72, 1.15)}


def _mean_sector_factor(kind: str) -> float:
    if kind == "rocket_launch":
        return 0.0
    transparency, sky, seeing = SECTOR_MULTIPLIERS.get(kind, SECTOR_MULTIPLIERS["overcast"])
    severity = np.linspace(0.55, 1.0, 46)
    value = (1 + severity * (transparency - 1)) * (1 + severity * (sky - 1)) / (1 + severity * (seeing - 1))
    return float(np.mean(value))


def _azimuth_inside(width) -> np.ndarray:
    """P(|offset - centre| <= width/2) for offsets 0..180 deg, centre uniform in +-22.5."""
    offsets = np.arange(181.0)[:, None, None]
    centres = np.linspace(-22.5, 22.5, 91)[None, :, None]
    halves = np.linspace(width[0], width[1], 81)[None, None, :] / 2
    return np.mean(np.abs(offsets - centres) <= halves, axis=(1, 2))


SECTOR_AZIMUTH = {name: _azimuth_inside(shape["width"]) for name, shape in SECTOR_SHAPES.items()}


def sector_inside_probability(kind: str, offset, altitude):
    shape = "rocket_launch" if kind == "rocket_launch" else "weather"
    low, high = SECTOR_SHAPES[shape]["top"]
    table = SECTOR_AZIMUTH[shape]
    index = np.clip(np.rint(np.abs(offset)).astype(np.int64), 0, 180)
    return table[index] * np.clip((high - np.asarray(altitude)) / (high - low), 0.0, 1.0)


class QualityModel:
    def __init__(self, task, config: dict):
        self.task = task
        self.config = config
        self.settings = config["quality"]
        self.samples = deque(maxlen=1600)
        self.clean = deque(maxlen=4000)
        self.band_checks = deque(maxlen=128)
        self.terrain = set()
        self.notices = set()
        self.forecast = None
        self.forecast_weather_nights = set()
        self.instrument = self.settings["instrument_prior"]
        self.reports = 0
        self.last_report = float("-inf")
        self.quake_until = float("-inf")
        self.zero_observes = 0
        self.estimate = self.settings["prior"]
        self.last_notice_signature = None
        self.terrain_model = None
        self.terrain_free = None

    def messages(self, snapshot: dict, now: float) -> None:
        for message in snapshot.get("new_messages", []):
            if message.get("record_type") == "bulletin" and message.get("initial"):
                for notice in message.get("notices", []):
                    if notice.get("event_kind") == "terrain_obstruction":
                        self.terrain.add(notice.get("direction", ""))
                if self.settings.get("terrain_model", False) and self.terrain:
                    self.terrain_model = TerrainModel(self.terrain, self.settings)
        bulletin = snapshot.get("latest_bulletin") or {}
        self.notices = {
            (str(row.get("event_kind", "")), str(row.get("direction", "")))
            for row in bulletin.get("notices", [])
            if row.get("event_kind") != "terrain_obstruction"
        }
        self.forecast = snapshot.get("latest_forecast")
        for notice in (self.forecast or {}).get("notices", []):
            if notice.get("event_kind") in {"rain", "storm", "overcast", "haze"}:
                self.forecast_weather_nights.update(notice.get("nights", []))
        if any(kind == "earthquake" for kind, _ in self.notices):
            self.quake_until = max(self.quake_until, now + 4 * 86400.0)

    def closed(self) -> bool:
        return any(kind in {"rain", "storm"} and direction == "ALL" for kind, direction in self.notices)

    def notice_factor(self, altitude, azimuth):
        """Gain factor from the announced sector events alone (no state is changed)."""
        alt, az = np.asarray(altitude), np.asarray(azimuth)
        factor = np.ones_like(alt, dtype=float)
        if not self.settings["directional_notices"]:
            return factor
        for kind, direction in self.notices:
            if direction not in DIRECTIONS:
                continue
            factor *= self._sector_factor(kind, direction, alt, az)
        return factor

    def _sector_factor(self, kind, direction, alt, az):
        offset = (az - DIRECTIONS[direction] + 180) % 360 - 180
        if self.settings.get("sector_prior", False) and kind in {"rain", "overcast", "haze", "storm", "rocket_launch"}:
            inside = sector_inside_probability(kind, offset, alt)
            return 1.0 - inside * (1.0 - _mean_sector_factor(kind))
        factor = np.ones_like(alt, dtype=float)
        near = np.abs(offset) <= 72.0
        if kind == "rocket_launch":
            factor[near & (alt < 62.0)] = 0.0
        elif kind in {"rain", "overcast", "haze", "storm"}:
            factor[(alt < 80.0) & near] = 0.40
        return factor

    def direction_factors(self, altitude, azimuth):
        alt, az = np.asarray(altitude), np.asarray(azimuth)
        factor = np.ones_like(alt, dtype=float)
        clean = np.ones_like(alt, dtype=bool)
        self.terrain_free = np.ones_like(alt, dtype=float)
        if self.terrain_model is not None:
            try:
                # A blocked target scores nothing, so the free probability scales expected
                # gains; fault levels keep the conservative envelope.
                self.terrain_free = 1.0 - self.terrain_model.blocked_probability(az, alt)
                clean &= ~self.terrain_model.envelope(az, alt)
            except Exception:  # noqa: BLE001 - fall back to the announced-sector envelope
                self.terrain_model = None
                self.terrain_free = np.ones_like(alt, dtype=float)
        if self.terrain_model is None:
            for direction in self.terrain:
                if direction in DIRECTIONS:
                    near = np.abs((az - DIRECTIONS[direction] + 180) % 360 - 180) <= 58.0
                    factor[(alt < 50.3) & near] = 0.0
                    clean[(alt < 50.3) & near] = False
        self.notice_clean = np.ones_like(alt, dtype=bool)
        for kind, direction in self.notices:
            if direction in DIRECTIONS:
                # Sector events reach up to 60 deg beyond a centre quantized to 22.5 deg.
                near = np.abs((az - DIRECTIONS[direction] + 180) % 360 - 180) <= 83.0
                self.notice_clean &= ~(near & (alt < 81.0))
        if not self.settings["directional_notices"]:
            self.local_clean = clean.copy()
            return factor, clean
        self.local_clean = clean.copy()
        for kind, direction in self.notices:
            if direction == "ALL":
                clean[:] = False
                continue
            if direction not in DIRECTIONS:
                continue
            near = np.abs((az - DIRECTIONS[direction] + 180) % 360 - 180) <= 72.0
            affected = (alt < 80.0) & near
            clean[affected] = False
            self.local_clean[affected] = False
            factor *= self._sector_factor(kind, direction, alt, az)
        return factor, clean

    def update(self, samples: list[dict], pending: dict, now: float, night: int) -> dict:
        positives = [row for row in samples if row["score"] > 0]
        self.zero_observes = self.zero_observes + 1 if samples and not positives else 0
        if not self.settings["weather_feedback"]:
            return self.status()
        for row in positives:
            if row["matched"] or row["saturated_mismatch"]:
                if row["clean"]:
                    self.band_checks.append((now, night, pending["program"], row["matched"], row["base_quality"]))
                continue  # saturation only constrains quality; it is not an unbiased sample
            if row["factor_estimate"] >= 0.97:
                continue
            ratio = (
                row["factor_estimate"] * self.task.scoring["flux_zero_point"] * self.task.scoring["exposure_zero_point_seconds"]
            )
            ratio /= self.task.flux[row["index"]] * pending["duration"] * max(1e-9, row["base_quality"])
            if not 0.015 <= ratio <= 3.0:
                continue
            self.samples.append((now, float(ratio), row["clean"]))
            if row["clean"]:
                self.clean.append((now, night, float(ratio)))
        self.scale(now)
        return self.status()

    def attach(self, monitor) -> None:
        """Use exact exposure levels from the instrument monitor when configured."""
        self.monitor = monitor if self.settings.get("level_model", False) else None

    def level_prediction(self, now: float, night: int):
        monitor = getattr(self, "monitor", None)
        if monitor is None:
            return None
        signature = frozenset(kind for kind, direction in self.notices if direction == "ALL")
        predicted = monitor.predict(now, night, signature)
        if predicted is None:
            return None
        level, sigma = predicted
        self.instrument = float(self.settings["instrument_prior"]) * math.exp(
            monitor.efficiency.log_efficiency(night, now))
        self.estimate = float(np.clip(math.exp(level), 0.005, 3.0))
        self.level_sigma = sigma
        return self.estimate, sigma

    def scale(self, now: float) -> float:
        if not self.settings["weather_feedback"]:
            return self.estimate
        recent = [value for timestamp, value, _ in self.samples if timestamp >= now - self.settings["memory_hours"] * 3600]
        if len(recent) >= 4:
            self.estimate = float(np.clip(np.median(recent), 0.025, 2.0))
        else:
            historical = [value for _, value, clean in self.samples if clean]
            self.estimate = float(np.median(historical[-200:])) if len(historical) >= 8 else self.settings["prior"]
        return self.estimate

    def program_probabilities(self, base_quality, uncertainty=None):
        band_quality = np.maximum(1e-6, np.asarray(base_quality) * self.estimate / self.instrument)
        thresholds = self.task.scoring["program"]["bands"]
        uncertainty = self.settings["program_uncertainty"] if uncertainty is None else uncertainty
        dark = 1 / (1 + np.exp(np.clip(-np.log(band_quality / thresholds["DARK"]) / uncertainty, -50, 50)))
        above_bright = 1 / (1 + np.exp(np.clip(-np.log(band_quality / thresholds["BRIGHT"]) / uncertainty, -50, 50)))
        return np.stack((dark, np.maximum(0, above_bright - dark), 1 - above_bright))

    def report_evidence(self, now: float) -> dict | None:
        settings = self.config["fault"]
        if (
            not settings["enabled"]
            or self.reports >= settings["maximum_reports"]
            or now - self.last_report < settings["cooldown_hours"] * 3600
            or (settings.get("quake_cooldown", True) and now < self.quake_until)
            or any(direction == "ALL" for _, direction in self.notices)
        ):
            return None
        minimum = settings["minimum_samples"]
        history = list(self.clean)
        if len(history) < 2 * minimum:
            return None
        recent = history[-minimum:]
        if len({night for _, night, _ in recent}) < settings["confirm_nights"]:
            return None
        if settings.get("forecast_guard", True) and any(
            self.task.nights[night].date in self.forecast_weather_nights for _, night, _ in recent
        ):
            return None
        earlier = history[:-minimum]
        baseline = float(np.median([value for _, _, value in earlier]))
        actual = float(np.median([value for _, _, value in recent]))
        drop = actual / max(1e-9, baseline)
        dark = [
            matched
            for timestamp, _, program, matched, base in self.band_checks
            if timestamp >= recent[0][0]
            and program == "DARK"
            and base * baseline / self.instrument > self.task.scoring["program"]["bands"]["DARK"] * 1.1
        ]
        if drop >= settings["drop_threshold"] or len(dark) < 4 or sum(dark) < 0.6 * len(dark):
            return None
        return {
            "baseline": baseline,
            "recent": actual,
            "drop": drop,
            "clean_samples": len(recent),
            "nights": len({night for _, night, _ in recent}),
            "dark_checks": len(dark),
            "dark_matches": int(sum(dark)),
        }

    def terrain_evidence(self, record: dict, pending: dict, task, extra_directions=()) -> int:
        """Unblocked fractions of hit targets inside the terrain envelope.

        The reference level comes from targets outside the envelope or, when the whole
        field lies inside it, from the level predicted for this exposure. Targets near
        directional notices published during the exposure are skipped, since a sector
        event can dim or close them as terrain would.
        """
        model = self.terrain_model
        if model is None or record.get("z") is None:
            return 0
        level = record["level"]
        predicted = pending.get("predicted_level")
        if level is None and predicted is not None and predicted[1] <= float(self.settings.get("terrain_sigma", 0.08)):
            level = predicted[0]
        all_zero = bool(record["zero"])
        if all_zero and np.any(record["hit"] & record["clean"]):
            return 0  # a closed dome zeroes targets outside the envelope as well
        if level is None and not all_zero:
            return 0
        excluded = [DIRECTIONS[d] for d in extra_directions if d in DIRECTIONS]
        rows = pending["targets"]
        indices = record["indices"]
        program = task.scoring["program"]
        mismatch = float(program["mismatch_multiplier"])
        multipliers = (*[float(x) for x in program["multipliers"].values()], mismatch)
        base = float(np.exp(level)) if level is not None else None
        steps = max(2, min(9, int(pending["duration"] // 450) + 2))
        times = pending["start"] + pending["duration"] * (np.arange(steps) + 0.5) / steps
        tracks, observations = [], []
        for j, row in enumerate(rows):
            if not record["hit"][j] or not row.get("notice_clean", True):
                continue
            i = int(indices[j])
            vectors = [horizontal_vectors(task.ra[i:i + 1], task.dec[i:i + 1], sidereal(float(t), task.longitude),
                                          task.latitude) for t in times]
            samples = [altaz(v) for v in vectors]
            az = np.array([sample[1][0] for sample in samples])
            alt = np.array([sample[0][0] for sample in samples])
            if not np.any(model.envelope(az, alt)):
                continue
            if any(np.any((np.abs((az - center + 180) % 360 - 180) <= 83.0) & (alt < 81.0)) for center in excluded):
                continue
            score = float(record["scores"][j])
            ratio = score / float(task.weight[i])
            if all_zero:
                # Terrain or an unannounced dome closure; the update mixes both.
                observation = ("zero", 0.0)
            elif score <= 0:
                observation = ("fraction", 0.0)
            elif any(abs(ratio - m) < 2e-5 for m in multipliers):
                # Saturated: flux*t*base*k*open/(f0*t0) >= 1, and z/ratio = f0*t0/(flux*t*k).
                observation = ("lower", float(min(1.0, record["z"][j] / ratio / base)))
            else:
                # y = open fraction x program multiplier, and the multiplier may be either.
                implied = float(record["z"][j]) / base
                declared = float(program["multipliers"][pending["program"]])
                low, high = implied / max(declared, mismatch), implied / min(declared, mismatch)
                observation = ("interval", (float(min(1.0, low)), float(min(1.0, high))))
            tracks.append((az, alt))
            observations.append(observation)
        return model.update(tracks, observations)

    def reported(self, now: float) -> None:
        self.last_report = now
        self.reports += 1
        self.samples.clear()
        self.clean.clear()
        self.band_checks.clear()
        self.estimate = self.settings["prior"]
        self.instrument = self.settings["instrument_prior"]

    def status(self) -> dict:
        return {
            "quality_scale": round(self.estimate, 5),
            "instrument_estimate": round(self.instrument, 5),
            "quality_samples": len(self.samples),
            "clean_samples": len(self.clean),
            "reports": self.reports,
            "zero_observes": self.zero_observes,
            "notices": [list(pair) for pair in sorted(self.notices)],
            "terrain": None if self.terrain_model is None else self.terrain_model.status(),
        }
