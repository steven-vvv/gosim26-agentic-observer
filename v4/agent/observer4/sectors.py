"""Posterior over announced sector events (haze, overcast, rocket launches) from exposure levels.

A sector notice only names the compass point nearest the event's centre. The generator
draws the centre uniformly within 22.5 deg of it, a width and a top altitude from fixed
ranges, and scales the weather multipliers by a severity in [0.55, 1]. Targets inside the
sector score the exposure level times the sector factor, targets outside score the level
itself, so ratios of hit targets to the exposure's clean level locate the sector.
"""

from __future__ import annotations

import numpy as np

from .quality import DIRECTIONS, SECTOR_MULTIPLIERS, SECTOR_SHAPES

HALF_SECTOR = 22.5


def _factors(kind: str, severity):
    if kind == "rocket_launch":
        return np.zeros_like(severity)
    transparency, sky, seeing = SECTOR_MULTIPLIERS.get(kind, SECTOR_MULTIPLIERS["overcast"])
    return (1 + severity * (transparency - 1)) * (1 + severity * (sky - 1)) / (1 + severity * (seeing - 1))


class SectorEvent:
    OFFSET_SPAN = 90
    ALT_TOP = 91

    def __init__(self, kind: str, direction: str, settings: dict, seed: int):
        self.kind, self.direction = kind, direction
        self.settings = settings
        shape = SECTOR_SHAPES["rocket_launch" if kind == "rocket_launch" else "weather"]
        rng = np.random.default_rng(seed)
        count = int(settings.get("sector_particles", 2000))
        self.rng = rng
        self.shape = shape
        self.centre = rng.uniform(-HALF_SECTOR, HALF_SECTOR, count)
        self.half = rng.uniform(shape["width"][0], shape["width"][1], count) / 2.0
        self.top = rng.uniform(shape["top"][0], shape["top"][1], count)
        self.severity = rng.uniform(0.55, 1.0, count)
        self.factor = _factors(kind, self.severity)
        self.log_weight = np.zeros(count)
        self.updates = 0
        self._grid = None

    def _offset(self, azimuth):
        return (np.asarray(azimuth, dtype=float) - DIRECTIONS[self.direction] + 180.0) % 360.0 - 180.0

    def _weights(self):
        weights = np.exp(self.log_weight - np.max(self.log_weight))
        return weights / np.sum(weights)

    def grid(self):
        """Expected gain factor on a 1 deg offset x 1 deg altitude grid."""
        if self._grid is not None:
            return self._grid
        weights = self._weights() * (1.0 - self.factor)
        width = 2 * self.OFFSET_SPAN + 1
        diff = np.zeros((width + 1, self.ALT_TOP + 1))
        j0 = np.clip(np.ceil(self.centre - self.half + self.OFFSET_SPAN), 0, width).astype(np.int64)
        j1 = np.clip(np.floor(self.centre + self.half + self.OFFSET_SPAN) + 1, 0, width).astype(np.int64)
        k1 = np.clip(np.floor(self.top) + 1, 0, self.ALT_TOP).astype(np.int64)
        keep = (j1 > j0) & (k1 > 0)
        np.add.at(diff, (j0[keep], 0), weights[keep])
        np.add.at(diff, (j1[keep], 0), -weights[keep])
        np.add.at(diff, (j0[keep], k1[keep]), -weights[keep])
        np.add.at(diff, (j1[keep], k1[keep]), weights[keep])
        loss = np.cumsum(np.cumsum(diff, axis=0), axis=1)[:width, :self.ALT_TOP]
        self._grid = np.clip(1.0 - loss, 0.0, 1.0)
        return self._grid

    def expected_factor(self, altitude, azimuth):
        alt = np.asarray(altitude, dtype=float)
        offset = self._offset(azimuth)
        result = np.ones(alt.shape)
        inside = (np.abs(offset) <= self.OFFSET_SPAN) & (alt >= 0)
        if np.any(inside):
            j = np.rint(offset[inside] + self.OFFSET_SPAN).astype(np.int64)
            k = np.clip(np.floor(alt[inside]).astype(np.int64), 0, self.ALT_TOP - 1)
            result[inside] = self.grid()[j, k]
        return result

    def envelope(self, altitude, azimuth):
        offset = self._offset(azimuth)
        reach = HALF_SECTOR + self.shape["width"][1] / 2.0
        return (np.abs(offset) <= reach) & (np.asarray(altitude) <= self.shape["top"][1])

    def update(self, altitude, azimuth, low, high) -> int:
        """Ratio intervals [low, high] of target score to the clean level at given positions."""
        alt = np.asarray(altitude, dtype=float)
        offset = self._offset(azimuth)
        if not len(alt):
            return 0
        noise = float(self.settings.get("sector_ratio_noise", 0.06))
        inside = (np.abs(offset[None, :] - self.centre[:, None]) <= self.half[:, None]) & (
            alt[None, :] <= self.top[:, None])
        predicted = np.where(inside, self.factor[:, None], 1.0)
        miss = np.maximum(0.0, np.asarray(low)[None, :] - predicted) + np.maximum(0.0, predicted - np.asarray(high)[None, :])
        self.log_weight += np.sum(-0.5 * (miss / noise) ** 2, axis=1)
        self.log_weight -= np.max(self.log_weight)
        self.updates += len(alt)
        self._grid = None
        self._resample()
        return len(alt)

    def _resample(self) -> None:
        weights = self._weights()
        if 1.0 / float(np.sum(weights * weights)) >= 0.5 * len(weights):
            return
        rng, n = self.rng, len(weights)
        pick = rng.choice(n, size=n, p=weights)
        self.centre = np.clip(self.centre[pick] + rng.normal(0, 0.8, n), -HALF_SECTOR, HALF_SECTOR)
        self.half = np.clip(self.half[pick] + rng.normal(0, 0.8, n),
                            self.shape["width"][0] / 2.0, self.shape["width"][1] / 2.0)
        self.top = np.clip(self.top[pick] + rng.normal(0, 0.5, n), self.shape["top"][0], self.shape["top"][1])
        self.severity = np.clip(self.severity[pick] + rng.normal(0, 0.01, n), 0.55, 1.0)
        self.factor = _factors(self.kind, self.severity)
        self.log_weight = np.zeros(n)

    def status(self) -> dict:
        weights = self._weights()
        return {"kind": self.kind, "direction": self.direction, "updates": self.updates,
                "centre": round(float(weights @ self.centre), 2), "half_width": round(float(weights @ self.half), 2),
                "top": round(float(weights @ self.top), 2), "factor": round(float(weights @ self.factor), 3)}


class SectorTracker:
    """Active sector events keyed by (kind, direction), created and dropped with the notices."""

    KINDS = frozenset({"overcast", "haze", "rain", "storm", "rocket_launch"})

    def __init__(self, settings: dict):
        self.settings = settings
        self.events: dict[tuple, SectorEvent] = {}
        self.created = 0

    def sync(self, notices) -> None:
        active = {(kind, direction) for kind, direction in notices
                  if direction in DIRECTIONS and kind in self.KINDS}
        for key in list(self.events):
            if key not in active:
                del self.events[key]
        for key in active:
            if key not in self.events:
                self.created += 1
                self.events[key] = SectorEvent(key[0], key[1], self.settings, 20261006 + self.created)

    def factor(self, altitude, azimuth):
        result = np.ones(np.asarray(altitude).shape)
        for event in self.events.values():
            result = result * event.expected_factor(altitude, azimuth)
        return result

    def status(self) -> list:
        return [event.status() for event in self.events.values()]
