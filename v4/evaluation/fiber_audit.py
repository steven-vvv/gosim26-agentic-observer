"""Where the fibre time of one run went, from its public outputs (no truth files).

* fill: for every valid exposure, the cells of the actual pointing that held a target above the
  altitude limit, the cells holding a target the exposure could improve (predicted with the
  median measured quality of that exposure), and the fibres assigned;
* repeats: under best-single-observation scoring a later, better observation of a target voids
  the earlier one; first and repeat observations are compared in fibre hours and score gained.

    uv run python -m v4.evaluation.fiber_audit RUN_DIR CARD_DIR
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path

import numpy as np

from v4.agent.observer4.geometry import FiberGrid, altaz, horizontal_vectors, sidereal


def _rows(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        yield from csv.DictReader(stream)


def audit(run: Path, card: Path) -> dict:
    scenario = json.loads((card / "config" / "v4_scenario.json").read_text(encoding="utf-8"))
    field = json.loads((card / "config" / "v4_fiber_config.json").read_text(encoding="utf-8"))["field"]
    score = json.loads((card / "config" / "v4_score_config.json").read_text(encoding="utf-8"))
    zero_point = float(score["flux_zero_point"]) * float(score["exposure_zero_point_seconds"])
    latitude, longitude = scenario["site"]["latitude_deg"], scenario["site"]["longitude_deg"]
    side = math.isqrt(int(field["n_fibers"]))
    grid = FiberGrid({"grid_side": side, "n_fibers": field["n_fibers"],
                      "fiber_area_deg2": field["fiber_area_deg2"], "gap_deg": field["gap_deg"]})
    targets = list(_rows(card / "public" / "targets.csv"))
    index = {row["target_id"]: i for i, row in enumerate(targets)}
    ra = np.array([float(row["ra_deg"]) for row in targets])
    dec = np.array([float(row["dec_deg"]) for row in targets])
    flux = np.array([float(row["feature_flux"]) for row in targets])
    by_exposure = collections.defaultdict(list)
    for row in _rows(run / "observations.csv"):
        if row["valid"] == "true":
            by_exposure[int(row["observe_index"])].append(row)
    best_factor = np.zeros(len(targets))
    best_score = collections.defaultdict(float)
    cells_up, cells_improvable, assigned = [], [], []
    first = {"fiber_hours": 0.0, "gain": 0.0}
    repeat = {"fiber_hours": 0.0, "gain": 0.0}
    seen = set()
    for decision in _rows(run / "decisions.csv"):
        if decision["action"] != "observe" or decision["valid"] != "true":
            continue
        exposure = int(decision["observe_index"])
        duration = float(decision["duration_seconds"])
        start = datetime.fromisoformat(decision["start_utc"].replace("Z", "+00:00")).timestamp()
        vectors = horizontal_vectors(ra, dec, sidereal(start, longitude), latitude)
        altitude, _ = altaz(vectors)
        up = np.flatnonzero(altitude >= scenario["minimum_altitude_deg"])
        fibers = grid.classify(vectors[up], np.array([float(decision["alt_deg"])]),
                               np.array([float(decision["az_deg"])]))[0][0]
        inside = fibers >= 0
        observed = by_exposure.get(exposure, [])
        quality = statistics.median(float(row["quality"]) for row in observed) if observed else 0.0
        members, cells = up[inside], fibers[inside]
        predicted = np.minimum(1.0, flux[members] * quality * duration / zero_point)
        cells_up.append(len(set(cells.tolist())))
        cells_improvable.append(len(set(cells[predicted > best_factor[members] + 0.02].tolist())))
        assigned.append(int(decision["assigned_count"]))
        for row in observed:
            target, score = row["target_id"], float(row["score"])
            bucket = repeat if target in seen else first
            bucket["fiber_hours"] += duration / 3600.0
            bucket["gain"] += max(0.0, score - best_score[target])
            best_score[target] = max(best_score[target], score)
            best_factor[index[target]] = max(best_factor[index[target]], float(row["factor"]))
            seen.add(target)
    total_hours = first["fiber_hours"] + repeat["fiber_hours"]
    return {
        "exposures": len(assigned), "fibers": grid.n,
        "cells_with_target": round(statistics.mean(cells_up), 2),
        "cells_with_improvable_target": round(statistics.mean(cells_improvable), 2),
        "assigned": round(statistics.mean(assigned), 2),
        "targets_observed": len(seen),
        "repeat_share_of_fiber_hours": round(repeat["fiber_hours"] / max(total_hours, 1e-9), 3),
        "points_per_fiber_hour_first": round(first["gain"] / max(first["fiber_hours"], 1e-9), 3),
        "points_per_fiber_hour_repeat": round(repeat["gain"] / max(repeat["fiber_hours"], 1e-9), 3),
        "first_gain": round(first["gain"], 1), "repeat_gain": round(repeat["gain"], 1),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("card", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.run, args.card), sort_keys=True))


if __name__ == "__main__":
    main()
