"""Expected future chances of each required target, from public geometry and observed sky.

A required target counts only once one exposure reaches the factor threshold, so its
urgency depends on how many later nights could still give such an exposure. For every
night the best lunar-over-airmass factor and the visible time come from the public
calendar; the sky level needed on that night follows from the flux, and the chance of
getting it comes from the sky levels measured so far. Announced rain or storm over the
whole sky removes a forecast night.
"""

from __future__ import annotations

import math

import numpy as np

from .geometry import airmass, altaz, horizontal_vectors, lunar_factors, sidereal

SAMPLE_SECONDS = 1200.0


class OpportunityModel:
    def __init__(self, task, settings: dict):
        self.task = task
        self.settings = settings
        self.indices = np.flatnonzero(task.required)
        self.nights = len(task.nights)
        self.kbest = np.zeros((self.nights, len(self.indices)))
        self.window = np.zeros((self.nights, len(self.indices)))
        self._precompute()
        self.closed_nights: set[str] = set()
        self.cache_key = None
        self.cache = None

    def _precompute(self) -> None:
        task, ids = self.task, self.indices
        if not len(ids):
            return
        ra, dec = task.ra[ids], task.dec[ids]
        limit = task.minimum_altitude + float(self.settings.get("opportunity_altitude_margin", 1.0))
        exponent = float(task.scoring["airmass_exponent"])
        model = task.scoring.get("lunar_model")
        for k, night in enumerate(task.nights):
            times = np.arange(night.start + SAMPLE_SECONDS / 2, night.end, SAMPLE_SECONDS)
            best = np.zeros(len(ids))
            for moment in times:
                altitude, _ = altaz(horizontal_vectors(ra, dec, sidereal(float(moment), task.longitude), task.latitude))
                up = altitude >= limit
                if not np.any(up):
                    continue
                value = lunar_factors(ra, dec, float(moment), task.latitude, task.longitude, model)
                value = value / (float(task.scoring["q0"]) * airmass(np.maximum(altitude, 1.0)) ** exponent)
                best = np.maximum(best, np.where(up, value, 0.0))
            self.kbest[k] = best
            self.window[k] = np.minimum(task.visibility[k, ids], float(task.maximum_exposure))

    def forecast(self, snapshot: dict) -> None:
        """Nights announced with whole-sky rain or storm are lost for faint targets."""
        forecast = snapshot.get("latest_forecast") or {}
        for notice in forecast.get("notices", []):
            if notice.get("direction") == "ALL" and notice.get("event_kind") in {"rain", "storm"}:
                self.closed_nights.update(str(night) for night in notice.get("nights", []))

    def expected_chances(self, night_index: int, log_levels, threshold: float) -> np.ndarray:
        """Expected count of later nights on which each required target can be completed."""
        levels = np.asarray(log_levels, dtype=float)
        if len(levels) < int(self.settings.get("opportunity_min_levels", 8)):
            center = math.log(float(self.settings.get("opportunity_prior_level", 0.6)))
            levels = center + float(self.settings.get("opportunity_prior_spread", 0.35)) * np.linspace(-1.5, 1.5, 13)
        key = (night_index, len(levels), len(self.closed_nights))
        if self.cache_key == key:
            return self.cache
        levels = np.sort(levels)
        zero_point = float(self.task.scoring["flux_zero_point"]) * float(self.task.scoring["exposure_zero_point_seconds"])
        safety = float(self.settings.get("opportunity_safety", 1.1))
        flux = self.task.flux[self.indices]
        chances = np.zeros(len(self.indices))
        for k in range(night_index + 1, self.nights):
            if self.task.nights[k].date in self.closed_nights:
                continue
            capacity = flux * self.window[k] * self.kbest[k] / zero_point
            needed = np.where(capacity > 0, safety * threshold / np.maximum(capacity, 1e-12), np.inf)
            # Share of observed sky levels at or above the level this night needs.
            above = 1.0 - np.searchsorted(levels, np.log(np.maximum(needed, 1e-12)), side="left") / len(levels)
            chances += np.where(np.isfinite(needed), above, 0.0)
        self.cache_key, self.cache = key, chances
        return chances

    def urgency(self, night_index: int, log_levels, threshold: float, weight: float) -> np.ndarray:
        """Multiplier for every target: 1 + weight / (1 + expected later chances) on required."""
        result = np.ones(len(self.task.ids))
        if not len(self.indices) or weight <= 0:
            return result
        chances = self.expected_chances(night_index, log_levels, threshold)
        result[self.indices] = 1.0 + weight / (1.0 + chances)
        return result
