"""把科学观测的开始格变成完整计划：选最优项目、打请求标签、DP 填补空闲、补足缺的请求访问，再精确评分。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fill import Filler
from model import COMPLETED, Scenario


def science_plan(sc: Scenario, chosen):
    """chosen: [(tick, tile)] -> [(tick, tile, program, request_id)]，科学观测落在请求窗口里就打标签（优先需求多的请求）。"""
    plan = []
    for k, i in sorted(chosen):
        program = int(np.argmax(sc.bonus[i, k]))
        tag = ""
        for row in sc.requests:
            rid = row["request_id"]
            a, b = sc.request_window[rid]
            if sc.tile_ids[i] in sc.request_tiles[rid] and a <= k < b:
                tag = rid
                break
        plan.append((int(k), int(i), program, tag))
    return plan


def request_status(sc: Scenario, full):
    visits = {}
    for k, tile, _p, rid, kind in full:
        if rid and sc.outcome[tile, k] == COMPLETED:
            visits.setdefault(rid, set()).add(tile)
    missing = {}
    for row in sc.requests:
        rid = row["request_id"]
        got = len(visits.get(rid, set()))
        need = int(row["required_tile_count"])
        if got < need:
            missing[rid] = (need - got, visits.get(rid, set()))
    return missing


def finish(sc: Scenario, chosen, filler: Filler | None = None, verbose=False):
    filler = filler or Filler(sc)
    plan = science_plan(sc, chosen)
    # 同一请求的标签分配：若科学观测可服务多个请求，选缺口最大的
    forced = []
    for _round in range(12):
        _pen, full = filler.solve(plan, forced)
        missing = request_status(sc, full)
        if not missing:
            break
        # 对每个缺口，尝试把一次重访固定下来：选窗口里代价最小的（逐个试）
        progress = False
        for rid, (need, have) in missing.items():
            a, b = sc.request_window[rid]
            best = None
            end_of = {t: int(sc.end[t, k]) for k, t, _p, _r in plan}
            for tile_id in sc.request_tiles[rid]:
                j = sc.index[tile_id]
                if j in have or j not in end_of:
                    continue
                ks = np.flatnonzero(sc.outcome[j, a:b] == COMPLETED) + a
                ks = ks[ks >= end_of[j]]
                for k in ks[:: max(1, len(ks) // 40)]:
                    trial = forced + [(int(k), j, rid)]
                    try:
                        p2, _ = filler.solve(plan, trial)
                    except RuntimeError:
                        continue
                    if best is None or p2 < best[0]:
                        best = (p2, int(k), j)
            if best is not None:
                forced.append((best[1], best[2], rid))
                progress = True
                break
        if not progress:
            break
    fullplan = [(k, t, p, r) for k, t, p, r, _kind in full]
    result = sc.evaluate(sc.decisions_from_plan(fullplan))
    if verbose:
        print(json.dumps({k: v for k, v in result.items() if k not in ("requests",)}, ensure_ascii=False))
    return result, fullplan


if __name__ == "__main__":
    sc = Scenario(Path(sys.argv[1]).resolve())
    chosen = [tuple(x) for x in json.loads(Path(sys.argv[2]).read_text())]
    result, fullplan = finish(sc, chosen, verbose=True)
    print({k: v for k, v in result["requests"].items() if v != "completed"})
