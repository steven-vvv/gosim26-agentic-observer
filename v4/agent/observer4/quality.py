"""Online weather and instrument evidence from public, possibly censored scores."""

from __future__ import annotations

from collections import deque

import numpy as np

DIRECTIONS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}


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

    def messages(self, snapshot: dict, now: float) -> None:
        for message in snapshot.get("new_messages", []):
            if message.get("record_type") == "bulletin" and message.get("initial"):
                for notice in message.get("notices", []):
                    if notice.get("event_kind") == "terrain_obstruction":
                        self.terrain.add(notice.get("direction", ""))
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

    def direction_factors(self, altitude, azimuth):
        alt, az = np.asarray(altitude), np.asarray(azimuth)
        factor = np.ones_like(alt, dtype=float)
        clean = np.ones_like(alt, dtype=bool)
        for direction in self.terrain:
            if direction in DIRECTIONS:
                near = np.abs((az - DIRECTIONS[direction] + 180) % 360 - 180) <= 58.0
                factor[(alt < 50.3) & near] = 0.0
                clean[(alt < 50.3) & near] = False
        if not self.settings["directional_notices"]:
            return factor, clean
        for kind, direction in self.notices:
            if direction == "ALL":
                clean[:] = False
                continue
            if direction not in DIRECTIONS:
                continue
            near = np.abs((az - DIRECTIONS[direction] + 180) % 360 - 180) <= 72.0
            affected = (alt < 80.0) & near
            clean[affected] = False
            if kind == "rocket_launch":
                factor[near & (alt < 62.0)] = 0.0
            elif kind in {"rain", "overcast", "haze", "storm"}:
                factor[affected] *= 0.40
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

    def program_probabilities(self, base_quality):
        band_quality = np.maximum(1e-6, np.asarray(base_quality) * self.estimate / self.instrument)
        thresholds = self.task.scoring["program"]["bands"]
        uncertainty = self.settings["program_uncertainty"]
        dark = 1 / (1 + np.exp(np.clip(-np.log(band_quality / thresholds["DARK"]) / uncertainty, -50, 50)))
        above_bright = 1 / (1 + np.exp(np.clip(-np.log(band_quality / thresholds["BRIGHT"]) / uncertainty, -50, 50)))
        return np.stack((dark, np.maximum(0, above_bright - dark), 1 - above_bright))

    def report_evidence(self, now: float) -> dict | None:
        settings = self.config["fault"]
        if (
            not settings["enabled"]
            or self.reports >= settings["maximum_reports"]
            or now - self.last_report < settings["cooldown_hours"] * 3600
            or now < self.quake_until
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
        if any(self.task.nights[night].date in self.forecast_weather_nights for _, night, _ in recent):
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
            "dark_matches": sum(dark),
        }

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
        }
