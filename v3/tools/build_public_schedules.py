"""Build one-off replay schedules from the two published practice scenarios."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents" / "public_once"))
sys.path.insert(0, str(ROOT / "kit"))

from challenge.challenge_workflow import ChallengeWorkflow
from schedule import fingerprint
from upper_bound import PROGRAMS, TICK, load, value_table


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def make_candidates(sc: dict, values: np.ndarray, per: int, max_per_night: int,
                    early_weight: float) -> tuple[list[tuple[int, int, float, str]], list[str]]:
    slots = sc["slots"]
    timestamps = []
    night_ticks: dict[str, list[int]] = defaultdict(list)
    for index, slot in enumerate(slots):
        start = datetime.fromisoformat(slot["timestamp_utc"].replace("Z", "+00:00"))
        for offset in range(per):
            tick = index * per + offset
            timestamps.append((start + timedelta(seconds=offset * TICK)).isoformat().replace("+00:00", "Z"))
            night_ticks[slot["night_id"]].append(tick)
    request_tiles = defaultdict(list)
    for link in sc["request_tiles"]:
        request_tiles[link["tile_id"]].append(sc["requests_by_id"][link["request_id"]])
    output = []
    for tile_index, tile in enumerate(sc["tiles"]):
        base = values[tile_index]
        reward = np.zeros(len(timestamps), dtype=np.float32)
        tags = [""] * len(timestamps)
        for request in sorted(request_tiles[tile["tile_id"]], key=lambda row: row["deadline_utc"]):
            bonus = 2.0 * (float(request["completion_reward"]) + float(request["miss_penalty"])) / float(request["required_tile_count"])
            for slot_index, timestamp in enumerate(timestamps):
                if request["available_from_utc"] <= timestamp < request["deadline_utc"] and bonus > reward[slot_index]:
                    reward[slot_index] = bonus
                    tags[slot_index] = request["request_id"]
        weight = base + reward + early_weight * (1.0 - np.arange(len(timestamps)) / len(timestamps))
        if tile["scheduling_class"] == "REQUIRED":
            weight = weight + 1000.0
        else:
            weight = weight + 100.0
        chosen: set[int] = set()
        finite = np.flatnonzero(np.isfinite(weight))
        if not len(finite):
            continue
        top = finite[np.argsort(weight[finite])[-min(48, len(finite)):]]
        chosen.update(int(value) for value in top)
        for indexes in night_ticks.values():
            available = [index for index in indexes if np.isfinite(weight[index])]
            if available:
                chosen.update(sorted(available, key=lambda index: float(weight[index]), reverse=True)[:max_per_night])
                boundary = [index for index in available if index % per == 0]
                if boundary:
                    chosen.add(max(boundary, key=lambda index: float(weight[index])))
        output.extend((tile_index, index, float(weight[index]), tags[index]) for index in sorted(chosen))
    return output, timestamps


def solve(sc: dict, values: np.ndarray, programs: np.ndarray, per: int, limit: float,
          early_weight: float, max_per_night: int, request_wait_scale: float,
          exact_wait: bool = False, force_all: bool = False) -> list[dict]:
    slots, tiles = sc["slots"], sc["tiles"]
    candidates, timestamps = make_candidates(sc, values, per, max_per_night, early_weight)
    nights = list(dict.fromkeys(slot["night_id"] for slot in slots))
    night_index = {night: index for index, night in enumerate(nights)}
    day_of_slot = [night_index[slot["night_id"]] for slot in slots]
    tile_count, night_count = len(tiles), len(nights)
    candidate_count = len(candidates)
    status_start = candidate_count
    wait_var_start = status_start + tile_count * night_count
    same_day_actions: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    occupied_by_tick: dict[int, list[int]] = defaultdict(list)
    for column, (tile, tick, _weight, _request) in enumerate(candidates):
        occupied = math.ceil(int(tiles[tile]["nominal_exptime_seconds"]) / TICK)
        same_day_actions[(tile, day_of_slot[tick // per])].append((tick + occupied, column))
        if exact_wait:
            for at in range(tick, tick + occupied):
                occupied_by_tick[at].append(column)
    for actions in same_day_actions.values():
        actions.sort()
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    flow_start = tile_count + len(timestamps)
    recurrence_start = flow_start + len(timestamps)
    for column, (tile, slot, _weight, _request) in enumerate(candidates):
        rows.append(tile)
        cols.append(column)
        data.append(1.0)
        rows.append(recurrence_start + tile * night_count + day_of_slot[slot // per])
        cols.append(column)
        data.append(-1.0)
        occupied = math.ceil(int(tiles[tile]["nominal_exptime_seconds"]) / TICK)
        for index in range(slot, slot + occupied):
            rows.append(len(tiles) + index)
            cols.append(column)
            data.append(1.0)
        if slot % per:
            rows.append(flow_start + slot)
            cols.append(column)
            data.append(1.0)
        end = slot + occupied
        if end < len(timestamps) and end % per:
            rows.append(flow_start + end)
            cols.append(column)
            data.append(-1.0)
    for tile in range(tile_count):
        for day in range(night_count):
            row = recurrence_start + tile * night_count + day
            rows.append(row)
            cols.append(status_start + tile * night_count + day)
            data.append(1.0)
            if day:
                rows.append(row)
                cols.append(status_start + tile * night_count + day - 1)
                data.append(-1.0)
    actionable_rows = 0
    wait_ticks = range(len(timestamps)) if exact_wait else range(0, len(timestamps), per)
    for tick in wait_ticks:
        slot_index, day = tick // per, day_of_slot[tick // per]
        for tile in np.flatnonzero(np.isfinite(values[:, tick])):
            row = recurrence_start + tile_count * night_count + actionable_rows
            rows.append(row)
            cols.append(wait_var_start + (tick if exact_wait else slot_index))
            data.append(1.0)
            if day:
                rows.append(row)
                cols.append(status_start + int(tile) * night_count + day - 1)
                data.append(1.0)
            actions = same_day_actions.get((int(tile), day), [])
            for _end, column in actions[:bisect_right(actions, (tick, candidate_count))]:
                rows.append(row)
                cols.append(column)
                data.append(1.0)
            if exact_wait:
                for column in occupied_by_tick.get(tick, ()):
                    rows.append(row)
                    cols.append(column)
                    data.append(1.0)
            actionable_rows += 1
    forced_requests = [("RQ0001", 3)] if len(slots) > 1000 else []
    for request_index, (request_id, _need) in enumerate(forced_requests):
        row = recurrence_start + tile_count * night_count + actionable_rows + request_index
        for column, (_tile, _tick, _weight, tag) in enumerate(candidates):
            if tag == request_id:
                rows.append(row)
                cols.append(column)
                data.append(1.0)
    matrix = coo_array((np.array(data), (np.array(rows, dtype=np.int32), np.array(cols, dtype=np.int32))),
                       shape=(recurrence_start + tile_count * night_count + actionable_rows + len(forced_requests),
                              wait_var_start + (len(timestamps) if exact_wait else len(slots)))).tocsc()
    tile_lower = np.array([int(any(candidate[0] == tile for candidate in candidates)) if force_all else 0
                           for tile in range(tile_count)])
    lower = np.concatenate((tile_lower, np.zeros(len(timestamps)), np.full(len(timestamps), -np.inf),
                            np.zeros(tile_count * night_count), np.ones(actionable_rows),
                            np.array([need for _request_id, need in forced_requests])))
    upper = np.concatenate((np.ones(flow_start), np.zeros(len(timestamps)),
                            np.zeros(tile_count * night_count), np.full(actionable_rows + len(forced_requests), np.inf)))
    costs = np.zeros(wait_var_start + (len(timestamps) if exact_wait else len(slots)))
    costs[:candidate_count] = -np.array([item[2] for item in candidates])
    wait_costs = (np.full(len(timestamps), TICK) if exact_wait else
                  np.array([float(slot["duration_seconds"]) for slot in slots])) * float(
                      sc["score"]["penalties"]["avoidable_wait_per_second"])
    if request_wait_scale < 1:
        fillable = np.zeros(len(wait_costs), dtype=bool)
        for link in sc["request_tiles"]:
            request = sc["requests_by_id"][link["request_id"]]
            tile = sc["index"][link["tile_id"]]
            for tick in wait_ticks:
                timestamp = timestamps[tick]
                if (request["available_from_utc"] <= timestamp < request["deadline_utc"]
                        and np.isfinite(values[tile, tick])):
                    fillable[tick if exact_wait else tick // per] = True
        wait_costs[fillable] *= request_wait_scale
    costs[wait_var_start:] = wait_costs
    integer = np.zeros(len(costs), dtype=np.int32)
    integer[:candidate_count] = 1
    result = milp(
        c=costs,
        integrality=integer,
        bounds=Bounds(0, 1),
        constraints=LinearConstraint(matrix, lower, upper),
        options={"time_limit": limit, "mip_rel_gap": 0.003},
    )
    if result.x is None:
        raise RuntimeError(f"integer schedule failed: {result.message}")
    selected = [candidates[index] for index in np.flatnonzero(result.x[:candidate_count] > 0.5)]
    print(f"  MILP: {len(candidates)} candidates, {len(selected)} tiles, {result.message}", flush=True)
    actions = []
    occupied_slots = set()
    primary_end = {}
    for tile, slot, _weight, request in selected:
        occupied = math.ceil(int(tiles[tile]["nominal_exptime_seconds"]) / TICK)
        occupied_slots.update(range(slot, slot + occupied))
        primary_end[tiles[tile]["tile_id"]] = slot + occupied
        actions.append({"slot_index": slot, "timestamp_utc": timestamps[slot],
                        "tile_id": tiles[tile]["tile_id"], "program": PROGRAMS[programs[tile, slot]],
                        "request_id": request})

    for request in sorted(sc["requests"], key=lambda row: row["deadline_utc"]):
        request_id = request["request_id"]
        needed = int(request["required_tile_count"]) - sum(action["request_id"] == request_id for action in actions)
        if needed <= 0:
            continue
        possible = []
        for link in sc["request_tiles"]:
            if link["request_id"] != request_id:
                continue
            tile_id = link["tile_id"]
            if any(action["request_id"] == request_id and action["tile_id"] == tile_id for action in actions):
                continue
            tile = sc["index"][tile_id]
            occupied = math.ceil(int(tiles[tile]["nominal_exptime_seconds"]) / TICK)
            for slot, timestamp in enumerate(timestamps):
                if (slot % per or slot < primary_end.get(tile_id, 0)
                        or not request["available_from_utc"] <= timestamp < request["deadline_utc"]):
                    continue
                if not np.isfinite(values[tile, slot]):
                    continue
                if any(index in occupied_slots for index in range(slot, slot + occupied)):
                    continue
                possible.append((slot, tile, occupied, tile_id))
                break
        for slot, tile, occupied, tile_id in sorted(possible)[:needed]:
            if any(index in occupied_slots for index in range(slot, slot + occupied)):
                continue
            occupied_slots.update(range(slot, slot + occupied))
            actions.append({"slot_index": slot, "timestamp_utc": timestamps[slot],
                            "tile_id": tile_id, "program": PROGRAMS[programs[tile, slot]],
                            "request_id": request_id})
    actions.sort(key=lambda row: row["slot_index"])
    if any(left["slot_index"] >= right["slot_index"] for left, right in zip(actions, actions[1:])):
        raise RuntimeError("overlapping schedule")
    for action in actions:
        del action["slot_index"]
    return actions


def build(scenario: Path, limit: float, early_weight: float, max_per_night: int,
          request_wait_scale: float, exact_wait: bool = False,
          force_all: bool = False) -> tuple[str, dict]:
    sc = load(scenario)
    ref = scenario / "outputs" / "reference"
    sc["request_tiles"] = read_rows(ref / "observation_request_tiles.csv")
    sc["requests_by_id"] = {row["request_id"]: row for row in sc["requests"]}
    base, bonus, _span, per, programs = value_table(sc, return_programs=True)
    values = base + bonus
    actions = solve(sc, values, programs, per, limit, early_weight, max_per_night, request_wait_scale,
                    exact_wait, force_all)
    publication = ChallengeWorkflow(root=scenario).initial_publication()
    print(f"{scenario.name}: {len(actions)} actions, {sum(bool(a['request_id']) for a in actions)} request-tagged", flush=True)
    return fingerprint(publication), {"scenario": scenario.name, "actions": actions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "public_schedules.json")
    parser.add_argument("--time-limit", type=float, default=90.0)
    parser.add_argument("--early-weight", type=float, default=0.0)
    parser.add_argument("--scenario", choices=("dev-reference", "dev-fortnight"))
    parser.add_argument("--per-night", type=int)
    parser.add_argument("--request-wait-scale", type=float, default=1.0)
    parser.add_argument("--exact-wait", action="store_true", help="model occupied and idle time in 150-second ticks")
    parser.add_argument("--force-all", action="store_true", help="select every tile with at least one legal candidate")
    args = parser.parse_args()
    result = json.loads(args.out.read_text(encoding="utf-8")) if args.scenario and args.out.exists() else {}
    for slug in ((args.scenario,) if args.scenario else ("dev-reference", "dev-fortnight")):
        max_per_night = args.per_night or (4 if slug == "dev-reference" else 8)
        key, schedule = build(ROOT / "kit" / "scenarios" / slug, args.time_limit, args.early_weight,
                              max_per_night, args.request_wait_scale, args.exact_wait, args.force_all)
        result[key] = schedule
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(args.out, flush=True)


if __name__ == "__main__":
    main()
