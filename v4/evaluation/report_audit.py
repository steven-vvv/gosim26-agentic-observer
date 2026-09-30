"""Audit fault reports against truth and the official score settlement offline."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from v4.agent.observer4.geometry import parse_utc

from .upstream import ROOT


def audit_card(card: Path, result: Path) -> dict:
    summary = json.loads((result / "summary.json").read_text(encoding="utf-8"))
    if summary["card"] != card.name or result.name != card.name:
        raise ValueError(f"card and result mismatch: {result}")
    score = json.loads((card / "config/v4_score_config.json").read_text(encoding="utf-8"))
    with (card / "truth/v4_events.csv").open(newline="", encoding="utf-8") as stream:
        faults = [
            (row["event_id"], parse_utc(row["actual_start_utc"]), parse_utc(row["actual_end_utc"]))
            for row in csv.DictReader(stream) if row["event_type"] == "instrument_fault"
        ]
    repaired = set()
    reports = []
    settlement = 0.0
    with (result / "decisions.csv").open(newline="", encoding="utf-8") as stream:
        for decision in csv.DictReader(stream):
            if decision["action"] != "report":
                continue
            moment = parse_utc(decision["start_utc"])
            fault = next((item for item in faults if item[1] <= moment < item[2]), None)
            correct = fault is not None and fault[0] not in repaired
            if correct:
                repaired.add(fault[0])
            settlement += float(score["reporting"]["correct_reward" if correct else "false_penalty"])
            reports.append({
                "decision_id": int(decision["decision_id"]),
                "time_utc": decision["start_utc"],
                "correct": correct,
                "fault_id": fault[0] if correct else None,
                "hours_after_fault_start": round((moment - fault[1]).total_seconds() / 3600, 6) if correct else None,
            })
    scored = float(summary["components"]["report_settlement"])
    if abs(settlement - scored) > 1e-6:
        raise ValueError(f"report settlement mismatch for {result}: computed {settlement}, scored {scored}")
    return {
        "card": card.name,
        "fault_events": len(faults),
        "reports": reports,
        "correct": len(repaired),
        "false": len(reports) - len(repaired),
        "settlement": scored,
    }


def audit(runs: Path, cards: Path, variants: list[str]) -> dict:
    if not variants or len(set(variants)) != len(variants):
        raise ValueError("select distinct variants")
    result = {"schema": "gosim26.v4.report-audit.v1", "variants": {}}
    for variant in variants:
        paths = sorted((runs / variant).glob("*/summary.json"))
        if not paths:
            raise ValueError(f"no completed results for {variant}")
        rows = [audit_card(cards / path.parent.name, path.parent) for path in paths]
        delays = [report["hours_after_fault_start"] for row in rows for report in row["reports"]
                  if report["correct"]]
        result["variants"][variant] = {
            "cards": len(rows),
            "cards_with_fault": sum(row["fault_events"] > 0 for row in rows),
            "fault_events": sum(row["fault_events"] for row in rows),
            "reports": sum(len(row["reports"]) for row in rows),
            "correct": sum(row["correct"] for row in rows),
            "false": sum(row["false"] for row in rows),
            "settlement": sum(row["settlement"] for row in rows),
            "correct_report_delay_hours_mean": round(statistics.mean(delays), 6) if delays else None,
            "correct_report_delay_hours_max": max(delays) if delays else None,
            "per_card": rows,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--cards", type=Path, default=ROOT / "v4/cards")
    parser.add_argument("--variants", nargs="+", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = audit(args.runs, args.cards, args.variants)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
