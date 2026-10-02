"""Truth-informed planning on published cards, recorded for exact replay.

Research and practice-board use only: it reads the card's truth folder. The planner keeps
its online structure but receives the true slot weather (efficiency, sky and program bands),
the true terrain and directional events, the fault start (reported at once) and the closed
slots (no exposure runs into one). The recorded responses replay bitwise on the same card,
since the engine is deterministic given the actions.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from v4.agent.observer4.config import load
from v4.agent.observer4.policy import Observer

from .research import _utc, fault_starts, oracle_sectors, oracle_terrain, sector_events, terrain_sectors
from .run import evaluate
from .upstream import ROOT, resolve

SEARCH = {"policy": {"anchors": 24, "anchor_pool": 512, "cluster_anchors": 12, "refine_top": 6,
                     "refine_iterations": 2, "adaptive_durations": True, "refine_centers": True,
                     "pace_budget": False, "level_safety_z": 0.0, "required_safe_factor": 0.505},
          "fault": {"enabled": False}, "model": {"enabled": False}}


class Truth:
    def __init__(self, card: Path):
        rows = list(csv.DictReader((card / "truth" / "v4_weather_truth.csv").open(newline="", encoding="utf-8")))
        self.starts = [_utc(row["timestamp_utc"]) for row in rows]
        self.ends = [start + float(row["duration_seconds"]) for start, row in zip(self.starts, rows)]
        self.open = [row["is_observable"] == "true" for row in rows]
        self.sky = [float(row["transparency"]) * float(row["sky_quality"]) / float(row["seeing_arcsec"])
                    if row["is_observable"] == "true" else 0.0 for row in rows]
        self.efficiency = [float(row["instrument_efficiency"]) if row["is_observable"] == "true" else 0.0 for row in rows]
        faults = [e for e in csv.DictReader((card / "truth" / "v4_events.csv").open(newline="", encoding="utf-8"))
                  if e["event_type"] == "instrument_fault"]
        self.faults = [(_utc(e["actual_start_utc"]), _utc(e["actual_end_utc"]), float(e["instrument_efficiency_multiplier"]))
                       for e in faults]
        self.repaired: set[int] = set()

    def index(self, moment: float) -> int | None:
        i = bisect.bisect_right(self.starts, moment) - 1
        return i if i >= 0 and moment < self.ends[i] else None

    def mean(self, start: float, seconds: float) -> tuple[float, float]:
        """Mean efficiency*sky and sky over [start, start + seconds), closed slots count zero."""
        total_level = total_sky = weight = 0.0
        moment = start
        while moment < start + seconds - 1e-6:
            i = self.index(moment)
            if i is None:
                break
            end = min(self.ends[i], start + seconds)
            span = end - moment
            if self.open[i]:
                efficiency = self.efficiency[i]
                for k, (f0, f1, multiplier) in enumerate(self.faults):
                    if k in self.repaired and f0 <= moment < f1:
                        efficiency = min(1.0, efficiency / multiplier)
                total_level += span * efficiency * self.sky[i]
                total_sky += span * self.sky[i]
            weight += span
            moment = end
        if weight <= 0:
            return 0.0, 0.0
        return total_level / weight, total_sky / weight

    def next_closed(self, moment: float) -> float | None:
        i = self.index(moment)
        while i is not None and i < len(self.starts):
            if not self.open[i]:
                return self.starts[i]
            if i + 1 < len(self.starts) and abs(self.starts[i + 1] - self.ends[i]) > 1:
                return None  # end of the night
            i += 1
        return None


class Omniscient(Observer):
    def __init__(self, publication, config, emit, *, card: Path):
        super().__init__(publication, config, emit)
        self.truth = Truth(card)
        self.fault_times = fault_starts(card)
        self.fault_done = 0
        oracle_terrain(self, terrain_sectors(card))
        oracle_sectors(self, sector_events(card))
        self._now = self.task.start
        self.times = []
        quality = self.quality
        bands = self.task.scoring["program"]["bands"]

        def level_prediction(now, night):
            level, _ = self.truth.mean(now, 900.0)
            quality.instrument = 1.0
            quality.estimate = float(np.clip(level, 0.005, 3.0))
            quality.level_sigma = 0.0
            return quality.estimate, 0.0

        def program_probabilities(base_quality, uncertainty=None):
            _, sky = self.truth.mean(self._now, 900.0)
            q = np.asarray(base_quality) * sky
            dark = (q >= float(bands["DARK"])).astype(float)
            bright = ((q >= float(bands["BRIGHT"])) & (q < float(bands["DARK"]))).astype(float)
            return np.stack((dark, bright, 1.0 - dark - bright))

        quality.level_prediction = level_prediction
        quality.program_probabilities = program_probabilities

    def respond(self, snapshot):
        now = _utc(snapshot["now_utc"])
        self._now = now
        self.times.append(snapshot["now_utc"])
        closed = self.truth.next_closed(now)
        self.planner.horizon_limit = closed
        action = super().respond(snapshot)
        due = self.fault_done < len(self.fault_times) and now >= self.fault_times[self.fault_done]
        if due and self.task.current_night(now) is not None:
            self.truth.repaired.add(self.fault_done)
            self.fault_done += 1
            self.pending = None
            self.quality.reported(now)
            return {"action": "report"}
        i = self.truth.index(now)
        if action.get("action") == "observe" and i is not None and not self.truth.open[i]:
            self.pending = None
            until = datetime.fromtimestamp(self.truth.ends[i], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"action": "wait", "until_utc": until}
        return action


def fingerprint(publication: dict) -> str:
    """Card identity from public initialization content only (targets, calendar, task card)."""
    material = {"task_card": publication.get("task_card"), "nights": publication["survey"]["nights"],
                "targets": publication["targets"]["rows"]}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plan_card(card: Path, out: Path, patch: dict) -> dict:
    config = load(ROOT / "v4" / "agent" / "config.json", patch)
    holder = {}

    def factory(publication, record):
        holder["fingerprint"] = fingerprint(publication)
        holder["agent"] = Omniscient(publication, config, record, card=card)
        return holder["agent"]

    resolve()
    from challenge.v4_workflow import V4Workflow

    # Offline planning may take longer than the card's 900 s; the recorded actions do not
    # depend on the wall clock, and the replay itself takes seconds.
    original = V4Workflow.wallclock_budget
    V4Workflow.wallclock_budget = lambda self, requested: 86400.0
    try:
        summary = evaluate(card, out, config, fast=True, factory=factory)
    finally:
        V4Workflow.wallclock_budget = original
    actions = [json.loads(line) for line in (out / "actions.jsonl").read_text().splitlines()]
    if len(holder["agent"].times) != len(actions):
        raise ValueError("recorded decision times do not match the actions")
    schedule = {"card": card.name, "fingerprint": holder["fingerprint"], "total": summary["total"],
                "times": holder["agent"].times,
                "actions": [{key: value for key, value in action.items()
                             if key not in ("protocol_version", "message_type", "decision_source")} for action in actions]}
    (out / "schedule.json").write_text(json.dumps(schedule, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"card": card.name, "total": summary["total"], "components": summary["components"],
            "decisions": len(actions), "policy_seconds": summary["policy_seconds"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--card", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--patch", type=Path, help="extra experiment patch merged over the omniscient search")
    args = parser.parse_args()
    patch = json.loads(json.dumps(SEARCH))
    if args.patch:
        extra = json.loads(args.patch.read_text(encoding="utf-8"))
        for section, values in extra.items():
            patch.setdefault(section, {}).update(values)
    print(json.dumps(plan_card(args.card, args.out, patch), indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
