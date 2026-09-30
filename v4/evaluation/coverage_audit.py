"""Classify required-target misses from the public catalog and scored action ledger."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from .upstream import ROOT


def audit_card(card: Path, result: Path) -> dict:
    summary = json.loads((result / "summary.json").read_text(encoding="utf-8"))
    if summary["card"] != card.name or result.name != card.name:
        raise ValueError(f"card and result mismatch: {result}")
    with (card / "public/targets.csv").open(newline="", encoding="utf-8") as stream:
        required = {row["target_id"] for row in csv.DictReader(stream) if row["required"].lower() == "true"}
    scoring = json.loads((card / "config/v4_score_config.json").read_text(encoding="utf-8"))
    threshold = float(scoring["required"]["observed_factor_threshold"])

    assigned = set()
    with (result / "actions.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            action = json.loads(line)
            if action.get("action") == "observe":
                assigned.update(action["assignments"].values())

    best_factor = {}
    with (result / "observations.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["valid"].lower() != "true":
                continue
            target = row["target_id"]
            best_factor[target] = max(best_factor.get(target, 0.0), float(row["factor"]))

    never_assigned = required - assigned
    assigned_no_valid_hit = (required & assigned) - best_factor.keys()
    hit_below_threshold = {target for target in required & best_factor.keys() if best_factor[target] < threshold}
    reached_threshold = (required & best_factor.keys()) - hit_below_threshold
    missing = len(never_assigned) + len(assigned_no_valid_hit) + len(hit_below_threshold)
    if missing != summary["counts"]["required_missing"]:
        raise ValueError(f"required missing mismatch for {result}: audited {missing}, scored "
                         f"{summary['counts']['required_missing']}")
    return {
        "card": card.name,
        "required": len(required),
        "required_missing": missing,
        "never_assigned": len(never_assigned),
        "assigned_no_valid_hit": len(assigned_no_valid_hit),
        "hit_below_threshold": len(hit_below_threshold),
        "reached_threshold": len(reached_threshold),
    }


def audit(runs: Path, cards: Path, variants: list[str]) -> dict:
    if not variants or len(set(variants)) != len(variants):
        raise ValueError("select distinct variants")
    output = {"schema": "gosim26.v4.coverage-audit.v1", "variants": {}}
    for variant in variants:
        paths = sorted((runs / variant).glob("*/summary.json"))
        if not paths:
            raise ValueError(f"no completed results for {variant}")
        rows = [audit_card(cards / path.parent.name, path.parent) for path in paths]
        output["variants"][variant] = {
            "cards": len(rows),
            "totals": {key: sum(row[key] for row in rows) for key in rows[0] if key != "card"},
            "means": {key: round(statistics.mean(row[key] for row in rows), 6)
                      for key in rows[0] if key != "card"},
            "per_card": rows,
        }
    return output


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
