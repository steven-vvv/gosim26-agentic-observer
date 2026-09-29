"""Run a participant or official baseline against the pinned, unmodified v4 engine."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

from v4.agent.observer4.config import load
from v4.agent.observer4.policy import Observer

from .upstream import ROOT, resolve


def baseline_factory(clone: Path):
    path = clone / "starter_kit_v4" / "agent"
    sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location("gosim4_official_baseline", path / "baseline_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda publication: module.BaselineAgent(publication)


def evaluate(card: Path, output: Path, config: dict, *, baseline=False, fast=False, wallclock=None, profile=None) -> dict:
    clone = resolve()
    from challenge.v4_workflow import V4Workflow

    workflow = V4Workflow(card.resolve())
    holder = {}
    policy_seconds = 0.0
    actions = []
    factory = baseline_factory(clone) if baseline else lambda publication: Observer(publication, config)

    def initialize(publication):
        holder["agent"] = factory(publication)

    def decide(message, _deadline):
        nonlocal policy_seconds
        started = time.perf_counter()
        action = holder["agent"].respond(message["payload"])
        policy_seconds += time.perf_counter() - started
        response = {
            "protocol_version": "participant-agent-protocol-v4",
            "message_type": "decision_response",
            "decision_sequence": message["decision_sequence"],
            **action,
        }
        actions.append(response)
        return response

    started = time.perf_counter()
    if fast:
        from .acceleration import accelerated_engine

        with accelerated_engine():
            result = workflow.run(decide, output, wallclock_seconds=wallclock, initialize=initialize)
    else:
        result = workflow.run(decide, output, wallclock_seconds=wallclock, initialize=initialize)
    elapsed = time.perf_counter() - started
    report = result["score_report"]
    trace = holder["agent"].summary() if not baseline and "agent" in holder else {}
    summary = {
        "card": card.name,
        "variant": "official-baseline" if baseline else "observer4",
        "fast_engine": fast,
        "total": report["total"],
        "components": report["components"],
        "counts": report["counts"],
        "termination_reason": result["termination_reason"],
        "engine_detail": result["termination_detail"],
        "elapsed_seconds": round(elapsed, 6),
        "policy_seconds": round(policy_seconds, 6),
        "engine_seconds": round(elapsed - policy_seconds, 6),
        "decision_requests": result["decision_requests"],
        "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "action_sha256": hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest(),
        "agent_state": trace,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--card", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "v4" / "agent" / "config.json")
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--wallclock", type=float)
    args = parser.parse_args()
    patch = json.loads(args.patch.read_text(encoding="utf-8")) if args.patch else None
    config = load(args.config, patch)
    if args.out.exists():
        parser.error("output already exists; choose a new run path")
    print(
        json.dumps(
            evaluate(args.card, args.out, config, baseline=args.baseline, fast=args.fast, wallclock=args.wallclock),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
