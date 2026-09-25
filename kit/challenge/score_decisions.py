#!/usr/bin/env python3
"""Replay committed decisions and write the authoritative score report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import sha256_file, write_text_lf
from .project_paths import EXAMPLE3_ROOT, REFERENCE_OUTPUT_DIR
from .scoring_core import ChallengeScorer, load_decisions


def score(
    decisions_path: Path,
    output_path: Path,
    termination_reason: str,
) -> dict[str, object]:
    scorer = ChallengeScorer.from_files(EXAMPLE3_ROOT)
    for decision in load_decisions(decisions_path, allow_reports=scorer.mechanics):
        scorer.apply_decision(decision)
    report = scorer.finalize(termination_reason)
    governed = {
        "decisions": decisions_path,
        "calendar_config": EXAMPLE3_ROOT / "config" / "calendar_config.json",
        "tile_config": EXAMPLE3_ROOT / "config" / "tile_config.json",
        "weather_config": EXAMPLE3_ROOT / "config" / "weather_config.json",
        "request_config": EXAMPLE3_ROOT / "config" / "request_config.json",
        "score_config": EXAMPLE3_ROOT / "config" / "score_config.json",
        "slots": REFERENCE_OUTPUT_DIR / "slots.csv",
        "tiles": REFERENCE_OUTPUT_DIR / "tiles.csv",
        "targets": REFERENCE_OUTPUT_DIR / "targets.csv",
        "weather": REFERENCE_OUTPUT_DIR / "weather.csv",
        "events": REFERENCE_OUTPUT_DIR / "weather_events.csv",
        "forecasts": REFERENCE_OUTPUT_DIR / "weather_forecasts.csv",
        "requests": REFERENCE_OUTPUT_DIR / "observation_requests.csv",
        "request_tiles": REFERENCE_OUTPUT_DIR / "observation_request_tiles.csv",
    }
    anomalies = REFERENCE_OUTPUT_DIR / "tile_anomalies.csv"
    if anomalies.exists():
        governed["anomalies"] = anomalies
    report["input_sha256"] = {key: sha256_file(path) for key, path in governed.items()}
    report["actions"] = scorer.actions
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(output_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("--output", type=Path, default=REFERENCE_OUTPUT_DIR / "score_report.json")
    parser.add_argument("--termination-reason", default="trace_complete")
    args = parser.parse_args()
    report = score(args.decisions, args.output, args.termination_reason)
    print(json.dumps(report["score"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
