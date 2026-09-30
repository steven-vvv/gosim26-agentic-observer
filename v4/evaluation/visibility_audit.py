"""Separate required-target misses by their public geometric observing time."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from v4.agent.observer4.geometry import SIDEREAL_RATE, max_hour_angle, parse_utc, sidereal

from .coverage_audit import audit_card as coverage_card
from .upstream import ROOT

GROUPS = ("never_assigned", "assigned_no_valid_hit", "hit_below_threshold", "reached_threshold")
BINS = ("under_1h", "1_to_6h", "6_to_24h", "at_least_24h")


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def geometric_hours(card: Path, altitude_margin: float = 0.2) -> dict[str, float]:
    scenario = json.loads((card / "config/v4_scenario.json").read_text(encoding="utf-8"))
    targets = [row for row in _rows(card / "public/targets.csv") if row["required"].lower() == "true"]
    if not targets:
        return {}
    ra = np.array([float(row["ra_deg"]) for row in targets])
    dec = np.array([float(row["dec_deg"]) for row in targets])
    latitude = float(scenario["site"]["latitude_deg"])
    longitude = float(scenario["site"]["longitude_deg"])
    hmax = max_hour_angle(dec, latitude, float(scenario["minimum_altitude_deg"]) + altitude_margin)
    seconds = np.zeros(len(targets))
    for night in _rows(card / "public/v4_night_calendar.csv"):
        start = parse_utc(night["observing_start_utc"]).timestamp()
        end = parse_utc(night["observing_end_utc"]).timestamp()
        span = (end - start) * SIDEREAL_RATE
        rising = (ra - hmax - sidereal(start, longitude)) % 360.0
        overlap = np.maximum(0, np.minimum(span, rising + 2 * hmax) - rising)
        overlap += np.maximum(0, np.minimum(span, rising - 360.0 + 2 * hmax))
        seconds += np.where(hmax >= 180, span, overlap) / SIDEREAL_RATE
    return {row["target_id"]: float(value / 3600) for row, value in zip(targets, seconds)}


def _group_hours(card: Path, result: Path) -> dict[str, list[float]]:
    coverage = coverage_card(card, result)
    hours = geometric_hours(card)
    assigned = set()
    with (result / "actions.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            action = json.loads(line)
            if action.get("action") == "observe":
                assigned.update(action["assignments"].values())
    best_factor = {}
    for row in _rows(result / "observations.csv"):
        if row["valid"].lower() == "true":
            target = row["target_id"]
            best_factor[target] = max(best_factor.get(target, 0.0), float(row["factor"]))
    scoring = json.loads((card / "config/v4_score_config.json").read_text(encoding="utf-8"))
    threshold = float(scoring["required"]["observed_factor_threshold"])
    groups = {name: [] for name in GROUPS}
    for target, visible_hours in hours.items():
        if target not in assigned:
            group = "never_assigned"
        elif target not in best_factor:
            group = "assigned_no_valid_hit"
        elif best_factor[target] < threshold:
            group = "hit_below_threshold"
        else:
            group = "reached_threshold"
        groups[group].append(visible_hours)
    if any(len(groups[name]) != coverage[name] for name in GROUPS):
        raise ValueError(f"visibility and coverage classification disagree for {result}")
    return groups


def _summarize(values: list[float]) -> dict:
    hours = np.asarray(values, dtype=float)
    return {
        "count": len(values),
        "visibility_hours_median": round(float(np.median(hours)), 6) if len(hours) else None,
        "visibility_hours_p10": round(float(np.quantile(hours, 0.1)), 6) if len(hours) else None,
        "visibility_hours_p90": round(float(np.quantile(hours, 0.9)), 6) if len(hours) else None,
        "bins": dict(zip(BINS, (int(np.count_nonzero(mask)) for mask in (
            hours < 1, (hours >= 1) & (hours < 6),
            (hours >= 6) & (hours < 24), hours >= 24,
        )))),
    }


def audit(runs: Path, cards: Path, variants: list[str]) -> dict:
    if not variants or len(set(variants)) != len(variants):
        raise ValueError("select distinct variants")
    output = {"schema": "gosim26.v4.visibility-audit.v1", "variants": {}}
    for variant in variants:
        paths = sorted((runs / variant).glob("*/summary.json"))
        if not paths:
            raise ValueError(f"no completed results for {variant}")
        combined = {name: [] for name in GROUPS}
        per_card = []
        for path in paths:
            groups = _group_hours(cards / path.parent.name, path.parent)
            for name in GROUPS:
                combined[name].extend(groups[name])
            per_card.append({"card": path.parent.name,
                             "groups": {name: _summarize(values) for name, values in groups.items()}})
        output["variants"][variant] = {
            "cards": len(per_card),
            "groups": {name: _summarize(values) for name, values in combined.items()},
            "per_card": per_card,
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--cards", type=Path, default=ROOT / "v4/cards")
    parser.add_argument("--variants", nargs="+", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = audit(args.runs, args.cards, args.variants)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
