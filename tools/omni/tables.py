"""全知排程用的精确结果表：逐块天区、逐个 150 秒开始格，按官方计分器的代码路径算出一次观测的结果。

    uv run python tools/omni/tables.py kit/scenarios/dev-reference [--jobs 4]

输出 runs/omni/<场景>/tables.npz：
- outcome[i, k]：0 不安全（开始时天气不可观测，罚 2000）；1 开始时不合法（outside_tile_window，罚 100 并空耗到时隙末）；
  2 完成；3 天气中断（无罚）；4 几何或跨夜中断（罚 100）
- end[i, k]：观测结束后的游标格；base[i, k]、bonus[i, k, p]：完成时的基础分与三种项目的加成（p 按 DARK/BRIGHT/BACKUP）
- actionable[i, k]：官方 _can_complete_from（空等判罚与请求豁免用的“能否完成”，要求每段开始和中点都合法）

只适用于旧版计分（没有异常机制）。几何和有效天气直接调用 kit 函数并按 (天区, 时刻) 缓存，浮点结果与官方一致。
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "kit"))

TICK = 150
PROGRAMS = ("DARK", "BRIGHT", "BACKUP")
UNSAFE, OUTSIDE, COMPLETED, WEATHER, GEOMETRY = range(5)


def make_scorer(scenario: Path):
    from challenge.scoring_core import ChallengeScorer

    scorer = ChallengeScorer.from_files(scenario)
    if scorer.mechanics:
        raise SystemExit("tables.py 只支持旧版计分（无异常机制）")
    geo_cache: dict = {}
    original_geometry = scorer.geometry.get_tile_geometry

    def get_tile_geometry(tile_id, moment):
        key = (tile_id, moment)
        value = geo_cache.get(key)
        if value is None:
            value = original_geometry(tile_id, moment)
            geo_cache[key] = value
        return value

    scorer.geometry.get_tile_geometry = get_tile_geometry
    cond_cache: dict = {}
    original_conditions = scorer.weather.get_effective_conditions

    def get_effective_conditions(slot_id, tile_id=None, **kwargs):
        key = (slot_id, tile_id, tuple(sorted(kwargs.items())))
        value = cond_cache.get(key)
        if value is None:
            value = original_conditions(slot_id, tile_id, **kwargs)
            cond_cache[key] = value
        return value

    scorer.weather.get_effective_conditions = get_effective_conditions
    return scorer


def build_rows(args):
    """在子进程里算一组天区的全部开始格。"""
    scenario, tile_ids = args
    from challenge.weather_simulator import weather_quality

    scorer = make_scorer(Path(scenario))
    slots = scorer.slots
    per = int(slots[0].duration_seconds) // TICK
    n_ticks = len(slots) * per
    rates = [float(scorer.config["program_bonus"][p]) for p in PROGRAMS]
    wconfig = scorer.weather.config
    out = {}
    for tile_id in tile_ids:
        tile = scorer.tiles[tile_id]
        value = scorer.tile_values[tile_id]
        anomaly = scorer._anomaly_factor(tile_id)
        exptime = tile.nominal_exptime_seconds
        outcome = np.zeros(n_ticks, dtype=np.int8)
        end = np.zeros(n_ticks, dtype=np.int32)
        base_arr = np.zeros(n_ticks, dtype=np.float64)
        bonus_arr = np.zeros((n_ticks, 3), dtype=np.float64)
        actionable = np.zeros(n_ticks, dtype=bool)
        for s, slot in enumerate(slots):
            for o in range(per):
                k = s * per + o
                offset = o * TICK
                actionable[k] = scorer._can_complete_from(tile, s, offset)
                started = slot.timestamp_utc + timedelta(seconds=offset)
                if not scorer.weather.get_effective_conditions(slot.slot_id, tile_id)["is_observable"]:
                    outcome[k] = UNSAFE
                    end[k] = k
                    continue
                if not scorer._tile_legal(tile, started):
                    outcome[k] = OUTSIDE
                    end[k] = (s + 1) * per
                    continue
                remaining = exptime
                cur_s, cur_o = s, offset
                pending_base = 0.0
                pending = [0.0, 0.0, 0.0]
                result = COMPLETED
                while remaining > 0:
                    if cur_s >= len(slots):
                        result = GEOMETRY
                        break
                    cur = slots[cur_s]
                    moment = cur.timestamp_utc + timedelta(seconds=cur_o)
                    if cur.night_id != slot.night_id or not scorer._tile_legal(tile, moment):
                        result = GEOMETRY
                        break
                    conditions = scorer.weather.get_effective_conditions(cur.slot_id, tile_id)
                    if not conditions["is_observable"]:
                        result = WEATHER
                        break
                    seconds = min(remaining, cur.duration_seconds - cur_o)
                    midpoint = moment + timedelta(seconds=seconds / 2)
                    geometry = scorer.geometry.get_tile_geometry(tile_id, midpoint)
                    atmospheric = weather_quality(conditions, float(geometry["airmass"]), wconfig)
                    lunar = float(geometry["lunar_quality_factor"])
                    combined = atmospheric * lunar
                    band = scorer._quality_band(atmospheric * lunar)
                    base = value * seconds / exptime * combined * anomaly
                    pending_base += base
                    for p, program in enumerate(PROGRAMS):
                        pending[p] += base * (rates[p] if program == band else 0.0)
                    cur_o += seconds
                    if cur_o == cur.duration_seconds:
                        cur_s += 1
                        cur_o = 0
                    remaining -= seconds
                outcome[k] = result
                end[k] = cur_s * per + cur_o // TICK
                if result == COMPLETED:
                    base_arr[k] = pending_base
                    bonus_arr[k] = pending
        out[tile_id] = (outcome, end, base_arr, bonus_arr, actionable)
    return out


def build(scenario: Path, jobs: int) -> Path:
    scorer = make_scorer(scenario)
    tile_ids = sorted(scorer.tiles)
    slots = scorer.slots
    assert all(slot.duration_seconds == slots[0].duration_seconds for slot in slots)
    per = int(slots[0].duration_seconds) // TICK
    chunks = [tile_ids[j::jobs] for j in range(jobs)]
    results = {}
    with ProcessPoolExecutor(jobs) as pool:
        for part in pool.map(build_rows, [(str(scenario), chunk) for chunk in chunks]):
            results.update(part)
    n_ticks = len(slots) * per
    n = len(tile_ids)
    outcome = np.zeros((n, n_ticks), dtype=np.int8)
    end = np.zeros((n, n_ticks), dtype=np.int32)
    base = np.zeros((n, n_ticks))
    bonus = np.zeros((n, n_ticks, 3))
    actionable = np.zeros((n, n_ticks), dtype=bool)
    for i, tile_id in enumerate(tile_ids):
        outcome[i], end[i], base[i], bonus[i], actionable[i] = results[tile_id]
    nights = sorted({slot.night_id for slot in slots})
    night_of_slot = np.array([nights.index(slot.night_id) for slot in slots], dtype=np.int32)
    slot_start = np.array([slot.timestamp_utc.timestamp() for slot in slots], dtype=np.int64)
    out = ROOT / "runs" / "omni" / scenario.name / "tables.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, tile_ids=np.array(tile_ids), outcome=outcome, end=end, base=base, bonus=bonus,
                        actionable=actionable, night_of_slot=night_of_slot, slot_start=slot_start,
                        slot_ids=np.array([slot.slot_id for slot in slots]), per=per)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    print(build(args.scenario.resolve(), args.jobs))


if __name__ == "__main__":
    main()
