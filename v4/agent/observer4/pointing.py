"""Infer a fixed pointing error from public hit/miss feedback, without fiber labels."""

from __future__ import annotations

import numpy as np

from .geometry import altaz, horizontal_vectors, sidereal


class PointingModel:
    def __init__(self, task):
        self.task = task
        axis = np.linspace(-0.12, 0.12, 17)
        da, dz = np.meshgrid(axis, axis, indexing="ij")
        self.offsets = np.stack((da.ravel(), dz.ravel()), axis=-1)
        self.log_weights = np.zeros(len(self.offsets))
        self.alt_offset = 0.0
        self.az_offset = 0.0
        self.uncertainty = 0.12
        self.samples = 0

    def feedback(self, pending: dict, result: dict) -> None:
        if not result or result.get("action") != "observe":
            return
        hit_ids = {row["target_id"] for row in result.get("hits", [])}
        indices = np.array([row["index"] for row in pending["targets"]], dtype=np.int32)
        if not len(indices):
            return
        task = self.task
        vectors = horizontal_vectors(
            task.ra[indices], task.dec[indices], sidereal(pending["start"], task.longitude), task.latitude
        )
        altitude, _ = altaz(vectors)
        # Near the altitude limit, a missing hit may reflect the whole-exposure rule.
        use = altitude > task.minimum_altitude + 6.0
        if not np.any(use) or pending["pointing"]["alt_deg"] < task.minimum_altitude + 7.0:
            return
        ca = pending["pointing"]["alt_deg"] + self.offsets[:, 0]
        cz = (pending["pointing"]["az_deg"] + self.offsets[:, 1]) % 360.0
        fiber, _, _, _ = task.grid.classify(vectors[use], ca, cz)
        expected_fiber = np.array([row["fiber"] for row in pending["targets"]], dtype=np.int32)[use]
        actual = np.array([task.ids[i] in hit_ids for i in indices], dtype=bool)[use]
        expected = fiber == expected_fiber[None, :]
        contradictions = np.count_nonzero(expected != actual[None, :], axis=1)
        self.log_weights -= 2.8 * contradictions
        self.log_weights -= np.max(self.log_weights)
        weights = np.exp(np.clip(self.log_weights, -50, 0))
        weights /= np.sum(weights)
        mean = weights @ self.offsets
        self.alt_offset, self.az_offset = float(mean[0]), float(mean[1])
        distance = np.max(np.abs(self.offsets - mean), axis=1)
        order = np.argsort(distance)
        self.uncertainty = float(distance[order[np.searchsorted(np.cumsum(weights[order]), 0.95)]])
        self.samples += int(np.count_nonzero(use))

    def status(self):
        return {
            "alt_offset_deg": round(self.alt_offset, 5),
            "az_offset_deg": round(self.az_offset, 5),
            "uncertainty_deg": round(self.uncertainty, 5),
            "samples": self.samples,
        }
