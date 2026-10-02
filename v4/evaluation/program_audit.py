"""Program-multiplier audit of a run: what the declared programs cost against the true bands."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .upstream import resolve


def audit(card: Path, run: Path) -> dict:
    resolve()
    from challenge.v4_fiber_map import radec_to_altaz
    from challenge.v4_runner import load_scenario
    from challenge.v4_scorer import WeatherTruth, lunar_quality_factor, program_band

    scenario = load_scenario(card / "config" / "v4_scenario.json")
    faults = [event for event in scenario.events if event.event_type == "instrument_fault"]
    weather = WeatherTruth(scenario.slots, faults)
    score = scenario.score_config
    site = scenario.site
    lat, lon = float(site["latitude_deg"]), float(site["longitude_deg"])
    targets = {row["target_id"]: row for row in scenario.targets}
    multipliers = score["program"]["multipliers"]
    decisions = {int(r["observe_index"]): r for r in csv.DictReader((run / "decisions.csv").open()) if r["action"] == "observe"}
    best = {}
    for row in csv.DictReader((run / "observations.csv").open()):
        if row["valid"] != "true" or float(row["score"]) <= 0:
            continue
        if row["target_id"] not in best or float(row["score"]) > float(best[row["target_id"]]["score"]):
            best[row["target_id"]] = row
    loss, counts = 0.0, Counter()
    for target_id, row in best.items():
        decision = decisions[int(row["observe_index"])]
        start = datetime.fromisoformat(decision["start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(decision["end_utc"].replace("Z", "+00:00"))
        target = targets[target_id]
        quality = weather.band_quality(
            start, end, score,
            lambda moment, t=target: radec_to_altaz(t["ra_deg"], t["dec_deg"], moment, lat, lon)[0],
            lambda moment, t=target: lunar_quality_factor(t["ra_deg"], t["dec_deg"], moment, site, score))
        band = program_band(quality, score)
        declared = decision["program"]
        base = float(row["score"]) / float(row["prog_mult"])
        counts[(declared, band)] += 1
        loss += base * (float(multipliers[band]) - float(row["prog_mult"]))
    return {"targets": len(best), "loss": round(loss, 1),
            "pairs": {f"{d}->{b}": n for (d, b), n in sorted(counts.items())}}


if __name__ == "__main__":
    print(json.dumps(audit(Path(sys.argv[1]), Path(sys.argv[2])), indent=1))
