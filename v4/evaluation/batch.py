"""Run paired v4 experiments without overwriting prior evidence."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import statistics
from pathlib import Path

from v4.agent.observer4.config import load

from .generate import specifications
from .run import evaluate, source_sha256
from .upstream import ROOT, resolve


def _config(variant: str) -> dict:
    patch = None
    if variant not in {"baseline", "default"}:
        patch = json.loads((ROOT / "v4" / "experiments" / f"{variant}.json").read_text(encoding="utf-8"))
    return load(ROOT / "v4" / "agent" / "config.json", patch)


def _run(card: Path, output: Path, variant: str, expected_source: str) -> dict:
    if source_sha256() != expected_source:
        raise ValueError("agent source changed during experiment")
    config = _config(variant)
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if output.exists():
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        if (
            summary["card"] != card.name
            or summary["config_sha256"] != digest
            or summary.get("source_sha256") != expected_source
            or summary["fast_engine"] is not True
            or (summary["variant"] == "official-baseline") != (variant == "baseline")
        ):
            raise ValueError(f"existing run does not match requested experiment: {output}")
        return summary
    summary = evaluate(card, output, config, baseline=variant == "baseline", fast=True)
    if source_sha256() != expected_source:
        raise ValueError("agent source changed during experiment")
    return summary


def run(dataset: str, variants: list[str], jobs: int, *, limit: int | None = None, tag: str = "dev") -> dict:
    resolve()
    if not tag or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in tag):
        raise ValueError("tag must use lowercase letters, digits, hyphens or underscores")
    specs = specifications(dataset)
    if limit is not None:
        specs = specs[:limit]
    cards = [ROOT / "v4" / "cards" / spec["name"] for spec in specs]
    if any(not card.is_dir() for card in cards):
        raise ValueError("dataset cards are missing; run evaluation.generate first")
    output = ROOT / "v4" / "results" / f"{dataset}-r2" / tag
    source = source_sha256()
    requested = [(_run, card, output / variant / card.name, variant, source) for variant in variants for card in cards]
    results = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, min(jobs, 4))) as pool:
        futures = {pool.submit(fn, *args): (args[0].name, args[2]) for fn, *args in requested}
        for future in concurrent.futures.as_completed(futures):
            card, variant = futures[future]
            summary = future.result()
            results[(card, variant)] = summary
            print(json.dumps({"card": card, "variant": variant, "total": summary["total"]}), flush=True)
    aggregate = {"schema": "gosim26.v4.paired-results.v1", "dataset": dataset,
                 "tag": tag, "source_sha256": source, "cards": len(cards), "variants": {}}
    for variant in variants:
        rows = [results[(card.name, variant)] for card in cards]
        scores = [row["total"] for row in rows]
        entry = {
            "mean": round(statistics.mean(scores), 6),
            "median": round(statistics.median(scores), 6),
            "minimum": round(min(scores), 6),
            "maximum": round(max(scores), 6),
            "required_missing_mean": round(statistics.mean(row["counts"]["required_missing"] for row in rows), 3),
            "policy_seconds_mean": round(statistics.mean(row["policy_seconds"] for row in rows), 3),
            "termination_reasons": sorted({row["termination_reason"] for row in rows}),
        }
        if "baseline" in variants and variant != "baseline":
            deltas = [row["total"] - results[(card.name, "baseline")]["total"] for card, row in zip(cards, rows)]
            entry["paired_delta_vs_baseline"] = {
                "mean": round(statistics.mean(deltas), 6),
                "median": round(statistics.median(deltas), 6),
                "minimum": round(min(deltas), 6),
                "wins": sum(delta > 0 for delta in deltas),
            }
        aggregate["variants"][variant] = entry
    print(json.dumps(aggregate, indent=2, sort_keys=True), flush=True)
    return aggregate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--variants", nargs="+", default=["baseline", "default"])
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tag", default="dev")
    args = parser.parse_args()
    run(args.dataset, args.variants, args.jobs, limit=args.limit, tag=args.tag)


if __name__ == "__main__":
    main()
