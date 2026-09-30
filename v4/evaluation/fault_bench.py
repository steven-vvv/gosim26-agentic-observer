"""Offline fault-detector bench on recorded runs (research only).

Rebuilds the public feedback stream of a finished run: every observe action with its
targets, public hit scores and the bulletin in force, plus each target's airmass and
lunar factor at the exposure midpoint. Truth is only used to score detector timing.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from v4.agent.observer4.geometry import airmass, altaz, horizontal_vectors, lunar_factors, sidereal


def _utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def load_stream(card: Path, run: Path) -> dict:
    score = json.loads((card / "config" / "v4_score_config.json").read_text(encoding="utf-8"))
    scenario = json.loads((card / "config" / "v4_scenario.json").read_text(encoding="utf-8"))
    lat, lon = float(scenario["site"]["latitude_deg"]), float(scenario["site"]["longitude_deg"])
    targets = _rows(card / "public" / "targets.csv")
    index = {row["target_id"]: i for i, row in enumerate(targets)}
    ra = np.array([float(row["ra_deg"]) for row in targets])
    dec = np.array([float(row["dec_deg"]) for row in targets])
    flux = np.array([float(row["feature_flux"]) for row in targets])
    weight = np.array([float(row["science_weight"]) for row in targets])
    required = np.array([row["required"] == "true" for row in targets])
    nights = _rows(card / "public" / "v4_night_calendar.csv")
    night_bounds = [(_utc(n["observing_start_utc"]), _utc(n["observing_end_utc"])) for n in nights]
    bulletins = [json.loads(line) for line in (card / "public" / "v4_bulletins.jsonl").read_text().splitlines() if line]
    bulletin_times = np.array([_utc(b["issued_at_utc"]) for b in bulletins])
    actions = [json.loads(line) for line in (run / "actions.jsonl").read_text().splitlines() if line]
    observes = [a for a in actions if a.get("action") == "observe"]
    decisions = [row for row in _rows(run / "decisions.csv") if row["action"] == "observe"]
    reports = [_utc(row["start_utc"]) for row in _rows(run / "decisions.csv") if row["action"] == "report"]
    hits: dict[int, dict[str, float]] = {}
    for row in _rows(run / "observations.csv"):
        hits.setdefault(int(row["observe_index"]), {})[row["target_id"]] = float(row["score"])
    exposures = []
    for row, action in zip(decisions, observes):
        start, end = _utc(row["start_utc"]), _utc(row["end_utc"])
        duration = end - start
        mid = start + duration / 2
        ids = np.array([index[t] for t in action["assignments"].values()], dtype=np.int64)
        alt_mid, az_mid = altaz(horizontal_vectors(ra[ids], dec[ids], sidereal(mid, lon), lat))
        pieces = np.arange(start + 60.0, end, 120.0) if duration > 120 else np.array([mid])
        lunar = np.mean([lunar_factors(ra[ids], dec[ids], t, lat, lon, score.get("lunar_model")) for t in pieces], axis=0)
        k = lunar / (score["q0"] * airmass(alt_mid) ** score["airmass_exponent"])
        b = int(np.searchsorted(bulletin_times, start, side="right") - 1)
        notices = [(n["event_kind"], n["direction"]) for n in bulletins[b]["notices"]] if b >= 0 else []
        night = next((j for j, (s, e) in enumerate(night_bounds) if s <= start < e), None)
        observed = hits.get(int(row["observe_index"]), {})
        exposures.append({
            "start": start, "end": end, "duration": duration, "program": row["program"], "night": night,
            "ids": ids, "k": k, "alt": alt_mid, "az": az_mid,
            "hit": np.array([targets[i]["target_id"] in observed for i in ids]),
            "score": np.array([observed.get(targets[i]["target_id"], 0.0) for i in ids]),
            "notices": notices,
        })
    events = _rows(card / "truth" / "v4_events.csv")
    faults = [(_utc(e["actual_start_utc"]), float(e["instrument_efficiency_multiplier"]))
              for e in events if e["event_type"] == "instrument_fault"]
    quakes = [(_utc(e["actual_start_utc"]), float(e["magnitude"])) for e in events if e["event_type"] == "earthquake"]
    slots = sum(int(n["slot_count"]) for n in nights)
    return {"card": card.name, "score": score, "flux": flux, "weight": weight, "required": required,
            "exposures": exposures, "faults": faults, "quakes": quakes, "reports": reports,
            "night_bounds": night_bounds, "slots": slots, "nights": nights}


SATURATED = (1.2, 1.12, 1.06, 1.0)


def levels(stream: dict, exposure: dict) -> dict:
    """Public per-exposure evidence: unsaturated level z = eff*W'*M and saturated band marks."""
    w = stream["weight"][exposure["ids"]]
    f = stream["flux"][exposure["ids"]]
    ratio = exposure["score"] / w
    saturated = np.zeros(len(ratio), dtype=bool)
    for value in SATURATED:
        saturated |= np.abs(ratio - value) < 2e-5
    positive = exposure["hit"] & (exposure["score"] > 0)
    z = ratio * 450.0 / (f * exposure["duration"] * exposure["k"])
    return {"z": z, "unsaturated": positive & ~saturated, "saturated": positive & saturated,
            "ratio": ratio, "zero_hit": exposure["hit"] & (exposure["score"] <= 0)}


