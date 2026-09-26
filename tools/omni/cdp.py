"""dp.c 的 ctypes 封装：给定每块天区的科学观测开始格，精确求填补后的总分（含请求补访）。"""
from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path

import numpy as np
from model import COMPLETED, TICK, WEATHER, Scenario

HERE = Path(__file__).resolve().parent
LIB = HERE / "dp.so"


def _compile() -> None:
    src = HERE / "dp.c"
    if not LIB.exists() or LIB.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["gcc", "-O3", "-march=native", "-shared", "-fPIC", "-o", str(LIB), str(src)], check=True)


class Problem(ctypes.Structure):
    _fields_ = [
        ("n_ticks", ctypes.c_int), ("per", ctypes.c_int), ("n_tiles", ctypes.c_int), ("n_req", ctypes.c_int),
        ("act", ctypes.c_void_p), ("outcome", ctypes.c_void_p), ("endt", ctypes.c_void_p), ("value", ctypes.c_void_p),
        ("rv_ptr", ctypes.c_void_p), ("rv_tile", ctypes.c_void_p), ("rv_req", ctypes.c_void_p),
        ("dm_ptr", ctypes.c_void_p), ("dm_tile", ctypes.c_void_p),
        ("req_lo", ctypes.c_void_p), ("req_hi", ctypes.c_void_p), ("req_need", ctypes.c_void_p),
        ("req_mask", ctypes.c_void_p), ("req_reward", ctypes.c_void_p), ("req_pen", ctypes.c_void_p),
        ("req_excused", ctypes.c_void_p), ("required", ctypes.c_void_p), ("region", ctypes.c_void_p),
        ("n_regions", ctypes.c_int), ("flex_quota", ctypes.c_int),
        ("rate", ctypes.c_double), ("required_pen", ctypes.c_double), ("flex_pen", ctypes.c_double),
    ]


