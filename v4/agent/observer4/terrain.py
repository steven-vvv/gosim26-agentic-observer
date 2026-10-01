"""Posterior over permanent terrain sectors from their announced directions and hits.

The generator draws one to three sectors, each with a uniform start azimuth, a width of
30-70 deg and a maximum altitude of 35-50 deg, and announces only the compass point
nearest each centre; two sectors with the same compass point share one notice. Every
consistent sector lies inside an envelope twice as wide as a typical sector, so treating
the envelope as blocked discards reachable sky. Particles hold one or two sectors per
announced direction; unblocked fractions of hit targets update them.
"""

from __future__ import annotations

import numpy as np

DIRECTIONS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}
HALF_SECTOR = 22.5


def _inside(az, center, half):
    return np.abs((az - center + 180.0) % 360.0 - 180.0) <= half


class TerrainModel:
    AZ_SPAN = 60.0
    ALT_STEP = 0.5
    ALT_TOP = 51.0

    def __init__(self, directions, settings: dict, seed: int = 20261005):
        self.settings = settings
        self.width = tuple(settings.get("terrain_width_range", (30.0, 70.0)))
        self.top = tuple(settings.get("terrain_altitude_range", (35.0, 50.0)))
        self.second = float(settings.get("terrain_second_probability", 0.15))
        self.rng = np.random.default_rng(seed)
        self.count = int(settings.get("terrain_particles", 3000))
        self.sectors = [self._prior(direction) for direction in sorted(set(directions)) if direction in DIRECTIONS]
        self.updates = 0

    def _prior(self, direction: str) -> dict:
        n, rng = self.count, self.rng
        base = DIRECTIONS[direction]
        return {
            "direction": direction,
            "center": base + rng.uniform(-HALF_SECTOR, HALF_SECTOR, (n, 2)),
            "half": rng.uniform(self.width[0], self.width[1], (n, 2)) / 2.0,
            "top": rng.uniform(self.top[0], self.top[1], (n, 2)),
            "active": np.stack((np.ones(n, dtype=bool), rng.random(n) < self.second), axis=1),
            "log_weight": np.zeros(n),
        }

    def _covered(self, sector, az, alt):
        """Particle x sample coverage for one announced direction."""
        covered = np.zeros((len(sector["log_weight"]), len(az)), dtype=bool)
        for j in range(2):
            covered |= (sector["active"][:, j:j + 1] & _inside(az[None, :], sector["center"][:, j:j + 1],
                                                            sector["half"][:, j:j + 1])
                        & (alt[None, :] <= sector["top"][:, j:j + 1]))
        return covered

    def envelope(self, azimuth, altitude):
        """Positions that some sector consistent with the notices could block."""
        az, alt = np.asarray(azimuth, dtype=float), np.asarray(altitude, dtype=float)
        mask = np.zeros(az.shape, dtype=bool)
        reach = HALF_SECTOR + self.width[1] / 2.0
        for sector in self.sectors:
            mask |= _inside(az, DIRECTIONS[sector["direction"]], reach) & (alt <= self.top[1])
        return mask

    def _grid(self, sector):
        """Coverage probability on a 1 deg x 0.5 deg grid around the announced direction.

        The union of a particle's two rectangles is added as A + B - (A and B) through a
        two-dimensional difference array, so the cost is linear in the particle count.
        """
        if sector.get("grid_version") == self.updates:
            return sector["grid"]
        weights = np.exp(sector["log_weight"] - np.max(sector["log_weight"]))
        weights /= np.sum(weights)
        width = int(2 * self.AZ_SPAN) + 1
        heights = int(self.ALT_TOP / self.ALT_STEP) + 1
        diff = np.zeros((width + 1, heights + 1))
        base = DIRECTIONS[sector["direction"]]
        low = (sector["center"] - sector["half"]) - base + self.AZ_SPAN
        high = (sector["center"] + sector["half"]) - base + self.AZ_SPAN
        top = sector["top"]
        active = sector["active"]

        def add(lo, hi, cap, weight):
            j0 = np.clip(np.ceil(lo), 0, width).astype(np.int64)
            j1 = np.clip(np.floor(hi) + 1, 0, width).astype(np.int64)
            k1 = np.clip(np.floor(cap / self.ALT_STEP - 0.5) + 1, 0, heights).astype(np.int64)
            keep = (j1 > j0) & (k1 > 0) & (weight != 0)
            j0, j1, k1, weight = j0[keep], j1[keep], k1[keep], weight[keep]
            np.add.at(diff, (j0, 0), weight)
            np.add.at(diff, (j1, 0), -weight)
            np.add.at(diff, (j0, k1), -weight)
            np.add.at(diff, (j1, k1), weight)

        add(low[:, 0], high[:, 0], top[:, 0], weights * active[:, 0])
        second = weights * active[:, 1]
        add(low[:, 1], high[:, 1], top[:, 1], second)
        both = second * active[:, 0]
        add(np.maximum(low[:, 0], low[:, 1]), np.minimum(high[:, 0], high[:, 1]),
            np.minimum(top[:, 0], top[:, 1]), -both)
        grid = np.cumsum(np.cumsum(diff, axis=0), axis=1)[:width, :heights]
        sector["grid"], sector["grid_version"] = np.clip(grid, 0.0, 1.0), self.updates
        return sector["grid"]

    def draw(self, rng) -> None:
        """Fix one posterior sample per announced direction (Thompson sampling).

        Planning on a sample explores uncertain edges in proportion to their chance of
        being open, so the posterior keeps sharpening where the mean would never look.
        """
        self._drawn = []
        for sector in self.sectors:
            weights = np.exp(sector["log_weight"] - np.max(sector["log_weight"]))
            weights /= np.sum(weights)
            self._drawn.append((sector, int(rng.choice(len(weights), p=weights))))

    def release(self) -> None:
        self._drawn = None

    def _drawn_blocked(self, az, alt):
        blocked = np.zeros(az.shape, dtype=bool)
        for sector, i in self._drawn:
            for j in range(2):
                if not sector["active"][i, j]:
                    continue
                blocked |= _inside(az, sector["center"][i, j], sector["half"][i, j]) & (alt <= sector["top"][i, j])
        return blocked.astype(float)

    def blocked_probability(self, azimuth, altitude):
        az, alt = np.asarray(azimuth, dtype=float), np.asarray(altitude, dtype=float)
        if getattr(self, "_drawn", None) is not None:
            return self._drawn_blocked(az, alt)
        free = np.ones(az.shape)
        for sector in self.sectors:
            grid = self._grid(sector)
            offset = (az - DIRECTIONS[sector["direction"]] + 180.0) % 360.0 - 180.0 + self.AZ_SPAN
            inside = (offset >= 0) & (offset <= 2 * self.AZ_SPAN) & (alt >= 0) & (alt < self.ALT_TOP)
            if not np.any(inside):
                continue
            j = np.round(offset[inside]).astype(np.int64)
            k = np.floor(alt[inside] / self.ALT_STEP).astype(np.int64)
            free[inside] *= 1.0 - grid[j, k]
        return 1.0 - free

    def _reach(self, sector, az, alt):
        return _inside(az, DIRECTIONS[sector["direction"]], HALF_SECTOR + self.width[1] / 2.0) & (alt <= self.top[1])

    def update(self, tracks, observations) -> int:
        """tracks: (az, alt) samples over each exposure; observations: (kind, value) with kind
        "fraction" (exact unblocked fraction), "lower" (saturated lower bound), "interval"
        ((low, high) when the program multiplier of the target is unknown) or "zero" (every
        hit of the exposure scored zero, so a dome closure is the alternative explanation).

        Each direction's particles are weighted with the other directions held at their
        current posterior coverage (mean field), since blocked light has one combined cause.
        """
        used = 0
        noise = float(self.settings.get("terrain_fraction_noise", 0.08))
        for (az, alt), observation in zip(tracks, observations):
            if observation is None:
                continue
            kind, value = observation
            az, alt = np.asarray(az, dtype=float), np.asarray(alt, dtype=float)
            relevant = [sector for sector in self.sectors if np.any(self._reach(sector, az, alt))]
            if not relevant:
                continue
            free = {id(sector): 1.0 - self._point_probability(sector, az, alt) for sector in relevant}
            for sector in relevant:
                others = np.ones(len(az))
                for other in relevant:
                    if other is not sector:
                        others = others * free[id(other)]
                covered = self._covered(sector, az, alt)
                predicted = np.mean((1.0 - covered) * others[None, :], axis=1)
                low, high = value if kind == "interval" else ((value, 1.0) if kind == "lower" else (value, value))
                miss = np.maximum(0.0, low - predicted) + np.maximum(0.0, predicted - high)
                if kind == "zero":
                    closure = float(self.settings.get("terrain_closure_probability", 0.03))
                    sector["log_weight"] += np.log(closure + (1.0 - closure) * np.exp(-0.5 * (miss / noise) ** 2))
                else:
                    sector["log_weight"] += -0.5 * (miss / noise) ** 2
                sector["log_weight"] -= np.max(sector["log_weight"])
            used += 1
        if used:
            self.updates += used
            self._resample()
        return used

    def _point_probability(self, sector, az, alt):
        weights = np.exp(sector["log_weight"] - np.max(sector["log_weight"]))
        weights /= np.sum(weights)
        return weights @ self._covered(sector, az, alt)

    def _resample(self) -> None:
        rng = self.rng
        for sector in self.sectors:
            weights = np.exp(sector["log_weight"])
            weights /= np.sum(weights)
            if 1.0 / float(np.sum(weights * weights)) >= 0.5 * len(weights):
                continue
            pick = rng.choice(len(weights), size=len(weights), p=weights)
            base = DIRECTIONS[sector["direction"]]
            n = len(pick)
            sector["center"] = np.clip(sector["center"][pick] + rng.normal(0, 0.6, (n, 2)),
                                       base - HALF_SECTOR, base + HALF_SECTOR)
            sector["half"] = np.clip(sector["half"][pick] + rng.normal(0, 0.5, (n, 2)),
                                     self.width[0] / 2.0, self.width[1] / 2.0)
            sector["top"] = np.clip(sector["top"][pick] + rng.normal(0, 0.25, (n, 2)), self.top[0], self.top[1])
            sector["active"] = sector["active"][pick]
            sector["log_weight"] = np.zeros(n)

    def status(self) -> dict:
        rows = []
        for sector in self.sectors:
            weights = np.exp(sector["log_weight"] - np.max(sector["log_weight"]))
            weights /= np.sum(weights)
            rows.append({"direction": sector["direction"],
                         "center": round(float(weights @ sector["center"][:, 0]) % 360.0, 2),
                         "half_width": round(float(weights @ sector["half"][:, 0]), 2),
                         "top": round(float(weights @ sector["top"][:, 0]), 2),
                         "second": round(float(weights @ sector["active"][:, 1]), 3)})
        return {"sectors": rows, "updates": self.updates}
