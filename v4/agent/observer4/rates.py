"""Rate of every target at the exposure midpoints, with the costly rows evaluated on demand."""

from __future__ import annotations

import numpy as np

from .geometry import airmass, lunar_factors, sidereal

OFFSETS = (0.0, 300.0, 600.0, 900.0, 1350.0, 1800.0)


class RateProfile:
    """flux * K * level for the six midpoint offsets, K being the lunar factor over airmass.

    Row 1 (300 s) of K is the sky quality of every visible target in the plan and is formed
    up front. The other rows are only needed for the targets of the fields searched, so
    ``profile[:, indices]`` evaluates the rows of the targets not seen yet; every value is
    the elementwise result of evaluating the whole visible subset.
    """

    offsets = np.array(OFFSETS)

    def __init__(self, task, now: float, altitude, subset, horizon, scale, safety, directional, zero_point):
        """``horizon`` is task.horizon.take(subset); ``altitude`` is the current altitude of all targets."""
        self.task, self.now, self.altitude = task, now, altitude
        self.level = (scale, safety, directional, zero_point)
        self.k = np.ones(len(task.ids))
        self.k[subset] = self._k(1, task.vectors[subset], horizon.altitude(self._lst(1)))
        self.rates = np.empty((len(OFFSETS), len(task.ids)))
        self.have = np.zeros(len(task.ids), dtype=bool)

    def _lst(self, j: int) -> float:
        return sidereal(self.now + OFFSETS[j], self.task.longitude)

    def _k(self, j: int, vectors, altitude):
        task = self.task
        lunar = lunar_factors(None, None, self.now + max(OFFSETS[j], 60.0), task.latitude, task.longitude,
                              task.scoring.get("lunar_model"), vectors=vectors)
        exponent = task.scoring["airmass_exponent"]
        return lunar / (task.scoring["q0"] * airmass(np.maximum(altitude, 1.0)) ** exponent)

    def prepare(self, indices) -> None:
        """Evaluate the rows of ``indices`` (which may repeat) in one batch."""
        wanted = np.zeros(len(self.have), dtype=bool)
        wanted[indices] = True
        self._evaluate(np.flatnonzero(wanted & ~self.have))

    def __getitem__(self, key):
        indices = key[1]
        self._evaluate(indices[~self.have[indices]])
        return self.rates[:, indices]

    def _evaluate(self, new) -> None:
        if len(new):
            task, (scale, safety, directional, zero_point) = self.task, self.level
            horizon, vectors = task.horizon.take(new), task.vectors[new]
            for j in range(len(OFFSETS)):
                if j == 1:
                    k = self.k[new]
                else:
                    k = self._k(j, vectors, self.altitude[new] if j == 0 else horizon.altitude(self._lst(j)))
                self.rates[j, new] = task.flux[new] * k * scale * directional[new] * safety / zero_point
            self.have[new] = True