DIRECTIONS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}


def exposure_notices(stream_bulletins, start: float, end: float):
    times, bulletins = stream_bulletins
    first = int(np.searchsorted(times, start, side="right") - 1)
    last = int(np.searchsorted(times, end - 1e-6, side="right") - 1)
    notices = set()
    for b in range(max(first, 0), last + 1):
        notices.update((n["event_kind"], n["direction"]) for n in bulletins[b]["notices"])
    return notices


def run_detector(stream: dict, card: Path, settings: dict, *, stop_at_fault=True) -> dict:
    from v4.agent.observer4.fault import FaultDetector, Record, exposure_level

    bulletins = [json.loads(line) for line in (card / "public" / "v4_bulletins.jsonl").read_text().splitlines() if line]
    times = np.array([_utc(b["issued_at_utc"]) for b in bulletins])
    terrain = {n["direction"] for n in bulletins[0]["notices"] if n["event_kind"] == "terrain_obstruction"}
    score = stream["score"]
    multipliers = score["program"]["multipliers"]
    mismatch = float(score["program"]["mismatch_multiplier"])
    zero_point = float(score["flux_zero_point"]) * float(score["exposure_zero_point_seconds"])
    detector = FaultDetector(settings)
    fault_start = stream["faults"][0][0] if stream["faults"] else float("inf")
    triggers = []
    for e in stream["exposures"]:
        notices = exposure_notices((times, bulletins), e["start"], e["end"])
        signature = frozenset(kind for kind, direction in notices if direction == "ALL")
        clean = np.ones(len(e["ids"]), dtype=bool)
        for kind, direction in notices:
            if direction in DIRECTIONS and kind != "terrain_obstruction":
                near = np.abs((e["az"] - DIRECTIONS[direction] + 180) % 360 - 180) <= 75.0
                clean &= ~(near & (e["alt"] < 82.0))
        for direction in terrain:
            near = np.abs((e["az"] - DIRECTIONS[direction] + 180) % 360 - 180) <= 60.0
            clean &= ~(near & (e["alt"] < 52.0))
        use = e["hit"] & clean
        level, samples, zero = exposure_level(
            e["score"][use], stream["weight"][e["ids"]][use], stream["flux"][e["ids"]][use], e["k"][use],
            e["duration"], e["program"], multipliers, mismatch, zero_point)
        zero = bool(np.any(e["hit"]) and not np.any(e["score"] > 0))
        detector.add(Record(e["start"], e["end"], e["night"] if e["night"] is not None else -1,
                            level, zero, signature, samples))
        evidence = detector.trigger()
        if evidence is not None:
            correct = e["end"] > fault_start
            triggers.append({"time": e["end"], "correct": correct,
                             "delay_hours": (e["end"] - fault_start) / 3600 if correct else None, **evidence})
            if correct and stop_at_fault:
                break
            detector.reported(evidence, e["night"], signature)
            # Recorded data carries no repair; a false report shows no recovery.
    return {"card": stream["card"], "triggers": triggers, "fault_start": fault_start,
            "fault_m": stream["faults"][0][1] if stream["faults"] else None}


class _Night:
    def __init__(self, start, end):
        self.start, self.end = start, end


