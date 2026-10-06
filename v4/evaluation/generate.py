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
    if "cards" in selected:
        # Published organizer cards (downloaded bundles), linked under v4/cards by name.
        return [{"name": name, "card_id": name, "published": True} for name in selected["cards"]]
    if "family" in selected:
        from .family import family_specifications

        return family_specifications(dataset, selected, data["generation_revision"])
    if "kinds" in selected:
        from .kinds import kind_specifications

        return kind_specifications(dataset, selected, data["generation_revision"])
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
                **({"event_counts": profile["event_counts"]} if "event_counts" in profile else {}),
                **({"components": profile["components"]} if "components" in profile else {}),
            }
        )
    return result


def build_with_components(card: Path, spec: dict) -> None:
    """The organizer bundle builder with an explicit footprint component count.

    The public builder uses two components below 3000 deg2, while the published alpha
    card has three components on 1880 deg2; this mirrors the builder with the default
    three-component footprint at any area.
    """
    from challenge import v4_bundle

    original = v4_bundle._load

    def load(name: str) -> dict:
        data = original(name)
        if name == "v4_catalog_config.json":
            data["footprint"]["n_components"] = int(spec["components"])
            weights = data["footprint"]["component_area_weights"][: int(spec["components"])]
            data["footprint"]["component_area_weights"] = weights
            data["footprint"]["total_area_deg2"] = 3000.0  # keeps the builder on the default branch
        return data

    v4_bundle._load = load
    try:
        clean = {key: value for key, value in spec.items() if key != "components"}
        clean["area_deg2"] = 3000.0
        catalog_area = float(spec["area_deg2"])
        original_generate = v4_bundle.v4_catalog_generator.generate_catalog

        def generate_catalog(config_path, output_dir):
            import json as _json

            config = _json.loads(Path(config_path).read_text())
            config["footprint"]["total_area_deg2"] = catalog_area
            Path(config_path).write_text(_json.dumps(config))
            return original_generate(config_path, output_dir)

        v4_bundle.v4_catalog_generator.generate_catalog = generate_catalog
        try:
            v4_bundle.build_card_bundle(card, clean)
        finally:
            v4_bundle.v4_catalog_generator.generate_catalog = original_generate
    finally:
        v4_bundle._load = original


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
        if "family" in spec:
            from .family import build_family_card

            build_family_card(card, spec)
        elif "kind" in spec:
            from .kinds import build_kind_card

            build_kind_card(card, spec)
        elif "components" in spec:
            build_with_components(card, spec)
        else:
            build_card_bundle(card, spec)
        with (card / "public" / "v4_night_calendar.csv").open(newline="", encoding="utf-8") as stream:
            actual_nights = sum(1 for _ in csv.DictReader(stream))
        if "start_date" in spec:  # kind cards take their season from the organizer spec
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
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, min(args.jobs, os.cpu_count() or 4)), mp_context=context) as pool:
        futures = [pool.submit(generate, spec, args.out) for spec in specs]
        for future in concurrent.futures.as_completed(futures):
            print(json.dumps(future.result(), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
