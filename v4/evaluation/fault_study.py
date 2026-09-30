"""Compare public-evidence fault rules on fixed-action traces; truth is for scoring only."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from v4.agent.observer4.geometry import parse_utc

from .upstream import ROOT


@dataclass(frozen=True)
class Rule:
    forecast_guard: bool = True
    # A zero threshold means that any forecasted sample blocks a report.
    # Larger values let a sustained, otherwise clean drop outvote one old
    # forecast while keeping the rule explicit for offline experiments.
    forecast_fraction: float = 0.0
    quake_cooldown: bool = True
    recent_band_only: bool = True


RULES = {
    "current": Rule(),
    "ignore_forecast": Rule(forecast_guard=False),
    "post_quake_notice": Rule(quake_cooldown=False),
    "both_guards_relaxed": Rule(forecast_guard=False, quake_cooldown=False),
    "lasting_dark_evidence": Rule(forecast_guard=False, quake_cooldown=False, recent_band_only=False),
}


def first_trigger(trace: dict, dark_threshold: float, rule: Rule, *, minimum: int = 24,
                  confirm_nights: int = 2, drop_threshold: float = 0.60) -> dict | None:
    samples = trace["clean_samples"]
    bands = trace["band_checks"]
    sample_times = [row[0] for row in samples]
    band_times = [row[0] for row in bands]
    decisions = trace["decisions"]
    decision_times = [row["now"] for row in decisions]
    quake_until = float("-inf")
    for decision in trace["decisions"]:
        now, night = decision["now"], decision["night"]
        notices = decision["notices"]
        if any(kind == "earthquake" for kind, _ in notices):
            quake_until = max(quake_until, now + 4 * 86400)
        if (night is None or any(direction == "ALL" for _, direction in notices)
                or (rule.quake_cooldown and now < quake_until)):
            continue
        end = bisect.bisect_right(sample_times, now)
        history = samples[max(0, end - 4000):end]
        if len(history) < 2 * minimum:
            continue
        recent = history[-minimum:]
        nights = {row[1] for row in recent}
        if len(nights) < confirm_nights:
            continue
        if rule.forecast_guard:
            forecasted = 0
            for timestamp, sample_night, _ in recent:
                index = bisect.bisect_right(decision_times, timestamp) - 1
                if index >= 0:
                    forecast_nights = decisions[index].get("forecast_nights", [])
                    if sample_night in forecast_nights:
                        forecasted += 1
            if forecasted / len(recent) > rule.forecast_fraction:
                continue
        baseline = float(np.median([row[2] for row in history[:-minimum]]))
        actual = float(np.median([row[2] for row in recent]))
        drop = actual / max(1e-9, baseline)
        if drop >= drop_threshold:
            continue
        band_end = bisect.bisect_right(band_times, now)
        checked = bands[max(0, band_end - 128):band_end]
        dark = [row[3] for row in checked
                if (not rule.recent_band_only or row[0] >= recent[0][0])
                and row[2] == "DARK" and row[4] * baseline / 0.95 > dark_threshold * 1.1]
        if len(dark) < 4 or sum(dark) < 0.6 * len(dark):
            continue
        return {"now": now, "night": night, "baseline": round(baseline, 6),
                "recent": round(actual, 6), "drop": round(drop, 6),
            "clean_samples": len(recent), "dark_checks": len(dark), "dark_matches": int(sum(dark)),
                "forecasted_samples": forecasted if rule.forecast_guard else 0}
    return None


def study(trace_directory: Path, cards: Path) -> dict:
    traces = sorted(trace_directory.glob("*/quality_trace.json"))
    if not traces:
        raise ValueError("no public quality traces found")
    results = {name: {"suggested_reports": 0, "correct": 0, "false": 0, "settlement": 0, "per_card": []}
               for name in RULES}
    for path in traces:
        trace = json.loads(path.read_text(encoding="utf-8"))
        card = cards / trace["card"]
        scoring = json.loads((card / "config/v4_score_config.json").read_text(encoding="utf-8"))
        with (card / "truth/v4_events.csv").open(newline="", encoding="utf-8") as stream:
            faults = [(parse_utc(row["actual_start_utc"]).timestamp(),
                       parse_utc(row["actual_end_utc"]).timestamp())
                      for row in csv.DictReader(stream) if row["event_type"] == "instrument_fault"]
        for name, rule in RULES.items():
            trigger = first_trigger(trace, float(scoring["program"]["bands"]["DARK"]), rule)
            if trigger is None:
                continue
            correct = any(start <= trigger["now"] < end for start, end in faults)
            result = results[name]
            result["suggested_reports"] += 1
            result["correct" if correct else "false"] += 1
            result["settlement"] += scoring["reporting"]["correct_reward" if correct else "false_penalty"]
            result["per_card"].append({"card": trace["card"], "correct": correct, **trigger})
    return {"schema": "gosim26.v4.fault-study.v1", "fixed_action_cards": len(traces), "rules": results,
            "qualification": "First suggested report only; fixed actions and no simulated repair benefit."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--cards", type=Path, default=ROOT / "v4/cards")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = study(args.traces, args.cards)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
