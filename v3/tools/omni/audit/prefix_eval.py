"""前缀评分：按官方 score_files 的真实语义（决策用完后直接 finalize，不消耗剩余时间）逐前缀计分，单遍增量。

    .venv/bin/python tools/omni/audit/prefix_eval.py kit/scenarios/dev-reference dist/omni/dev-reference-decisions.csv

与 tools/omni/model.py 的差别只有一处：决策用完后不再把剩余时间按等待消耗，而是以当前游标为 final_time 结算请求
（issued_at > final_time 的请求跳过；deadline > final_time 的未完成请求不罚）。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]  # tools/omni/audit -> 仓库根目录
sys.path.insert(0, str(ROOT / "tools" / "omni"))
from model import COMPLETED, GEOMETRY, OUTSIDE, TICK, UNSAFE, Scenario, format_utc, parse_utc


def prefix_scores(sc: Scenario, decisions):
    """返回每个前缀 m=0..len 的官方总分数组，以及全程按 model.py 语义的总分。"""
    per, n = sc.per, sc.n_ticks
    rate_invalid = float(sc.score_config["penalties"]["invalid_action"])
    rate_unsafe = float(sc.score_config["penalties"]["unsafe_observation"])
    rate_wait = float(sc.score_config["penalties"]["avoidable_wait_per_second"])
    req_pen_required = float(sc.score_config["penalties"]["required_miss"])
    flex_pen = float(sc.score_config["penalties"]["flexible_shortfall_per_tile"])
    quota = int(sc.score_config["flexible_quota_per_region"])
    regions = sc.regions
    region_idx = np.array([regions.index(r) for r in sc.region])
    reqs = sc.requests
    R = len(reqs)
    req_issued = np.array([parse_utc(r["issued_at_utc"]) for r in reqs])
    req_deadline = np.array([parse_utc(r["deadline_utc"]) for r in reqs])
    req_need = np.array([int(r["required_tile_count"]) for r in reqs])
    req_reward = np.array([float(r["completion_reward"]) for r in reqs])
    req_pen = np.array([float(r["miss_penalty"]) for r in reqs])
    req_excused = np.array([sum(sc.request_opportunity(sc.index[t], r["request_id"]) for t in sc.request_tiles[r["request_id"]])
                            < int(r["required_tile_count"]) for r in reqs])
    rindex = {r["request_id"]: q for q, r in enumerate(reqs)}

    k = 0
    completed = np.zeros(sc.n_tiles, dtype=bool)
    science = 0.0
    penalty = 0.0  # avoidable + invalid + unsafe
    visits: Counter = Counter()
    satisfied = np.zeros(R, dtype=int)
    flex_count = np.zeros(len(regions), dtype=int)
    required_missing = int(sc.required.sum())
    night = np.repeat(sc.night_of_slot, per)
    avoid_by_night = Counter()

    def finalize():
        final_time = float(sc.tick_time[k]) if k < n else float(sc.slot_start[-1]) + per * TICK
        issued = req_issued <= final_time
        done = satisfied >= req_need
        expired = req_deadline <= final_time
        reward = float(req_reward[issued & done].sum())
        miss = float(req_pen[issued & ~done & expired & ~req_excused].sum())
        shortfall = int(np.maximum(0, quota - flex_count).sum())
        return science + reward - penalty - miss - req_pen_required * required_missing - flex_pen * shortfall

    def consume_wait(ticks: int) -> None:
        nonlocal k, penalty
        if (sc.actionable[:, k] & ~completed).any():
            penalty += ticks * TICK * rate_wait
            avoid_by_night[int(night[k])] += ticks * TICK
        k += ticks

    totals = [finalize()]
    ks = [0]
    for decision in decisions:
        if k >= n:
            totals.append(totals[-1]); ks.append(k); continue
        if decision[0] == "wait":
            consume_wait(per - k % per)
        else:
            _, tile, program, request_id = decision
            start = k
            bad = False
            if request_id:
                lo, hi = sc.request_window.get(request_id, (0, 0))
                if (request_id not in sc.request_tiles or sc.tile_ids[tile] not in sc.request_tiles[request_id]
                        or not lo <= k < hi):
                    consume_wait(per - k % per); penalty += rate_invalid; bad = True
            if not bad and completed[tile] and not request_id:
                consume_wait(per - k % per); penalty += rate_invalid; bad = True
            if not bad:
                result = sc.outcome[tile, k]
                if result == UNSAFE:
                    consume_wait(per - k % per); penalty += rate_unsafe
                elif result == OUTSIDE:
                    consume_wait(per - k % per); penalty += rate_invalid
                else:
                    k = int(sc.end[tile, start])
                    if result == GEOMETRY:
                        penalty += rate_invalid
                    elif result == COMPLETED:
                        if not completed[tile]:
                            completed[tile] = True
                            science += float(sc.base[tile, start]) + float(sc.bonus[tile, start, program])
                            if sc.required[tile]:
                                required_missing -= 1
                            else:
                                flex_count[region_idx[tile]] += 1
                        if request_id:
                            key = (request_id, sc.tile_ids[tile])
                            visits[key] += 1
                            if visits[key] == sc.request_tiles[request_id][sc.tile_ids[tile]]:
                                satisfied[rindex[request_id]] += 1
        totals.append(finalize())
        ks.append(k)
    return np.array(totals), np.array(ks), avoid_by_night


if __name__ == "__main__":
    sc = Scenario(Path(sys.argv[1]).resolve())
    decisions = sc.plan_from_decisions_csv(Path(sys.argv[2]))
    totals, ks, avoid_by_night = prefix_scores(sc, decisions)
    full = totals[-1]
    print(f"decisions={len(decisions)} full(official semantics)={full:.6f} model.evaluate={sc.evaluate(decisions)['total']:.6f}")
    best = int(np.argmax(totals))
    print(f"best prefix m={best} total={totals[best]:.6f} (k={ks[best]} {format_utc(sc.tick_time[min(ks[best], sc.n_ticks - 1)])})")
    order = np.argsort(-totals)
    shown = 0
    last = None
    for m in order:
        if last is not None and abs(totals[m] - last) < 1e-9:
            continue
        last = totals[m]
        print(f"  m={int(m):5d} total={totals[m]:.6f} k={ks[m]} t={format_utc(sc.tick_time[min(ks[m], sc.n_ticks - 1)])}")
        shown += 1
        if shown >= 6:
            break
    np.save(Path(__file__).with_name("prefix_totals.npy"), totals)
    print("avoidable wait seconds by night (nonzero), and sum:", sum(avoid_by_night.values()))
    nights = sorted(avoid_by_night)
    line = []
    for nt in nights:
        line.append(f"{nt}:{avoid_by_night[nt]}")
    print(" ".join(line))
