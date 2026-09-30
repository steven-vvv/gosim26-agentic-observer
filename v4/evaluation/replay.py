"""Replay fixed actions to diagnose online quality beliefs without replanning."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import multiprocessing
from pathlib import Path

import numpy as np

from v4.agent.observer4.config import load
from v4.agent.observer4.geometry import airmass, altaz, horizontal_vectors, lunar_factors, parse_utc, sidereal
from v4.agent.observer4.planning import PROGRAMS
from v4.agent.observer4.policy import Observer

from .acceleration import accelerated_engine
from .upstream import ROOT, resolve


def _pending(agent: Observer, action: dict, now: float, night: int) -> dict:
    task = agent.task
    indices = np.array([task.index[target] for target in action["assignments"].values()], dtype=int)
    vectors = horizontal_vectors(task.ra[indices], task.dec[indices], sidereal(now, task.longitude), task.latitude)
    altitude, azimuth = altaz(vectors)
    directional, clean = agent.quality.direction_factors(altitude, azimuth)
    base = lunar_factors(task.ra[indices], task.dec[indices], now + 450, task.latitude, task.longitude,
                         task.scoring.get("lunar_model"))
    base /= task.scoring["q0"] * airmass(altitude) ** task.scoring["airmass_exponent"]
    scale = agent.quality.scale(now)
    probabilities = agent.quality.program_probabilities(base)
    program = PROGRAMS.index(action["program"])
    rate = task.flux[indices] * base * scale * directional * agent.config["policy"]["quality_safety"]
    rate /= task.scoring["flux_zero_point"] * task.scoring["exposure_zero_point_seconds"]
    return {
        "start": now, "duration": action["duration_seconds"], "program": action["program"], "night": night,
        "pointing": action["pointing"],
        "targets": [
            {"index": int(index), "fiber": int(fiber), "base_quality": float(base[k]), "clean": bool(clean[k]),
             "matched_probability": float(probabilities[program, k]),
             "predicted_factor": float(min(1, rate[k] * action["duration_seconds"]))}
            for k, (fiber, index) in enumerate(zip(action["assignments"], indices))
        ],
    }


def replay(card: Path, original: Path, output: Path, config: dict, *, trace: bool = False) -> dict:
    resolve()
    from challenge.v4_workflow import PROTOCOL_VERSION, V4Workflow

    if output.exists():
        raise ValueError("replay output already exists")
    expected = json.loads((original / "summary.json").read_text(encoding="utf-8"))
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if config_hash != expected["config_sha256"] or expected["variant"] != "observer4":
        raise ValueError("replay configuration does not match the original participant run")
    actions = [json.loads(line) for line in (original / "actions.jsonl").read_text(encoding="utf-8").splitlines()]
    # The organizer serializes dictionary keys lexically, while our planner
    # inserts fibers numerically. Restore that order for byte-identical ledgers.
    for action in actions:
        if action["action"] == "observe":
            action["assignments"] = dict(sorted(action["assignments"].items(), key=lambda item: int(item[0])))
    workflow = V4Workflow(card.resolve())
    holder = {}
    clean_samples = []
    band_checks = []
    evidence = []
    decisions = []
    position = 0

    def initialize(publication):
        holder["agent"] = Observer(publication, config)

    def decide(message, _deadline):
        nonlocal position
        agent = holder["agent"]
        snapshot = message["payload"]
        now = parse_utc(snapshot["now_utc"]).timestamp()
        night = agent.task.current_night(now)
        result = snapshot.get("last_result")
        if agent.pending and result and result.get("action") == "observe":
            samples = agent.state.feedback(result, agent.pending)
            agent.pointing.feedback(agent.pending, result)
            agent.quality.update(samples, agent.pending, now, agent.pending["night"])
            clean_samples.extend(row for row in agent.quality.clean if row[0] == now)
            band_checks.extend(row for row in agent.quality.band_checks if row[0] == now)
        agent.pending = None
        agent.quality.messages(snapshot, now)
        for item in snapshot.get("new_messages", []):
            if item.get("record_type") == "state_resync":
                agent.state.resync(item)
        candidate = agent.quality.report_evidence(now)
        if candidate:
            evidence.append({"now_utc": snapshot["now_utc"], **candidate})
        if trace:
            decisions.append({"now": now, "night": night,
                              "notices": sorted([kind, direction] for kind, direction in agent.quality.notices),
                              "forecast_nights": [index for index, calendar in enumerate(agent.task.nights)
                                                  if calendar.date in agent.quality.forecast_weather_nights]})
        if position >= len(actions):
            raise ValueError("replay requested more actions than the original")
        action = actions[position]
        position += 1
        if action["action"] == "observe":
            if night is None:
                raise ValueError("recorded observation outside a public night")
            agent.pending = _pending(agent, action, now, night)
        elif action["action"] == "report":
            agent.quality.reported(now)
        return {"protocol_version": PROTOCOL_VERSION, "message_type": "decision_response",
                "decision_sequence": message["decision_sequence"], "decision_source": "public-feedback-replay", **action}

    with accelerated_engine():
        outcome = workflow.run(decide, output, wallclock_seconds=900, initialize=initialize)
    if position != len(actions):
        raise ValueError("replay ended before consuming the original actions")
    score = outcome["score_report"]
    for key in ("total", "components", "counts"):
        if score[key] != expected[key]:
            raise ValueError(f"replay score differs from the original: {key}")
    if (original / "observations.csv").read_bytes() != (output / "observations.csv").read_bytes():
        raise ValueError("replay observation ledger differs from the original")
    agent = holder["agent"]
    nightly = []
    for night, calendar in enumerate(agent.task.nights):
        values = [value for _, index, value in clean_samples if index == night]
        checks = [row for row in band_checks if row[1] == night and row[2] == "DARK"]
        nightly.append({"night": night, "date": calendar.date, "clean_samples": len(values),
                        "median_quality_ratio": round(float(np.median(values)), 6) if values else None,
                        "forecast_weather": calendar.date in agent.quality.forecast_weather_nights,
                        "dark_saturated_checks": len(checks), "dark_matches": int(sum(row[3] for row in checks))})
    report = {"schema": "gosim26.v4.quality-replay.v1", "original": str(original.resolve()),
              "card": card.name, "config_sha256": config_hash, "total": score["total"],
              "identical_observation_ledger": True,
              "replayed_actions": position, "report_evidence": evidence, "nightly_quality": nightly,
              "final_state": agent.summary()}
    (output / "quality_replay.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if trace:
        public_trace = {"schema": "gosim26.v4.quality-trace.v1", "card": card.name,
                        "decisions": decisions, "clean_samples": clean_samples, "band_checks": band_checks}
        (output / "quality_trace.json").write_text(json.dumps(public_trace) + "\n", encoding="utf-8")
    return report


def replay_batch(cards: Path, originals: Path, output: Path, config: dict, jobs: int) -> dict:
    if not 1 <= jobs <= 4:
        raise ValueError("jobs must be between 1 and 4")
    sources = sorted(originals.glob("*/summary.json"))
    if not sources:
        raise ValueError(f"no original participant runs in {originals}")
    output.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("fork") if "fork" in multiprocessing.get_all_start_methods() else None
    options = {"max_workers": jobs}
    if context is not None:
        options["mp_context"] = context
    completed = 0
    with concurrent.futures.ProcessPoolExecutor(**options) as pool:
        futures = {}
        for source in sources:
            card = source.parent.name
            destination = output / card
            if destination.exists():
                report_path = destination / "quality_replay.json"
                trace_path = destination / "quality_trace.json"
                if not report_path.is_file() or not trace_path.is_file():
                    raise ValueError(f"incomplete prior replay: {destination}")
                report = json.loads(report_path.read_text(encoding="utf-8"))
                trace = json.loads(trace_path.read_text(encoding="utf-8"))
                config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
                if (report["original"] != str(source.parent.resolve()) or trace["card"] != card
                        or report.get("config_sha256") != config_hash):
                    raise ValueError(f"prior replay does not match original: {destination}")
                completed += 1
                continue
            future = pool.submit(replay, cards / card, source.parent, destination, config, trace=True)
            futures[future] = card
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            completed += 1
            print(json.dumps({"card": futures[future], "replayed_actions": result["replayed_actions"],
                              "suggested_reports": len(result["report_evidence"])}), flush=True)
    return {"cards": completed, "traces": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--card", type=Path)
    parser.add_argument("--original", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "v4" / "agent" / "config.json")
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--batch-originals", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    patch = json.loads(args.patch.read_text(encoding="utf-8")) if args.patch else None
    config = load(args.config, patch)
    if args.batch_originals:
        if args.card is None:
            parser.error("--card must name the cards directory for batch replay")
        print(json.dumps(replay_batch(args.card, args.batch_originals, args.out, config, args.jobs),
                         indent=2, sort_keys=True))
    else:
        if args.card is None or args.original is None:
            parser.error("--card and --original are required for a single replay")
        print(json.dumps(replay(args.card, args.original, args.out, config, trace=args.trace),
                         indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
