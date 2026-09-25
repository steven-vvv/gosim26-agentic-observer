"""Estimate same-night weather persistence from the public scenario weather CSVs."""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAGS = (1, 4, 8, 12, 24, 32)


def covariance_slope(pairs: list[tuple[float, float]]) -> tuple[float, float, float]:
    xs, ys = zip(*pairs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    xx = sum((x - mx) ** 2 for x in xs)
    yy = sum((y - my) ** 2 for y in ys)
    xy = sum((x - mx) * (y - my) for x, y in pairs)
    return xy / xx, xy / (xx * yy) ** 0.5, (my - mx)


def main() -> None:
    scenarios = sorted((ROOT / "scenarios").glob("comp-*"))
    scenarios += sorted((ROOT / "scenarios").glob("mc-*"))
    pairs = defaultdict(list)
    clean_means = []
    for scenario in scenarios:
        reference = scenario / "outputs" / "reference"
        weather_file = reference / "weather.csv"
        if not weather_file.is_file():
            continue
        nights = defaultdict(list)
        with weather_file.open(newline="") as source:
            for row in csv.DictReader(source):
                observable = row["is_observable"] == "true"
                w = (float(row["transparency"]) * float(row["sky_quality"]) / float(row["seeing_arcsec"])) if observable else None
                nights[row["night_id"]].append(w)
        values = [w for night in nights.values() for w in night if w is not None]
        if not values:
            continue
        clean_means.append(statistics.mean(values))
        for night in nights.values():
            for lag in LAGS:
                pairs[lag].extend((x, y) for x, y in zip(night, night[lag:]) if x is not None and y is not None)
    print(f"scenarios={len(clean_means)} clean_mean={statistics.mean(clean_means):.4f}")
    for lag in LAGS:
        slope, correlation, drift = covariance_slope(pairs[lag])
        print(f"lag={lag * 15:3d} min pairs={len(pairs[lag]):6d} slope={slope:.3f} correlation={correlation:.3f} drift={drift:+.4f}")


if __name__ == "__main__":
    main()