class _Task:
    """Minimal public task view for the monitor on recorded streams."""

    def __init__(self, stream, card: Path):
        self.nights = [_Night(s, e) for s, e in stream["night_bounds"]]
        self.slot_seconds = 900
        self.scoring = stream["score"]
        targets = _rows(card / "public" / "targets.csv")
        self.ids = [row["target_id"] for row in targets]
        self.ra = np.array([float(row["ra_deg"]) for row in targets])
        self.dec = np.array([float(row["dec_deg"]) for row in targets])
        self.flux = stream["flux"]
        self.weight = stream["weight"]
        scenario = json.loads((card / "config" / "v4_scenario.json").read_text(encoding="utf-8"))
        self.latitude = float(scenario["site"]["latitude_deg"])
        self.longitude = float(scenario["site"]["longitude_deg"])

    def current_night(self, timestamp):
        for i, night in enumerate(self.nights):
            if night.start <= timestamp < night.end:
                return i
        return None


def run_monitor(stream: dict, card: Path, settings: dict, revision: int = 1) -> dict:
    from v4.agent.observer4.instrument import InstrumentMonitor, build_record

    bulletins = [json.loads(line) for line in (card / "public" / "v4_bulletins.jsonl").read_text().splitlines() if line]
    times = np.array([_utc(b["issued_at_utc"]) for b in bulletins])
    terrain = {n["direction"] for n in bulletins[0]["notices"] if n["event_kind"] == "terrain_obstruction"}
    task = _Task(stream, card)
    monitor = InstrumentMonitor(task, settings, revision)
    fault_start = stream["faults"][0][0] if stream["faults"] else float("inf")
    triggers = []
    cursor = 0
    for e in stream["exposures"]:
        # Bulletins published up to this exposure's start reach the agent before it plans.
        upto = int(np.searchsorted(times, e["start"], side="right"))
        monitor.notices(bulletins[cursor:upto], e["start"])
        cursor = upto
        start_notices = {(n["event_kind"], n["direction"]) for n in bulletins[upto - 1]["notices"]} if upto else set()
        signature = frozenset(kind for kind, direction in start_notices if direction == "ALL")
        during = [bulletins[b] for b in range(upto, int(np.searchsorted(times, e["end"] - 1e-6, side="right")))]
        mixed = any(frozenset(n["event_kind"] for n in b["notices"] if n["direction"] == "ALL") != signature
                    for b in during)
        notices = exposure_notices((times, bulletins), e["start"], e["end"])
        clean = np.ones(len(e["ids"]), dtype=bool)
        for kind, direction in notices:
            if direction in DIRECTIONS and kind != "terrain_obstruction":
                near = np.abs((e["az"] - DIRECTIONS[direction] + 180) % 360 - 180) <= 72.0
                clean &= ~(near & (e["alt"] < 80.0))
        for direction in terrain:
            near = np.abs((e["az"] - DIRECTIONS[direction] + 180) % 360 - 180) <= 58.0
            clean &= ~(near & (e["alt"] < 50.3))
        night = e["night"] if e["night"] is not None else -1
        pending = {"start": e["start"], "duration": e["duration"], "program": e["program"], "night": night,
                   "targets": [{"index": int(i), "clean": bool(c), "local_clean": bool(c), "matched_probability": 0.5}
                               for i, c in zip(e["ids"], clean)]}
        hits = {task.ids[i]: float(score) for i, hit, score in zip(e["ids"], e["hit"], e["score"]) if hit}
        reference = monitor.reference_level(night, signature, e["start"])
        record = build_record(task, pending, hits, signature, revision >= 2, reference, mixed=mixed)
        monitor.add(record)
        evidence = monitor.decide(e["end"], night)
        if evidence is not None:
            correct = e["end"] >= fault_start
            triggers.append({"time": e["end"], "correct": correct, "kind": evidence["kind"],
                             "drop": round(evidence["drop"], 3), "band_llr": evidence.get("band_llr"),
                             "delay_hours": (e["end"] - fault_start) / 3600 if correct else None})
            if correct:
                break
            monitor.reported(evidence, e["end"], night)
            if revision >= 2:
                monitor.report_result({"correct": False})
    return {"card": stream["card"], "triggers": triggers, "fault_start": fault_start,
            "fault_m": stream["faults"][0][1] if stream["faults"] else None,
            "window": monitor.window, "quakes": monitor.status()["quakes"], "vetoes": monitor.vetoes}
