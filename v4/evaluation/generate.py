"""Generate reproducible research cards atomically using the organizer generators."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import multiprocessing
import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from .upstream import ROOT, resolve


def specifications(dataset: str, path: Path | None = None):
    path = path or ROOT / "v4" / "experiments" / "datasets.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    selected = data[dataset]
    result = []
    for i in range(selected["count"]):
        profile = selected["profiles"][i % len(selected["profiles"])]
        start = date.fromisoformat(profile["start_date"])
        seed = selected.get("seed_overrides", {}).get(str(i), selected["seed_base"] + i * 997 + 17)
        name = f"{dataset}-r{data['generation_revision']}-{i:03d}"
        result.append(
            {
                "name": name,
                "card_id": name,
                "scenario_slug": name,
                "phase": "research",
                "seed": seed,
                "start_date": str(start),
                "end_date": str(start + timedelta(days=profile["nights"])),
                "targets": profile["targets"],
                "area_deg2": profile["area_deg2"],
                "stress": profile["stress"],
                "wallclock_seconds": 900,
            }
        )
    return result


def generate(spec: dict, base: Path):
    resolve()
    from challenge.v4_bundle import build_card_bundle

    destination = base / spec["name"]
    encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    if destination.exists():
        manifest = json.loads((destination / "research-manifest.json").read_text(encoding="utf-8"))
        if manifest["spec_sha256"] != hashlib.sha256(encoded.encode()).hexdigest():
            raise ValueError("existing card has a different specification")
        return {"name": spec["name"], "status": "reused"}
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{spec['name']}-", dir=base) as scratch:
        card = Path(scratch) / "card"
        build_card_bundle(card, spec)
        with (card / "public" / "v4_night_calendar.csv").open(newline="", encoding="utf-8") as stream:
            actual_nights = sum(1 for _ in csv.DictReader(stream))
        expected_nights = (date.fromisoformat(spec["end_date"]) - date.fromisoformat(spec["start_date"])).days
        if actual_nights != expected_nights:
            raise ValueError(f"generated {actual_nights} nights, expected {expected_nights}")
        checksums = {
            str(path.relative_to(card)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(card.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema": "gosim26.v4.research-card.v1",
            "spec": spec,
            "spec_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "actual_nights": actual_nights,
            "products_sha256": checksums,
        }
        (card / "research-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.rename(card, destination)
        except FileExistsError:
            if destination.exists():
                shutil.rmtree(card)
            else:
                raise
    return {"name": spec["name"], "status": "created"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--specs", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "v4" / "cards")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    specs = specifications(args.dataset, args.specs)
    if args.limit is not None:
        specs = specs[: args.limit]
    context = multiprocessing.get_context("fork") if os.name == "posix" else None
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, min(args.jobs, 4)), mp_context=context) as pool:
        futures = [pool.submit(generate, spec, args.out) for spec in specs]
        for future in concurrent.futures.as_completed(futures):
            print(json.dumps(future.result(), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
