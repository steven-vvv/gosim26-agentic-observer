"""全知排程的场景模型与精确评分（旧版计分器的逐位复刻，建立在 tables.py 的结果表上）。

决策序列的写法与平台协议相同：每一步在当前游标上 wait 或 observe。计划（plan）是按开始格排好的观测列表
[(tick, tile, program, request_id)]，两次观测之间自动用 wait 补齐；wait 只能走到下一个时隙边界，
所以时隙中间开始的观测必须紧接在上一段观测之后。
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TICK = 150
PROGRAMS = ("DARK", "BRIGHT", "BACKUP")
UNSAFE, OUTSIDE, COMPLETED, WEATHER, GEOMETRY = range(5)


def parse_utc(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def format_utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class Scenario:
    def __init__(self, scenario: Path):
        self.root = scenario
        self.name = scenario.name
        data = np.load(ROOT / "runs" / "omni" / scenario.name / "tables.npz")
        self.tile_ids = [str(x) for x in data["tile_ids"]]
        self.index = {tile_id: i for i, tile_id in enumerate(self.tile_ids)}
        self.outcome = data["outcome"]
        self.end = data["end"]
        self.base = data["base"]
        self.bonus = data["bonus"]
        self.actionable = data["actionable"]
        self.night_of_slot = data["night_of_slot"]
        self.slot_start = data["slot_start"]
        self.slot_ids = [str(x) for x in data["slot_ids"]]
        self.per = int(data["per"])
        self.n_tiles, self.n_ticks = self.outcome.shape
        self.n_slots = len(self.slot_ids)
        self.tick_time = (np.repeat(self.slot_start, self.per) + np.tile(np.arange(self.per) * TICK, self.n_slots))
        ref = scenario / "outputs" / "reference"
        self.tiles = {row["tile_id"]: row for row in rows(ref / "tiles.csv")}
        self.exptime = np.array([int(self.tiles[t]["nominal_exptime_seconds"]) for t in self.tile_ids])
        self.required = np.array([self.tiles[t]["scheduling_class"] == "REQUIRED" for t in self.tile_ids])
        self.region = [self.tiles[t]["region_id"] for t in self.tile_ids]
        self.regions = sorted(set(self.region))
        self.score_config = json.loads((scenario / "config" / "score_config.json").read_text(encoding="utf-8"))
        self.requests = sorted(rows(ref / "observation_requests.csv"), key=lambda row: row["request_id"])
        self.request_tiles: dict[str, dict[str, int]] = {}
        for row in rows(ref / "observation_request_tiles.csv"):
            self.request_tiles.setdefault(row["request_id"], {})[row["tile_id"]] = int(row["required_visits"])
        self.request_by_id = {row["request_id"]: row for row in self.requests}
        # 请求窗口换成开始格区间 [lo, hi)：开始时刻 t 满足 available_from <= t < deadline
        self.request_window = {}
        for row in self.requests:
            lo = int(np.searchsorted(self.tick_time, parse_utc(row["available_from_utc"]), side="left"))
            hi = int(np.searchsorted(self.tick_time, parse_utc(row["deadline_utc"]), side="left"))
            self.request_window[row["request_id"]] = (lo, hi)

    # ------------------------------------------------------------------ 评分

    def request_opportunity(self, tile: int, request_id: str) -> bool:
        """官方 _tile_has_request_opportunity：窗口内某个时隙开头能完成且不越过截止时刻。"""
        row = self.request_by_id[request_id]
        start, deadline = parse_utc(row["available_from_utc"]), parse_utc(row["deadline_utc"])
        for s in range(self.n_slots):
            t = self.slot_start[s]
            if t < start:
                continue
            if t >= deadline:
                break
            if t + self.exptime[tile] > deadline:
                continue
            if self.actionable[tile, s * self.per]:
                return True
        return False

    def evaluate(self, decisions, detail: bool = False) -> dict:
        """decisions: [("wait",) | ("observe", tile, program, request_id)]，逐条按官方规则计分；游标到survey末尾后多余的决策忽略。"""
        per = self.per
        k = 0
        completed = np.zeros(self.n_tiles, dtype=bool)
        incomplete_actionable = self.actionable  # 只用来按列查
        base_total = 0.0
        bonus_total = 0.0
        penalties: Counter = Counter()
        visits: Counter = Counter()
        wait = Counter()
        rate_invalid = float(self.score_config["penalties"]["invalid_action"])
        rate_unsafe = float(self.score_config["penalties"]["unsafe_observation"])
        rate_wait = float(self.score_config["penalties"]["avoidable_wait_per_second"])
        log = [] if detail else None

        def consume_wait(ticks: int, category: str) -> None:
            nonlocal k
            seconds = ticks * TICK
            avoidable = bool((incomplete_actionable[:, k] & ~completed).any())
            wait[category] += seconds
            wait["avoidable" if avoidable else "unavailable"] += seconds
            if avoidable:
                penalties["avoidable_wait"] += seconds * rate_wait
            k += ticks

        def invalid(kind: str) -> None:
            consume_wait(per - k % per, "invalid")
            penalties[kind] += rate_unsafe if kind == "unsafe_observation" else rate_invalid

        for decision in decisions:
            if k >= self.n_ticks:
                break
            if decision[0] == "wait":
                if log is not None:
                    log.append((k, "wait"))
                consume_wait(per - k % per, "explicit")
                continue
            _, tile, program, request_id = decision
            start = k
            if request_id:
                lo, hi = self.request_window.get(request_id, (0, 0))
                if (request_id not in self.request_tiles or self.tile_ids[tile] not in self.request_tiles[request_id]
                        or not lo <= k < hi):
                    invalid("invalid_action")
                    if log is not None:
                        log.append((start, "invalid_request_tag", tile))
                    continue
            if completed[tile] and not request_id:
                invalid("invalid_action")
                if log is not None:
                    log.append((start, "duplicate_tile", tile))
                continue
            result = self.outcome[tile, k]
            if result == UNSAFE:
                invalid("unsafe_observation")
                if log is not None:
                    log.append((start, "unsafe", tile))
                continue
            if result == OUTSIDE:
                invalid("invalid_action")
                if log is not None:
                    log.append((start, "outside", tile))
                continue
            k = int(self.end[tile, start])
            if result == GEOMETRY:
                penalties["invalid_action"] += rate_invalid
            elif result == COMPLETED:
                if not completed[tile]:
                    completed[tile] = True
                    base_total += float(self.base[tile, start])
                    bonus_total += float(self.bonus[tile, start, program])
                if request_id:
                    visits[(request_id, self.tile_ids[tile])] += 1
            if log is not None:
                log.append((start, ("", "", "completed", "weather_interrupted", "geometry_interrupted")[result], tile))
        while k < self.n_ticks:
            consume_wait(per - k % per, "explicit")
        required_missing = [self.tile_ids[i] for i in range(self.n_tiles) if self.required[i] and not completed[i]]
        penalties["required_miss"] = len(required_missing) * float(self.score_config["penalties"]["required_miss"])
        flexible = Counter(self.region[i] for i in range(self.n_tiles) if completed[i] and not self.required[i])
        quota = int(self.score_config["flexible_quota_per_region"])
        shortfall = {region: max(0, quota - flexible[region]) for region in self.regions}
        penalties["flexible_shortfall"] = sum(shortfall.values()) * float(self.score_config["penalties"]["flexible_shortfall_per_tile"])
        request_reward = 0.0
        request_penalty = 0.0
        statuses = {}
        for row in self.requests:
            request_id = row["request_id"]
            needed = self.request_tiles[request_id]
            satisfied = sum(visits[(request_id, tile_id)] >= count for tile_id, count in needed.items())
            done = satisfied >= int(row["required_tile_count"])
            if done:
                request_reward += float(row["completion_reward"])
                statuses[request_id] = "completed"
                continue
            feasible = sum(self.request_opportunity(self.index[tile_id], request_id) for tile_id in needed)
            if feasible < int(row["required_tile_count"]):
                statuses[request_id] = "excused_unobservable"
            else:
                statuses[request_id] = "missed"
                request_penalty += float(row["miss_penalty"])
        penalties["request_miss"] = request_penalty
        counts = [float(sum(1 for i in range(self.n_tiles) if completed[i] and self.region[i] == r)) for r in self.regions]
        evenness = sum(counts) ** 2 / (len(counts) * sum(c * c for c in counts)) if sum(counts) else 0.0
        subtotal = base_total + bonus_total + request_reward
        total = round(subtotal - sum(penalties.values()), 6)
        result = {"total": total, "base_science": round(base_total, 6), "program_bonus": round(bonus_total, 6),
                  "request_reward": request_reward, "penalties": {key: round(v, 6) for key, v in sorted(penalties.items())},
                  "completed_tiles": int(completed.sum()), "required_missing": required_missing, "evenness": round(evenness, 6),
                  "wait_seconds": dict(sorted(wait.items())), "requests": statuses}
        if log is not None:
            result["log"] = log
        return result

    # ------------------------------------------------------------------ 计划与决策

    def decisions_from_plan(self, plan) -> list:
        """plan: [(tick, tile, program, request_id)]，按 tick 排好。补 wait，返回决策序列；无法到达的开始格抛错。"""
        per = self.per
        out = []
        k = 0
        for tick, tile, program, request_id in plan:
            while k < tick:
                nxt = (k // per + 1) * per
                if nxt > tick:
                    raise ValueError(f"tick {tick} 不在时隙边界上，也不紧接上一段观测（游标 {k}）")
                out.append(("wait",))
                k = nxt
            if k != tick:
                raise ValueError(f"观测重叠：游标 {k} 已越过计划开始格 {tick}")
            out.append(("observe", tile, program, request_id))
            result = self.outcome[tile, tick]
            if result in (UNSAFE, OUTSIDE):
                k = (k // per + 1) * per
            else:
                k = int(self.end[tile, tick])
        return out

    def plan_from_decisions_csv(self, path: Path) -> list:
        """把平台 decisions.csv 还原成决策序列（用于和官方评分对照）。"""
        decisions = []
        for row in rows(path):
            if row["action"] == "wait":
                decisions.append(("wait",))
            elif row["action"] == "observe":
                decisions.append(("observe", self.index[row["tile_id"]], PROGRAMS.index(row["program"]), row["request_id"]))
        return decisions

    def schedule_actions(self, plan) -> list[dict]:
        """转成 agents/public_once/public_schedules.json 的动作格式。"""
        return [{"timestamp_utc": format_utc(float(self.tick_time[tick])), "tile_id": self.tile_ids[tile],
                 "program": PROGRAMS[program], "request_id": request_id} for tick, tile, program, request_id in plan]

    def plan_from_actions(self, actions: list[dict]) -> list:
        lookup = {int(t): i for i, t in enumerate(self.tick_time)}
        return [(lookup[int(parse_utc(a["timestamp_utc"]))], self.index[a["tile_id"]], PROGRAMS.index(a["program"]),
                 a.get("request_id", "")) for a in actions]


if __name__ == "__main__":
    sc = Scenario(Path(sys.argv[1]).resolve())
    decisions = sc.plan_from_decisions_csv(Path(sys.argv[2]))
    print(json.dumps({k: v for k, v in sc.evaluate(decisions).items() if k != "requests"}, ensure_ascii=False))