class CDP:
    def __init__(self, sc: Scenario):
        _compile()
        self.lib = ctypes.CDLL(str(LIB))
        self.lib.dp_work_new.restype = ctypes.c_void_p
        self.lib.dp_work_new.argtypes = [ctypes.POINTER(Problem)]
        self.lib.dp_evaluate.restype = ctypes.c_double
        self.lib.dp_evaluate.argtypes = [ctypes.POINTER(Problem), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.lib.dp_path.restype = ctypes.c_int
        self.lib.dp_path.argtypes = [ctypes.POINTER(Problem), ctypes.c_void_p] + [ctypes.c_void_p] * 4 + [ctypes.c_int]
        self.sc = sc
        n, T = sc.n_ticks, sc.n_tiles
        assert T <= 64
        keep = {}
        act = np.zeros(n, dtype=np.uint64)
        for i in range(T):
            act |= sc.actionable[i].astype(np.uint64) << np.uint64(i)
        keep["act"] = act
        keep["outcome"] = np.ascontiguousarray(sc.outcome, dtype=np.int8)
        keep["endt"] = np.ascontiguousarray(sc.end, dtype=np.int32)
        value = np.where(sc.outcome == COMPLETED, sc.base + sc.bonus.max(axis=2), -1e30)
        keep["value"] = np.ascontiguousarray(value, dtype=np.float64)
        self.value = value
        # 重访
        per_tick = [[] for _ in range(n)]
        self.req_ids = [row["request_id"] for row in sc.requests]
        for r, row in enumerate(sc.requests):
            rid = row["request_id"]
            a, b = sc.request_window[rid]
            for tile_id in sc.request_tiles[rid]:
                j = sc.index[tile_id]
                for k in np.flatnonzero((sc.outcome[j, a:b] == COMPLETED) | (sc.outcome[j, a:b] == WEATHER)) + a:
                    per_tick[k].append((j, r))
        ptr = np.zeros(n + 1, dtype=np.int32)
        tiles, reqs = [], []
        for k in range(n):
            ptr[k] = len(tiles)
            for j, r in per_tick[k]:
                tiles.append(j)
                reqs.append(r)
        ptr[n] = len(tiles)
        keep["rv_ptr"], keep["rv_tile"], keep["rv_req"] = ptr, np.array(tiles, dtype=np.int32), np.array(reqs, dtype=np.int32)
        dm = [[] for _ in range(n)]
        for i, k in zip(*np.nonzero(sc.outcome == WEATHER)):
            dm[k].append(i)
        ptr = np.zeros(n + 1, dtype=np.int32)
        tiles = []
        for k in range(n):
            ptr[k] = len(tiles)
            tiles.extend(dm[k])
        ptr[n] = len(tiles)
        keep["dm_ptr"], keep["dm_tile"] = ptr, np.array(tiles, dtype=np.int32)
        lo, hi, need, mask, reward, pen, excused = [], [], [], [], [], [], []
        for row in sc.requests:
            rid = row["request_id"]
            a, b = sc.request_window[rid]
            lo.append(a)
            hi.append(b)
            need.append(int(row["required_tile_count"]))
            m = 0
            for tile_id in sc.request_tiles[rid]:
                m |= 1 << sc.index[tile_id]
            mask.append(m)
            reward.append(float(row["completion_reward"]))
            pen.append(float(row["miss_penalty"]))
            feasible = sum(sc.request_opportunity(sc.index[t], rid) for t in sc.request_tiles[rid])
            excused.append(1 if feasible < int(row["required_tile_count"]) else 0)
        keep["req_lo"] = np.array(lo, dtype=np.int32)
        keep["req_hi"] = np.array(hi, dtype=np.int32)
        keep["req_need"] = np.array(need, dtype=np.int32)
        keep["req_mask"] = np.array(mask, dtype=np.uint64)
        keep["req_reward"] = np.array(reward, dtype=np.float64)
        keep["req_pen"] = np.array(pen, dtype=np.float64)
        keep["req_excused"] = np.array(excused, dtype=np.int8)
        keep["required"] = sc.required.astype(np.int8)
        regions = sorted(set(sc.region))
        keep["region"] = np.array([regions.index(r) for r in sc.region], dtype=np.int32)
        self.keep = keep
        P = Problem()
        P.n_ticks, P.per, P.n_tiles, P.n_req = n, sc.per, T, len(sc.requests)
        for name, arr in keep.items():
            setattr(P, name, arr.ctypes.data)
        P.n_regions = len(regions)
        P.flex_quota = int(sc.score_config["flexible_quota_per_region"])
        P.rate = float(sc.score_config["penalties"]["avoidable_wait_per_second"]) * TICK
        P.required_pen = float(sc.score_config["penalties"]["required_miss"])
        P.flex_pen = float(sc.score_config["penalties"]["flexible_shortfall_per_tile"])
        self.P = P
        self.work = self.lib.dp_work_new(ctypes.byref(P))
        self.info = np.zeros(8)
        self.buf = {name: np.zeros(n + 1, dtype=np.int32) for name in ("kind", "tick", "tile", "req")}

    def evaluate(self, sci: np.ndarray, max_fix: int = 8) -> float:
        sci = np.ascontiguousarray(sci, dtype=np.int32)
        return self.lib.dp_evaluate(ctypes.byref(self.P), self.work, sci.ctypes.data, max_fix, self.info.ctypes.data)

    def path(self):
        b = self.buf
        m = self.lib.dp_path(ctypes.byref(self.P), self.work, b["kind"].ctypes.data, b["tick"].ctypes.data,
                             b["tile"].ctypes.data, b["req"].ctypes.data, len(b["kind"]))
        return [(int(b["kind"][q]), int(b["tick"][q]), int(b["tile"][q]), int(b["req"][q])) for q in range(m)]

    def plan(self, sci: np.ndarray, max_fix: int = 8):
        """评估并返回完整计划 [(tick, tile, program, request_id)]（重访的标签按需要重新分配）。"""
        total = self.evaluate(sci, max_fix)
        sc = self.sc
        steps = [s for s in self.path() if s[0] != 0]
        # 标签分配：先统计每个请求还缺的天区，按时间顺序给能服务的观测分配
        need = {rid: int(sc.request_by_id[rid]["required_tile_count"]) for rid in self.req_ids}
        got = {rid: set() for rid in self.req_ids}
        plan = []
        for kind, k, tile, _r in steps:
            program = int(np.argmax(sc.bonus[tile, k])) if kind == 1 else 0
            tag = ""
            if kind in (1, 2, 3) and sc.outcome[tile, k] == COMPLETED:
                options = []
                for rid in self.req_ids:
                    a, b = sc.request_window[rid]
                    if a <= k < b and sc.tile_ids[tile] in sc.request_tiles[rid]:
                        options.append(rid)
                useful = [rid for rid in options if tile not in got[rid] and len(got[rid]) < need[rid]]
                if useful:
                    tag = min(useful, key=lambda rid: (sc.request_window[rid][1], self.req_ids.index(rid)))
                elif options:
                    tag = options[0]
                if tag:
                    got[tag].add(tile)
            elif kind == 3:
                # 天气中断的重访：标签只需合法
                for rid in self.req_ids:
                    a, b = sc.request_window[rid]
                    if a <= k < b and sc.tile_ids[tile] in sc.request_tiles[rid]:
                        tag = rid
                        break
            plan.append((k, tile, program, tag))
        return total, plan
