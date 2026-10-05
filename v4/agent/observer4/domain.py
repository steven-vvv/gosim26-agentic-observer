"""Strict public initialization and compact immutable catalog indexes."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from .geometry import (
    RAD,
    SIDEREAL_RATE,
    FiberGrid,
    HorizonFactors,
    equatorial_vectors,
    max_hour_angle,
    parse_utc,
    sidereal,
)

PROTOCOL = "participant-agent-protocol-v4"
TARGET_COLUMNS = ("target_id", "ra_deg", "dec_deg", "target_class", "feature_flux", "science_weight", "required")


@dataclass(frozen=True)
class Night:
    start: float
    end: float
    name: str
    date: str


class Task:
    def __init__(self, publication: dict, altitude_margin: float = 0.2):
        if publication.get("schema_version") != "v4-initialize-v1":
            raise ValueError("unsupported initialization schema")
        self.card = dict(publication["task_card"])
        self.latitude = float(publication["site"]["latitude_deg"])
        self.longitude = float(publication["site"]["longitude_deg"])
        self.minimum_altitude = float(publication["site"]["minimum_altitude_deg"])
        self.start = parse_utc(publication["survey"]["start_utc"]).timestamp()
        self.end = parse_utc(publication["survey"]["end_utc"]).timestamp()
        self.slot_seconds = int(publication["survey"]["slot_seconds"])
        self.nights = [
            Night(
                parse_utc(n["observing_start_utc"]).timestamp(),
                parse_utc(n["observing_end_utc"]).timestamp(),
                str(n["night_id"]),
                str(n["night_date"]),
            )
            for n in publication["survey"]["nights"]
        ]
        if self.end <= self.start or any(n.end <= n.start for n in self.nights):
            raise ValueError("invalid public calendar")
        if any(left.end > right.start for left, right in zip(self.nights, self.nights[1:])):
            raise ValueError("overlapping public nights")
        self.grid = FiberGrid(publication["instrument"])
        self.minimum_exposure = int(publication["instrument"]["exposure"]["min_duration_seconds"])
        self.maximum_exposure = int(publication["instrument"]["exposure"]["max_duration_seconds"])
        self.scoring = dict(publication["scoring"])
        self.wallclock = float(publication["limits"]["global_wallclock_seconds"])
        columns = publication["targets"]["columns"]
        if set(columns) != set(TARGET_COLUMNS) or len(columns) != len(TARGET_COLUMNS):
            raise ValueError("invalid target columns")
        offsets = [columns.index(name) for name in TARGET_COLUMNS]
        rows = publication["targets"]["rows"]
        if any(len(row) != len(columns) for row in rows):
            raise ValueError("invalid target row length")
        ordered = [[row[j] for j in offsets] for row in rows]
        self.ids = [row[0] for row in ordered]
        if any(not isinstance(value, str) or not value for value in self.ids) or len(set(self.ids)) != len(self.ids):
            raise ValueError("invalid or duplicate target ids")
        if any(type(row[6]) is not bool for row in ordered):
            raise ValueError("required must be boolean")
        self.index = {value: i for i, value in enumerate(self.ids)}
        self.ra = np.array([row[1] for row in ordered], dtype=float)
        self.dec = np.array([row[2] for row in ordered], dtype=float)
        self.classes = [row[3] for row in ordered]
        self.flux = np.array([row[4] for row in ordered], dtype=float)
        self.weight = np.array([row[5] for row in ordered], dtype=float)
        self.required = np.array([row[6] for row in ordered], dtype=bool)
        if (
            not all(np.all(np.isfinite(a)) for a in (self.ra, self.dec, self.flux, self.weight))
            or np.any((self.ra < 0) | (self.ra >= 360))
            or np.any(np.abs(self.dec) > 90)
            or np.any(self.flux <= 0)
            or np.any(self.weight <= 0)
        ):
            raise ValueError("invalid public target values")
        self.vectors = equatorial_vectors(self.ra, self.dec)
        # Hour-angle-free factors of horizontal_vectors, formed once for the fixed catalog.
        self.horizon = HorizonFactors(self.ra, self.dec, self.latitude)
        self.hmax = max_hour_angle(self.dec, self.latitude, self.minimum_altitude + altitude_margin)
        width = float(self.scoring["uniformity"]["ra_band_width_deg"])
        self.bands, self.band_labels = np.unique(np.floor(self.ra / width).astype(np.int32), return_inverse=True)
        self.band_total = np.bincount(self.band_labels, minlength=len(self.bands))
        self.visibility = self._visibility()
        viable = self.visibility >= self.minimum_exposure
        self.last_night = np.max(np.where(viable, np.arange(len(self.nights))[:, None], -1), axis=0, initial=-1)
        self.first_night = np.min(
            np.where(viable, np.arange(len(self.nights))[:, None], len(self.nights)), axis=0, initial=len(self.nights)
        )
        self._neighbor_grid: dict[tuple, np.ndarray] = {}
        self._neighbor_cache: dict[int, np.ndarray] = {}

    def _visibility(self):
        result = np.zeros((len(self.nights), len(self.ids)))
        for k, night in enumerate(self.nights):
            start_lst = sidereal(night.start, self.longitude)
            span = (night.end - night.start) * SIDEREAL_RATE
            rising = (self.ra - self.hmax - start_lst) % 360.0
            overlap = np.maximum(0, np.minimum(span, rising + 2 * self.hmax) - rising) + np.maximum(
                0, np.minimum(span, rising - 360.0 + 2 * self.hmax)
            )
            result[k] = np.where(self.hmax >= 180, span, overlap) / SIDEREAL_RATE
        return result

    def prepare_neighbors(self, radius: float) -> None:
        self._radius = radius
        self._cell = 2 * np.sin(radius * RAD / 2)
        keys = np.floor(self.vectors / self._cell).astype(np.int32)
        groups: dict[tuple, list] = {}
        for i, key in enumerate(keys):
            groups.setdefault(tuple(key), []).append(i)
        self._neighbor_grid = {key: np.array(ids, dtype=np.int32) for key, ids in groups.items()}
        self._neighbor_cache.clear()

    def neighbors(self, i: int):
        cached = self._neighbor_cache.get(i)
        if cached is not None:
            return cached
        key = np.floor(self.vectors[i] / self._cell).astype(np.int32)
        groups = [self._neighbor_grid.get(tuple(key + delta)) for delta in itertools.product((-1, 0, 1), repeat=3)]
        near = np.concatenate([group for group in groups if group is not None])
        near = near[self.vectors[near] @ self.vectors[i] >= np.cos(self._radius * RAD)]
        self._neighbor_cache[i] = near
        return near

    def current_night(self, timestamp: float) -> int | None:
        for i, night in enumerate(self.nights):
            if night.start <= timestamp < night.end:
                return i
        return None

    def next_night(self, timestamp: float) -> float | None:
        return next((n.start for n in self.nights if n.start > timestamp), None)
