#!/usr/bin/env python3
"""Generate a new, fully public practice scenario from the reference configuration with a different seed.

    python3 make_scenario.py --out scenarios/mine --seed 7 --days 30 [--start-date 2026-10-05]

The output directory gets the example3 layout (config/*.json, outputs/reference/*.csv, metadata and a manifest),
generated deterministically by the same simulators the organizers use: observing calendar, tile geometry,
directional weather with forecast revisions, and temporary observation requests. Everything, including the
organizer-only weather truth, is written so the scenario can be run and scored locally. Hidden evaluation
scenarios use the same generator with undisclosed seeds and sizes.

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

from challenge.scenario_builder import ScenarioError, describe_scenario, generate_scenario  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="destination scenario directory (created; refuses to overwrite unless --force)")
    parser.add_argument("--seed", type=int, default=None, help="seed shared by the tile, weather and request simulators (required unless --validate-only)")
    parser.add_argument("--days", type=int, default=30, help="number of survey nights (reference scenario: 180)")
    parser.add_argument("--start-date", default=None, help="first survey night, YYYY-MM-DD (default: the reference start date)")
    parser.add_argument("--scenario-id", default=None, help="scenario_id written to config/scenario_config.json (default: derived from --out and --seed)")
    parser.add_argument("--wallclock", type=int, default=7200, help="global wall-clock budget recorded in the scenario (seconds)")
    parser.add_argument("--regions", type=int, default=None, help="override the number of sky regions")
    parser.add_argument("--tiles-per-region", type=int, default=None, help="override tiles per region")
    parser.add_argument("--base", type=Path, default=KIT_ROOT / "scenarios" / "dev-reference",
                        help="scenario whose config/*.json are the template (default: scenarios/dev-reference)")
    parser.add_argument("--force", action="store_true", help="replace an existing output directory")
    parser.add_argument("--validate-only", action="store_true", help="do not generate; validate --out with the scorer and print its summary")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if args.validate_only:
        info = describe_scenario(out)
    else:
        if out.exists() and not args.force:
            raise SystemExit(f"{out} already exists; pass --force to replace it")
        if args.seed is None:
            raise SystemExit("--seed is required")
        if args.days < 1:
            raise SystemExit("--days must be at least 1")
        if not (args.base / "config" / "scenario_config.json").is_file():
            raise SystemExit(f"{args.base} is not a scenario directory")
        overrides = {}
        if args.regions:
            overrides["n_regions"] = args.regions
        if args.tiles_per_region:
            overrides["tiles_per_region"] = args.tiles_per_region
        scenario_id = args.scenario_id or f"{out.name}-seed-{args.seed}"
        try:
            info = generate_scenario(out, scenario_id=scenario_id, seed=args.seed, days=args.days, start_date=args.start_date,
                                     global_wallclock_seconds=args.wallclock, tile_overrides=overrides, base=args.base.resolve())
        except ScenarioError as exc:
            raise SystemExit(f"scenario generation failed: {exc}")
        weather = json.loads((out / "config" / "weather_config.json").read_text(encoding="utf-8"))
        info["forecast_horizon_days"] = int(weather["forecast"]["horizon_days"])
    info = {key: value for key, value in info.items() if key != "manifest"}
    info["path"] = str(out)
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
