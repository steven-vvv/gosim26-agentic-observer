"""Jointly reschedule first science visits with public request visits fixed."""
from __future__ import annotations

import argparse
import csv
import json
import math
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

import numpy as np
from build_public_schedules import make_candidates
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array
from upper_bound import PROGRAMS, TICK, load, value_table

ROOT = Path(__file__).resolve().parent.parent


def optimize(source: Path, output: Path, per_night: int, limit: float) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    entry = next(item for item in data.values() if item["scenario"] == "dev-reference")
    sc = load(ROOT / "kit" / "scenarios" / "dev-reference")
    with (ROOT / "kit" / "scenarios" / "dev-reference" / "outputs" / "reference" / "observation_request_tiles.csv").open(newline="") as handle:
        sc["request_tiles"] = list(csv.DictReader(handle))
    sc["requests_by_id"] = {row["request_id"]: row for row in sc["requests"]}
    base, bonus, _span, per, programs = value_table(sc, return_programs=True)
    values = base + bonus
    raw, timestamps = make_candidates(sc, values, per, per_night, 0.0)
    at_time = {timestamp: tick for tick, timestamp in enumerate(timestamps)}
    tiles, slots = sc["tiles"], sc["slots"]
    nights = list(dict.fromkeys(slot["night_id"] for slot in slots))
    night_id = {night: index for index, night in enumerate(nights)}
    day_at = np.array([night_id[slot["night_id"]] for slot in slots], dtype=np.int32)
    tile_count, night_count = len(tiles), len(nights)
    duration = np.array([math.ceil(int(tile["nominal_exptime_seconds"]) / TICK) for tile in tiles])

    fixed = [action for action in entry["actions"] if action["request_id"]]
    busy = set()
    first_tag: dict[int, int] = {}
    fixed_flow = np.zeros(len(timestamps))
    for action in fixed:
        tile, tick = sc["index"][action["tile_id"]], at_time[action["timestamp_utc"]]
        first_tag[tile] = min(tick, first_tag.get(tile, tick))
        busy.update(range(tick, tick + duration[tile]))
        if tick % per:
            fixed_flow[tick] += 1
        end = tick + duration[tile]
        if end < len(timestamps) and end % per:
            fixed_flow[end] -= 1

    first_actions: dict[int, int] = {}
    for action in entry["actions"]:
        tile = sc["index"][action["tile_id"]]
        first_actions.setdefault(tile, at_time[action["timestamp_utc"]])
    proposed = {(tile, tick) for tile, tick, _weight, _request in raw}
    proposed.update(first_actions.items())
    candidates = []
    for tile, tick in sorted(proposed):
        if not np.isfinite(values[tile, tick]) or tick > first_tag.get(tile, len(timestamps)):
            continue
        coincident = tick == first_tag.get(tile)
        if not coincident and any(at in busy for at in range(tick, tick + duration[tile])):
            continue
        candidates.append((tile, tick, float(values[tile, tick]), coincident))
    print(f"  fixed {len(fixed)} request visits, {len(candidates)} science candidates", flush=True)
    n = len(candidates)
    status_start = n
    wait_start = status_start + tile_count * night_count
    flow_start = tile_count + len(timestamps)
    recurrence_start = flow_start + len(timestamps)
    same_day: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    occupancy: dict[int, list[int]] = defaultdict(list)
    rows: list[int] = []
    cols: list[int] = []
    val: list[int] = []

    def add(row: int, col: int, coefficient: int = 1) -> None:
        rows.append(row)
        cols.append(col)
        val.append(coefficient)

    for col, (tile, tick, _score, coincident) in enumerate(candidates):
        day = int(day_at[tick // per])
        add(tile, col)
        add(recurrence_start + tile * night_count + day, col, -1)
        same_day[(tile, day)].append((tick + duration[tile], col))
        if coincident:
            continue
        for at in range(tick, tick + duration[tile]):
            add(tile_count + at, col)
        if tick % per:
            add(flow_start + tick, col)
        end = tick + duration[tile]
        if end < len(timestamps) and end % per:
            add(flow_start + end, col, -1)
        for at in range((tick + per - 1) // per * per, tick + duration[tile], per):
            occupancy[at].append(col)
    for actions in same_day.values():
        actions.sort()
    for tile in range(tile_count):
        for day in range(night_count):
            row = recurrence_start + tile * night_count + day
            add(row, status_start + tile * night_count + day)
            if day:
                add(row, status_start + tile * night_count + day - 1, -1)

    actionable_rows = 0
    for slot_index, day in enumerate(day_at):
        tick = slot_index * per
        if tick in busy:
            continue
        actionable = np.flatnonzero(np.isfinite(values[:, tick]))
        occupied_here = occupancy.get(tick, [])
        for tile in actionable:
            row = recurrence_start + tile_count * night_count + actionable_rows
            add(row, wait_start + slot_index)
            if day:
                add(row, status_start + int(tile) * night_count + int(day) - 1)
            actions = same_day.get((int(tile), int(day)), [])
            for _end, col in actions[:bisect_right(actions, (tick, n))]:
                add(row, col)
            for col in occupied_here:
                add(row, col)
            actionable_rows += 1

    matrix = coo_array((np.array(val, dtype=np.int8),
                        (np.array(rows, dtype=np.int32), np.array(cols, dtype=np.int32))),
                       shape=(recurrence_start + tile_count * night_count + actionable_rows,
                              wait_start + len(slots))).tocsc()
    lower = np.concatenate((np.ones(tile_count), np.zeros(len(timestamps)),
                            np.full(len(timestamps), -np.inf), np.zeros(tile_count * night_count),
                            np.ones(actionable_rows)))
    upper = np.concatenate((np.ones(tile_count), 1 - np.isin(np.arange(len(timestamps)), list(busy)).astype(float),
                            -fixed_flow, np.zeros(tile_count * night_count),
                            np.full(actionable_rows, np.inf)))
    cost = np.zeros(wait_start + len(slots))
    cost[:n] = -np.array([score for _tile, _tick, score, _coincident in candidates])
    cost[wait_start:] = np.array([float(slot["duration_seconds"]) for slot in slots]) * float(
        sc["score"]["penalties"]["avoidable_wait_per_second"])
    integral = np.zeros(len(cost), dtype=np.int32)
    integral[:n] = 1
    baseline = np.zeros(len(cost))
    candidate_index = {(tile, tick): index for index, (tile, tick, _score, _coincident) in enumerate(candidates)}
    for tile, tick in first_actions.items():
        baseline[candidate_index[tile, tick]] = 1
        day = int(day_at[tick // per])
        baseline[status_start + tile * night_count + day:status_start + (tile + 1) * night_count] = 1
    baseline[wait_start:] = 1
    lhs = matrix @ baseline
    bad = np.flatnonzero((lhs < lower - 1e-8) | (lhs > upper + 1e-8))
    if len(bad):
        print(f"  baseline violates {len(bad)} rows: {[(int(row), float(lhs[row]), float(lower[row]), float(upper[row])) for row in bad[:8]]}", flush=True)
    result = milp(c=cost, integrality=integral, bounds=Bounds(0, 1),
                  constraints=LinearConstraint(matrix, lower, upper),
                  options={"time_limit": limit, "mip_rel_gap": 0.001})
    if result.x is None:
        raise RuntimeError(f"fixed-request solve failed: {result.message}")
    selected = [candidates[index] for index in np.flatnonzero(result.x[:n] > 0.5)]
    print(f"  MILP: {result.message}; {len(selected)} science visits", flush=True)
    actions = list(fixed)
    for tile, tick, _score, coincident in selected:
        if not coincident:
            actions.append({"timestamp_utc": timestamps[tick], "tile_id": tiles[tile]["tile_id"],
                            "program": PROGRAMS[programs[tile, tick]], "request_id": ""})
        else:
            action = next(action for action in actions
                          if action["tile_id"] == tiles[tile]["tile_id"] and action["timestamp_utc"] == timestamps[tick])
            action["program"] = PROGRAMS[programs[tile, tick]]
    entry["actions"] = sorted(actions, key=lambda action: action["timestamp_utc"])
    output.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(output, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "dist" / "public_refined.json")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "public_fixed_optimized.json")
    parser.add_argument("--per-night", type=int, default=16)
    parser.add_argument("--time-limit", type=float, default=180.0)
    args = parser.parse_args()
    optimize(args.source, args.out, args.per_night, args.time_limit)


if __name__ == "__main__":
    main()
