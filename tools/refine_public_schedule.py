"""Move public-reference science credit earlier while retaining request visits."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from upper_bound import PROGRAMS, TICK, load, value_table

ROOT = Path(__file__).resolve().parent.parent


def refine(schedule: list[dict], scenario: Path, min_gain: float = 1.0) -> list[dict]:
    sc = load(scenario)
    base, bonus, _span, per, programs = value_table(sc, return_programs=True)
    values = base + bonus
    starts = [datetime.fromisoformat(slot["timestamp_utc"].replace("Z", "+00:00")) for slot in sc["slots"]]
    slot_index = {slot["timestamp_utc"]: index for index, slot in enumerate(sc["slots"])}

    def tick(timestamp: str) -> int:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        start = moment.replace(minute=(moment.minute // 15) * 15, second=0, microsecond=0)
        slot = slot_index[start.isoformat().replace("+00:00", "Z")]
        return slot * per + int((moment - starts[slot]).total_seconds() // TICK)

    def timestamp(index: int) -> str:
        return (starts[index // per] + timedelta(seconds=(index % per) * TICK)).isoformat().replace("+00:00", "Z")

    occupied: set[int] = set()
    entries: list[tuple[int, int, dict]] = []
    first: dict[str, tuple[int, dict]] = {}
    for action in sorted(schedule, key=lambda item: item["timestamp_utc"]):
        index = tick(action["timestamp_utc"])
        tile = sc["index"][action["tile_id"]]
        duration = math.ceil(int(sc["tiles"][tile]["nominal_exptime_seconds"]) / TICK)
        occupied.update(range(index, index + duration))
        entries.append((index, index + duration, action))
        first.setdefault(action["tile_id"], (index, action))

    remaining = {tile_id for tile_id, (_, action) in first.items() if action["request_id"]}
    additions = []
    while remaining:
        best = None
        for tile_id in remaining:
            current, _old = first[tile_id]
            tile = sc["index"][tile_id]
            duration = math.ceil(int(sc["tiles"][tile]["nominal_exptime_seconds"]) / TICK)
            current_value = float(values[tile, current])
            for index in range(0, current, per):
                value = float(values[tile, index])
                if not np.isfinite(value) or value <= current_value + min_gain:
                    continue
                if any(at in occupied for at in range(index, index + duration)):
                    continue
                next_entry = next((entry for entry in entries if entry[0] >= index), None)
                if next_entry and next_entry[0] % per and index + duration != next_entry[0]:
                    continue
                choice = (value - current_value, -index, tile_id, index, tile)
                if best is None or choice > best:
                    best = choice
        if best is None:
            break
        gain, _early, tile_id, index, tile = best
        duration = math.ceil(int(sc["tiles"][tile]["nominal_exptime_seconds"]) / TICK)
        action = {"timestamp_utc": timestamp(index), "tile_id": tile_id,
                  "program": PROGRAMS[programs[tile, index]], "request_id": ""}
        additions.append(action)
        entries.append((index, index + duration, action))
        entries.sort()
        occupied.update(range(index, index + duration))
        remaining.remove(tile_id)
        print(f"  {tile_id}: +{gain:.3f} at {action['timestamp_utc']}")
    print(f"  added {len(additions)} earlier first visits")
    return sorted([*schedule, *additions], key=lambda action: action["timestamp_utc"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "dist" / "public_schedules.json")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "public_refined.json")
    parser.add_argument("--min-gain", type=float, default=1.0)
    args = parser.parse_args()
    data = json.loads(args.source.read_text(encoding="utf-8"))
    entry = next(item for item in data.values() if item["scenario"] == "dev-reference")
    entry["actions"] = refine(entry["actions"], ROOT / "kit" / "scenarios" / "dev-reference", args.min_gain)
    args.out.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
