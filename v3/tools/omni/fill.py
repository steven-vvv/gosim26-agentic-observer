"""给定科学观测（每块天区的首次完成）的开始格，用动态规划精确求出填补空闲的最少空等罚分。

填补手段（都不算等待，所以不计空等罚分）：
- 请求重访：已完成的请求天区、带该请求标签、开始格在请求窗口内；结果为完成或天气中断（几何中断罚 100，不用）
- 注定中断：未完成的天区、从当前格开始会被天气打断（无罚，天区仍未完成）
wait 走到下一个时隙边界，开始时若有未完成且能完成的天区，整段计罚。

请求约束：每个请求在窗口内至少要有 required_tile_count 块天区各被带标签观测一次。DP 本身不跟踪请求，
先求最小罚分，再检查缺的请求，把缺的访问固定下来重求。
"""
from __future__ import annotations

import numpy as np
from model import COMPLETED, TICK, WEATHER, Scenario


class Filler:
    def __init__(self, sc: Scenario):
        self.sc = sc
        per = sc.per
        self.rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
        # 每格可用的请求重访 (tile, request)
        self.revisit = [[] for _ in range(sc.n_ticks)]
        for row in sc.requests:
            request_id = row["request_id"]
            lo, hi = sc.request_window[request_id]
            for tile_id in sc.request_tiles[request_id]:
                j = sc.index[tile_id]
                ok = np.flatnonzero((sc.outcome[j, lo:hi] == COMPLETED) | (sc.outcome[j, lo:hi] == WEATHER)) + lo
                for k in ok:
                    self.revisit[k].append((j, request_id))
        self.doomed = [[] for _ in range(sc.n_ticks)]
        for i, k in zip(*np.nonzero(sc.outcome == WEATHER)):
            self.doomed[k].append(i)
        self.boundary = (np.arange(sc.n_ticks) // per + 1) * per

    def penalized(self, science_end: np.ndarray) -> np.ndarray:
        """P[k]：游标在 k 等待时是否计罚（存在未完成且能完成的天区）。science_end[i] 为天区完成格（未安排则为 n_ticks）。"""
        sc = self.sc
        p = np.zeros(sc.n_ticks, dtype=bool)
        for i in range(sc.n_tiles):
            e = int(science_end[i])
            p[:e] |= sc.actionable[i, :e]
        return p

    def solve(self, plan, forced=()):
        """plan：科学观测 [(tick, tile, program, request_id)]；forced：必须保留的重访 [(tick, tile, request_id)]。

        返回 (最小罚分, 完整计划)。计划里的重访 program 统一用 DARK（重访不计分）。
        """
        sc = self.sc
        n = sc.n_ticks
        end_of = np.full(sc.n_tiles, n, dtype=np.int64)
        fixed_at = {}
        for tick, tile, program, request_id in plan:
            end_of[tile] = int(sc.end[tile, tick])
            fixed_at[tick] = ("science", tile, program, request_id)
        for tick, tile, request_id in forced:
            fixed_at[tick] = ("revisit", tile, 0, request_id)
        p = self.penalized(end_of)
        # 下一个固定开始格
        next_fixed = np.full(n + 1, n, dtype=np.int64)
        blocked = np.zeros(n + 1, dtype=bool)
        for tick, (kind, tile, _program, _request) in fixed_at.items():
            blocked[tick + 1:int(sc.end[tile, tick])] = True
        nxt = n
        for k in range(n, -1, -1):
            if k in fixed_at:
                nxt = k
            next_fixed[k] = nxt
        INF = float("inf")
        cost = np.full(n + 1, INF)
        choice = [None] * (n + 1)
        cost[n] = 0.0
        rate = self.rate
        for k in range(n - 1, -1, -1):
            if blocked[k]:
                continue
            if k in fixed_at:
                kind, tile, program, request_id = fixed_at[k]
                e = int(sc.end[tile, k])
                cost[k] = cost[e]
                choice[k] = (kind, tile, program, request_id, e)
                continue
            limit = next_fixed[k]
            best = INF
            pick = None
            b = int(self.boundary[k])
            if b <= limit:
                c = cost[b] + (rate * (b - k) if p[k] else 0.0)
                if c < best:
                    best, pick = c, ("wait", -1, 0, "", b)
            for j, request_id in self.revisit[k]:
                if end_of[j] > k:
                    continue
                e = int(sc.end[j, k])
                if e <= limit and cost[e] < best:
                    best, pick = cost[e], ("revisit", j, 0, request_id, e)
            for i in self.doomed[k]:
                if end_of[i] <= k:
                    continue
                e = int(sc.end[i, k])
                if e <= limit and cost[e] < best:
                    best, pick = cost[e], ("doomed", i, 0, "", e)
            cost[k] = best
            choice[k] = pick
        full = []
        k = 0
        while k < n:
            pick = choice[k]
            if pick is None:
                raise RuntimeError(f"no path at tick {k}")
            kind, tile, program, request_id, e = pick
            if kind != "wait":
                full.append((k, tile, program, request_id, kind))
            k = e
        return float(cost[0]), full
