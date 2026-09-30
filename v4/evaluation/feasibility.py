"""Truth-based upper bound on required-target completion (research only).

For every required target and night, slot-aligned exposures of one to four slots are
scored with the card's true site weather, the target's own airmass and lunar factor at
slot midpoints, and permanent terrain sectors. A target is feasible when one such
exposure reaches the required factor threshold. The bound ignores fiber competition,
directional clouds and time spent elsewhere, so it is optimistic by construction.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from v4.agent.observer4.geometry import airmass, altaz, horizontal_vectors, lunar_factors, sidereal

from .upstream import ROOT


def _utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def card_bound(card: Path, run: Path | None = None, *, repaired: bool = False) -> dict:
    score = json.loads((card / "config" / "v4_score_config.json").read_text(encoding="utf-8"))
    scenario = json.loads((card / "config" / "v4_scenario.json").read_text(encoding="utf-8"))
    site = scenario["site"]
    lat, lon = float(site["latitude_deg"]), float(site["longitude_deg"])
    minimum = float(scenario["minimum_altitude_deg"])
    targets = _rows(card / "public" / "targets.csv")
    required = [row for row in targets if row["required"] == "true"]
    ids = [row["target_id"] for row in required]
    ra = np.array([float(row["ra_deg"]) for row in required])
    dec = np.array([float(row["dec_deg"]) for row in required])
    flux = np.array([float(row["feature_flux"]) for row in required])
    weather = _rows(card / "truth" / "v4_weather_truth.csv")
    events = _rows(card / "truth" / "v4_events.csv")
    fault = next((row for row in events if row["event_type"] == "instrument_fault"), None)
    terrain = [row for row in events if row["event_type"] == "terrain_obstruction"]
    starts = np.array([_utc(row["timestamp_utc"]) for row in weather])
    open_ = np.array([row["is_observable"] == "true" for row in weather])
    site_q = np.zeros(len(weather))
    for k, row in enumerate(weather):
        if open_[k]:
            efficiency = float(row["instrument_efficiency"])
            if repaired and fault and _utc(fault["actual_start_utc"]) <= starts[k] < _utc(fault["actual_end_utc"]):
                efficiency = min(1.0, efficiency / float(fault["instrument_efficiency_multiplier"]))
            site_q[k] = efficiency * float(row["transparency"]) * float(row["sky_quality"]) / float(row["seeing_arcsec"])
    nights = [row["night_id"] for row in weather]
    slot = 900.0
    # Target altitude at slot boundaries and midpoints; q at slot midpoints.
    q = np.zeros((len(weather), len(ids)))
    up = np.zeros((len(weather), len(ids)), dtype=bool)
    for k, start in enumerate(starts):
        samples = []
        for offset in (0.0, slot / 2, slot):
            vectors = horizontal_vectors(ra, dec, sidereal(start + offset, lon), lat)
            samples.append(altaz(vectors))
        alt_mid, az_mid = samples[1]
        blocked = np.zeros(len(ids), dtype=bool)
        for row in terrain:
            a0, a1, top = float(row["azimuth_start_deg"]), float(row["azimuth_end_deg"]), float(row["max_altitude_deg"])
            inside = (az_mid >= a0) & (az_mid <= a1) if a0 <= a1 else (az_mid >= a0) | (az_mid <= a1)
            blocked |= inside & (alt_mid <= top)
        up[k] = (np.minimum(np.minimum(samples[0][0], samples[1][0]), samples[2][0]) >= minimum) & ~blocked
        lunar = lunar_factors(ra, dec, start + slot / 2, lat, lon, score.get("lunar_model"))
        q[k] = site_q[k] * lunar / (score["q0"] * airmass(alt_mid) ** score["airmass_exponent"])
    rate = flux / (score["flux_zero_point"] * score["exposure_zero_point_seconds"])
    best = np.zeros(len(ids))
    for length in (1, 2, 3, 4):
        for k in range(len(weather) - length + 1):
            if nights[k] != nights[k + length - 1]:
                continue
            window = slice(k, k + length)
            ok = np.all(up[window], axis=0)
            factor = rate * np.sum(q[window], axis=0) * slot
            best = np.maximum(best, np.where(ok, np.minimum(factor, 1.0), 0.0))
    threshold = float(score["required"]["observed_factor_threshold"])
    feasible = best >= threshold
    result = {"card": card.name, "required": len(ids), "feasible": int(np.count_nonzero(feasible)),
              "infeasible": int(np.count_nonzero(~feasible)), "repaired": repaired}
    if run is not None:
        achieved: dict[str, float] = {}
        for row in _rows(run / "observations.csv"):
            if row["valid"] == "true":
                achieved[row["target_id"]] = max(achieved.get(row["target_id"], 0.0), float(row["factor"]))
        done = np.array([achieved.get(i, 0.0) >= threshold for i in ids])
        result.update({
            "completed": int(np.count_nonzero(done)),
            "missed_feasible": int(np.count_nonzero(feasible & ~done)),
            "missed_infeasible": int(np.count_nonzero(~feasible & ~done)),
            "completed_but_flagged_infeasible": int(np.count_nonzero(done & ~feasible)),
        })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", nargs="+", required=True)
    parser.add_argument("--runs", type=Path, help="variant directory with <card>/observations.csv")
    parser.add_argument("--repaired", action="store_true")
    args = parser.parse_args()
    rows = []
    for name in args.cards:
        run = args.runs / name if args.runs else None
        row = card_bound(ROOT / "v4" / "cards" / name, run, repaired=args.repaired)
        rows.append(row)
        print(json.dumps(row), flush=True)
    keys = [key for key in rows[0] if isinstance(rows[0][key], int) and not isinstance(rows[0][key], bool)]
    print(json.dumps({key: round(float(np.mean([row[key] for row in rows])), 3) for key in keys}))


if __name__ == "__main__":
    main()


def missed_detail(card: Path, run: Path) -> list[dict]:
    """Per missed-but-feasible required target: feasible nights, best factor, attempts."""
    score = json.loads((card / "config" / "v4_score_config.json").read_text(encoding="utf-8"))
    scenario = json.loads((card / "config" / "v4_scenario.json").read_text(encoding="utf-8"))
    site = scenario["site"]
    lat, lon = float(site["latitude_deg"]), float(site["longitude_deg"])
    minimum = float(scenario["minimum_altitude_deg"])
    targets = _rows(card / "public" / "targets.csv")
    required = [row for row in targets if row["required"] == "true"]
    ids = [row["target_id"] for row in required]
    ra = np.array([float(row["ra_deg"]) for row in required])
    dec = np.array([float(row["dec_deg"]) for row in required])
    flux = np.array([float(row["feature_flux"]) for row in required])
    weather = _rows(card / "truth" / "v4_weather_truth.csv")
    starts = np.array([_utc(row["timestamp_utc"]) for row in weather])
    site_q = np.array([
        float(row["instrument_efficiency"]) * float(row["transparency"]) * float(row["sky_quality"])
        / float(row["seeing_arcsec"]) if row["is_observable"] == "true" else 0.0 for row in weather])
    nights = [row["night_id"] for row in weather]
    q = np.zeros((len(weather), len(ids)))
    up = np.zeros((len(weather), len(ids)), dtype=bool)
    for k, start in enumerate(starts):
        alts = [altaz(horizontal_vectors(ra, dec, sidereal(start + o, lon), lat))[0] for o in (0.0, 450.0, 900.0)]
        up[k] = np.minimum(np.minimum(alts[0], alts[1]), alts[2]) >= minimum
        lunar = lunar_factors(ra, dec, start + 450.0, lat, lon, score.get("lunar_model"))
        q[k] = site_q[k] * lunar / (score["q0"] * airmass(alts[1]) ** score["airmass_exponent"])
    rate = flux / (score["flux_zero_point"] * score["exposure_zero_point_seconds"])
    night_best: dict[str, np.ndarray] = {}
    for length in (1, 2, 3, 4):
        for k in range(len(weather) - length + 1):
            if nights[k] != nights[k + length - 1]:
                continue
            window = slice(k, k + length)
            factor = np.where(np.all(up[window], axis=0), rate * np.sum(q[window], axis=0) * 900.0, 0.0)
            night_best[nights[k]] = np.maximum(night_best.get(nights[k], np.zeros(len(ids))), factor)
    per_night = np.array([night_best[n] for n in sorted(night_best)])
    achieved: dict[str, float] = {}
    attempts: dict[str, int] = {}
    decisions = {row["observe_index"]: row for row in _rows(run / "decisions.csv") if row["action"] == "observe"}
    for line in (run / "actions.jsonl").read_text(encoding="utf-8").splitlines():
        action = json.loads(line)
        if action.get("action") == "observe":
            for target in action["assignments"].values():
                attempts[target] = attempts.get(target, 0) + 1
    for row in _rows(run / "observations.csv"):
        if row["valid"] == "true":
            achieved[row["target_id"]] = max(achieved.get(row["target_id"], 0.0), float(row["factor"]))
    del decisions
    threshold = float(score["required"]["observed_factor_threshold"])
    detail = []
    for j, target in enumerate(ids):
        best = float(np.max(per_night[:, j]))
        if best >= threshold and achieved.get(target, 0.0) < threshold:
            detail.append({"target": target, "flux": round(float(flux[j]), 3), "dec": round(float(dec[j]), 2),
                           "ra": round(float(ra[j]), 2), "best_factor": round(min(best, 9.99), 3),
                           "feasible_nights": int(np.count_nonzero(per_night[:, j] >= threshold)),
                           "first_feasible_night": int(np.argmax(per_night[:, j] >= threshold)),
                           "last_feasible_night": int(len(per_night) - 1 - np.argmax(per_night[::-1, j] >= threshold)),
                           "attempts": attempts.get(target, 0), "achieved": round(achieved.get(target, 0.0), 3)})
    return detail
