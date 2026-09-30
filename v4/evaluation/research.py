"""Research-only paired runs with truth-informed oracles; never part of the submission.

Oracles read the card's truth folder to bound what better inference could gain. They
wrap the participant policy and only replace its action, so every variant runs through
the same pinned engine and summary format as ``batch``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import multiprocessing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from v4.agent.observer4.config import load
from v4.agent.observer4.policy import Observer

from .generate import specifications
from .run import evaluate, source_sha256
from .upstream import ROOT, resolve


def _utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def fault_starts(card: Path) -> list[float]:
    with (card / "truth" / "v4_events.csv").open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if row["event_type"] == "instrument_fault"]
    return sorted(_utc(row["actual_start_utc"]) for row in rows)


def terrain_sectors(card: Path) -> list[tuple[float, float, float]]:
    with (card / "truth" / "v4_events.csv").open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if row["event_type"] == "terrain_obstruction"]
    return [(float(r["azimuth_start_deg"]), float(r["azimuth_end_deg"]), float(r["max_altitude_deg"])) for r in rows]


def oracle_terrain(observer: Observer, sectors) -> None:
    """Replace the announced-terrain envelope by the true permanent sectors."""
    quality = observer.quality
    original = quality.direction_factors

    def direction_factors(altitude, azimuth):
        saved = quality.terrain
        quality.terrain = set()
        try:
            factor, clean = original(altitude, azimuth)
        finally:
            quality.terrain = saved
        alt, az = np.asarray(altitude), np.asarray(azimuth)
        for start, end, top in sectors:
            inside = (az >= start) & (az <= end) if start <= end else (az >= start) | (az <= end)
            blocked = inside & (alt <= top)
            factor = np.where(blocked, 0.0, factor)
            clean = clean & ~blocked
        return factor, clean

    quality.direction_factors = direction_factors


class OracleFault(Observer):
    """Report at the first decision at least ``delay`` seconds after each true fault start."""

    def __init__(self, publication, config, emit=None, *, starts=(), delay=0.0):
        super().__init__(publication, config, emit)
        self._oracle_times = [start + delay for start in starts]
        self._oracle_done = 0

    def respond(self, snapshot):
        action = super().respond(snapshot)
        now = _utc(snapshot["now_utc"])
        due = self._oracle_done < len(self._oracle_times) and now >= self._oracle_times[self._oracle_done]
        if due and self.task.current_night(now) is not None:
            self._oracle_done += 1
            self.pending = None
            self.quality.reported(now)
            return {"action": "report"}
        return action


VARIANTS = {
    "default": ({}, None),
    "no-report": ({"fault": {"enabled": False}}, None),
    "oracle-fault": ({"fault": {"enabled": False}}, ("fault", 0.0)),
    "oracle-fault-1n": ({"fault": {"enabled": False}}, ("fault", 86400.0)),
    "oracle-fault-3n": ({"fault": {"enabled": False}}, ("fault", 3 * 86400.0)),
    "oracle-terrain": ({}, ("terrain", 0.0)),
}


def variant_config(name: str) -> tuple[dict, tuple | None]:
    if name in VARIANTS:
        patch, oracle = VARIANTS[name]
    else:
        patch = json.loads((ROOT / "v4" / "experiments" / f"{name}.json").read_text(encoding="utf-8"))
        oracle = None
    return load(ROOT / "v4" / "agent" / "config.json", patch or None), oracle


def _run(card: Path, output: Path, name: str, source: str) -> dict:
    # Workers are forked after the agent modules were imported, so later edits on disk
    # cannot leak into a running experiment; the parent's source hash is recorded.
    if (output / "summary.json").is_file():
        return json.loads((output / "summary.json").read_text(encoding="utf-8"))
    config, oracle = variant_config(name)
    factory = None
    if oracle is not None and oracle[0] == "fault":
        starts = fault_starts(card)
        factory = lambda publication, record: OracleFault(
            publication, config, record, starts=starts, delay=oracle[1])
    elif oracle is not None and oracle[0] == "terrain":
        sectors = terrain_sectors(card)

        def factory(publication, record):
            observer = Observer(publication, config, record)
            oracle_terrain(observer, sectors)
            return observer
    summary = evaluate(card, output, config, fast=True, factory=factory)
    summary["variant_name"] = name
    summary["source_sha256"] = source
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--indices", default="", help="comma list or a:b range of card indices")
    parser.add_argument("--variants", nargs="+", required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    resolve()
    specs = specifications(args.dataset)
    if args.indices:
        if ":" in args.indices:
            first, last = (int(x) for x in args.indices.split(":"))
            chosen = list(range(first, last))
        else:
            chosen = [int(x) for x in args.indices.split(",")]
        specs = [specs[i] for i in chosen]
    cards = [ROOT / "v4" / "cards" / spec["name"] for spec in specs]
    base = ROOT / "v4" / "results" / "research" / args.tag
    source = source_sha256()
    jobs = [(card, base / name / card.name, name, source) for card in cards for name in args.variants]
    context = multiprocessing.get_context("fork")
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs, mp_context=context) as pool:
        futures = {pool.submit(_run, *job): job for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            card, _, name, _ = futures[future]
            summary = future.result()
            print(json.dumps({"card": card.name, "variant": name, "total": summary["total"],
                              "required_missing": summary["counts"]["required_missing"],
                              "report": summary["components"]["report_settlement"],
                              "policy_seconds": summary["policy_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
