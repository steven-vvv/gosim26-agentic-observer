"""Analyze paired research results, including tails, components and runtime."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import numpy as np

from .generate import specifications


def _rows(path: Path) -> dict[str, dict]:
    rows = {}
    for summary in sorted(path.glob("*/summary.json")):
        row = json.loads(summary.read_text(encoding="utf-8"))
        if row["card"] != summary.parent.name or row["card"] in rows:
            raise ValueError(f"ambiguous card result: {summary}")
        rows[row["card"]] = row
    if not rows:
        raise ValueError(f"no completed results in {path}")
    for key in ("config_sha256", "source_sha256", "fast_engine", "variant"):
        if len({row[key] for row in rows.values()}) != 1:
            raise ValueError(f"mixed {key} within {path}")
    return rows


def _distribution(values) -> dict:
    values = np.asarray(values, dtype=float)
    if not len(values) or not np.all(np.isfinite(values)):
        raise ValueError("statistics require nonempty, finite data")
    return {
        "mean": round(float(np.mean(values)), 6),
        "median": round(float(np.median(values)), 6),
        "p10": round(float(np.quantile(values, 0.10)), 6),
        "p90": round(float(np.quantile(values, 0.90)), 6),
        "minimum": round(float(np.min(values)), 6),
        "maximum": round(float(np.max(values)), 6),
    }


def paired(reference: dict[str, dict], candidate: dict[str, dict], *, samples: int = 10000,
           allow_incomplete: bool = False) -> dict:
    if samples < 100:
        raise ValueError("at least 100 bootstrap samples are required")
    missing = sorted(reference.keys() - candidate.keys())
    extra = sorted(candidate.keys() - reference.keys())
    if (missing or extra) and not allow_incomplete:
        raise ValueError("unpaired cards; use --allow-incomplete only for explicitly provisional analysis")
    cards = sorted(reference.keys() & candidate.keys())
    if not cards:
        raise ValueError("no paired cards")
    for card in cards:
        for key in ("source_sha256", "fast_engine"):
            if reference[card][key] != candidate[card][key]:
                raise ValueError(f"paired {key} mismatch for {card}")
    deltas = np.array([candidate[card]["total"] - reference[card]["total"] for card in cards])
    result = _distribution(deltas)
    rng = np.random.default_rng(20260930)
    means = np.mean(rng.choice(deltas, size=(samples, len(cards))), axis=1)
    result.update({
        "cards": len(cards),
        "provisional": bool(missing or extra),
        "missing_candidate_cards": missing,
        "extra_candidate_cards": extra,
        "wins": int(np.count_nonzero(deltas > 1e-6)),
        "ties": int(np.count_nonzero(np.abs(deltas) <= 1e-6)),
        "losses": int(np.count_nonzero(deltas < -1e-6)),
        "mean_bootstrap_95_ci": [round(float(value), 6) for value in np.quantile(means, (0.025, 0.975))],
        "bootstrap_samples": samples,
        "bootstrap_seed": 20260930,
    })
    component_keys = set(reference[cards[0]]["components"])
    if any(set(rows[card]["components"]) != component_keys for rows in (reference, candidate) for card in cards):
        raise ValueError("paired score component mismatch")
    count_keys = set(reference[cards[0]]["counts"])
    if any(set(rows[card]["counts"]) != count_keys for rows in (reference, candidate) for card in cards):
        raise ValueError("paired score count mismatch")
    result["component_delta_means"] = {
        key: round(statistics.mean(candidate[card]["components"][key] - reference[card]["components"][key]
                                   for card in cards), 6)
        for key in sorted(component_keys)
    }
    result["count_delta_means"] = {
        key: round(statistics.mean(candidate[card]["counts"][key] - reference[card]["counts"][key]
                                   for card in cards), 6)
        for key in sorted(count_keys)
    }
    result["required_missing_delta_mean"] = result["count_delta_means"]["required_missing"]
    result["per_card"] = [
        {"card": card, "delta": round(float(delta), 6), "reference_total": reference[card]["total"],
         "candidate_total": candidate[card]["total"],
         "required_missing_delta": candidate[card]["counts"]["required_missing"]
                                   - reference[card]["counts"]["required_missing"],
         "policy_seconds": candidate[card]["policy_seconds"],
         "termination_reason": candidate[card]["termination_reason"]}
        for card, delta in sorted(zip(cards, deltas), key=lambda item: (item[1], item[0]))
    ]
    return result


def analyze(path: Path, reference: str, variants: list[str] | None = None, *, samples: int = 10000,
            allow_incomplete: bool = False, specs: list[dict] | None = None) -> dict:
    names = variants or [entry.name for entry in sorted(path.iterdir()) if entry.is_dir() and entry.name != reference]
    if not names or reference in names or len(set(names)) != len(names):
        raise ValueError("select distinct candidate variants and a separate reference")
    rows = {name: _rows(path / name) for name in [reference, *names]}
    result = {
        "schema": "gosim26.v4.paired-analysis.v1",
        "run_directory": str(path.resolve()),
        "reference": reference,
        "variants": {},
        "paired": {},
    }
    for name, values in rows.items():
        ordered = list(values.values())
        result["variants"][name] = {
            "completed_cards": len(ordered),
            "total": _distribution([row["total"] for row in ordered]),
            "components": {
                key: _distribution([row["components"][key] for row in ordered])
                for key in sorted(ordered[0]["components"])
            },
            "counts": {
                key: _distribution([row["counts"][key] for row in ordered])
                for key in sorted(ordered[0]["counts"])
            },
            "required_missing": _distribution([row["counts"]["required_missing"] for row in ordered]),
            "policy_seconds": _distribution([row["policy_seconds"] for row in ordered]),
            "elapsed_seconds": _distribution([row["elapsed_seconds"] for row in ordered]),
            "termination_counts": {
                reason: sum(row["termination_reason"] == reason for row in ordered)
                for reason in sorted({row["termination_reason"] for row in ordered})
            },
            "config_sha256": ordered[0]["config_sha256"],
            "source_sha256": ordered[0]["source_sha256"],
            "fast_engine": ordered[0]["fast_engine"],
        }
        if name != reference:
            result["paired"][name] = paired(rows[reference], values, samples=samples, allow_incomplete=allow_incomplete)
    if specs is not None:
        expected = {spec["name"] for spec in specs}
        if len(expected) != len(specs):
            raise ValueError("duplicate dataset card specification")
        for name, values in rows.items():
            if values.keys() - expected or (not allow_incomplete and expected - values.keys()):
                raise ValueError(f"results do not match dataset specifications for {name}")
        groups = {}
        for spec in specs:
            profile = {key: spec[key] for key in ("start_date", "end_date", "targets", "area_deg2", "stress")}
            key = json.dumps(profile, sort_keys=True)
            groups.setdefault(key, {"profile": profile, "cards": []})["cards"].append(spec["name"])
        result["profiles"] = {}
        for index, group in enumerate(groups.values()):
            members = group["cards"]
            entry = {"profile": group["profile"], "expected_cards": len(members), "variants": {}, "paired": {}}
            for name, values in rows.items():
                subset = {card: values[card] for card in members if card in values}
                entry["variants"][name] = {
                    "completed_cards": len(subset),
                    "total": _distribution([row["total"] for row in subset.values()]) if subset else None,
                    "policy_seconds": _distribution([row["policy_seconds"] for row in subset.values()]) if subset else None,
                    "termination_counts": {
                        reason: sum(row["termination_reason"] == reason for row in subset.values())
                        for reason in sorted({row["termination_reason"] for row in subset.values()})
                    },
                }
            for name in names:
                left = {card: rows[reference][card] for card in members if card in rows[reference]}
                right = {card: rows[name][card] for card in members if card in rows[name]}
                entry["paired"][name] = (paired(left, right, samples=samples, allow_incomplete=allow_incomplete)
                                         if left.keys() & right.keys() else None)
            result["profiles"][f"profile_{index}"] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--reference", default="baseline")
    parser.add_argument("--variants", nargs="+")
    parser.add_argument("--bootstrap-samples", type=int, default=10000)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--dataset", help="check expected cards and report the predeclared dataset profiles")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = analyze(args.runs, args.reference, args.variants, samples=args.bootstrap_samples,
                     allow_incomplete=args.allow_incomplete,
                     specs=specifications(args.dataset) if args.dataset else None)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
