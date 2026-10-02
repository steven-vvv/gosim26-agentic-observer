#!/usr/bin/env python3
"""Replay a decisions.csv against a scenario with the authoritative scorer and write score_report.json.

    python3 score_decisions.py --scenario scenarios/dev-reference --decisions run_output/decisions.csv [--out report.json]

Scoring needs the organizer truth files (weather.csv, weather_events.csv, ...) so it only works for scenarios whose
truth is public, such as the bundled dev-reference and anything produced by make_scenario.py. The platform runs the
same `challenge.scoring_core.score_files` after every agent run; a local report for the same decisions.csv and
scenario is byte-for-byte reproducible.

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
if str(KIT_ROOT) not in sys.path:
    sys.path.insert(0, str(KIT_ROOT))

from challenge.scoring_core import score_files  # noqa: E402

TERMINATION_REASONS = ("trace_complete", "survey_complete", "global_wallclock_expired", "agent_error", "agent_initialization_error")


def default_termination(decisions: Path) -> str:
    """Reuse the termination reason recorded by the runner next to decisions.csv when there is one."""
    sibling = decisions.parent / "workflow_result.json"
    if sibling.is_file():
        try:
            reason = json.loads(sibling.read_text(encoding="utf-8")).get("termination_reason")
            if isinstance(reason, str) and reason:
                return reason
        except (OSError, ValueError):
            pass
    return "trace_complete"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", type=Path, required=True, help="scenario directory with config/ and outputs/reference/")
    parser.add_argument("--decisions", type=Path, required=True, help="decisions.csv (decision_id,slot_id,action,tile_id,program,request_id,reason)")
    parser.add_argument("--out", type=Path, default=None, help="report path (default: score_report.json next to the decisions file)")
    parser.add_argument("--termination-reason", default=None,
                        help=f"recorded in the report; one of {', '.join(TERMINATION_REASONS)} (default: from workflow_result.json if present, else trace_complete)")
    args = parser.parse_args(argv)
    scenario = args.scenario.resolve()
    truth = scenario / "outputs" / "reference"
    missing = [name for name in ("weather.csv", "weather_events.csv", "weather_forecasts.csv") if not (truth / name).is_file()]
    if missing:
        raise SystemExit(f"cannot score locally: {scenario} does not publish {', '.join(missing)} (hidden-truth scenarios are scored on the platform only)")
    if not args.decisions.is_file():
        raise SystemExit(f"decisions file not found: {args.decisions}")
    out = args.out or args.decisions.parent / "score_report.json"
    reason = args.termination_reason or default_termination(args.decisions)
    report = score_files(scenario, args.decisions.resolve(), out.resolve(), reason)
    print(json.dumps({"report": str(out.resolve()), "termination_reason": reason, "score": report["score"],
                      "completed_tiles": len(report["completion"]["completed_tiles"]),
                      "required_missing": report["completion"]["required_missing"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
