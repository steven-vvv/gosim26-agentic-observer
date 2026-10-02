#!/usr/bin/env python3
"""Build the checksum and row-count manifest for an example3 scenario."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .contracts import sha256_file, write_text_lf
from .project_paths import CONFIG_DIR, REFERENCE_OUTPUT_DIR


CONFIG_FILES = (
    "scenario_config.json",
    "calendar_config.json",
    "tile_config.json",
    "weather_config.json",
    "request_config.json",
    "workflow_config.json",
    "score_config.json",
)
DATA_FILES = (
    "night_calendar.csv",
    "slots.csv",
    "tiles.csv",
    "targets.csv",
    "tile_windows.csv",
    "weather.csv",
    "weather_forecasts.csv",
    "weather_events.csv",
    "observation_requests.csv",
    "observation_request_tiles.csv",
)


def csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.reader(handle)) - 1


def build(output: Path) -> dict[str, object]:
    scenario = json.loads((CONFIG_DIR / "scenario_config.json").read_text(encoding="utf-8"))
    files = {}
    for name in CONFIG_FILES:
        path = CONFIG_DIR / name
        files[f"config/{name}"] = {"sha256": sha256_file(path)}
    data_names = list(DATA_FILES)
    if (REFERENCE_OUTPUT_DIR / "tile_anomalies.csv").exists():
        data_names.append("tile_anomalies.csv")
    for name in data_names:
        path = REFERENCE_OUTPUT_DIR / name
        files[f"outputs/reference/{name}"] = {
            "sha256": sha256_file(path),
            "rows": csv_rows(path),
        }
    manifest = {
        "schema_version": "example3-scenario-manifest-v2",
        "scenario_id": scenario["scenario_id"],
        "seed": scenario["seed"],
        "global_wallclock_seconds": scenario["competition"]["global_wallclock_seconds"],
        "files": dict(sorted(files.items())),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(output, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


if __name__ == "__main__":
    destination = REFERENCE_OUTPUT_DIR / "scenario_manifest.json"
    print(json.dumps(build(destination), indent=2, sort_keys=True))
