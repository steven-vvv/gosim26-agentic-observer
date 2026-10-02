"""Build, validate and describe challenge scenarios (directories following the example3 layout).

    <root>/config/{scenario,calendar,tile,weather,request,workflow,score}_config.json
    <root>/outputs/reference/{night_calendar,slots,tiles,targets,tile_windows,weather,weather_forecasts,weather_events,
                              observation_requests,observation_request_tiles}.csv + *_metadata.json + scenario_manifest.json
                              (+ optional tile_anomalies.csv when the tile config ships anomaly_tags)
"""
from __future__ import annotations

import csv
import json
import random
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from .build_scenario_manifest import CONFIG_FILES, DATA_FILES, csv_rows
from .contracts import TILE_ANOMALY_COLUMNS, TILE_COLUMNS, read_exact_csv, sha256_file, write_exact_csv, write_text_lf
from .project_paths import EXAMPLE3_ROOT
from .scoring_core import ChallengeScorer

CONFIG_DIRNAME = "config"
DATA_DIRNAME = Path("outputs") / "reference"
ANOMALY_FILE = "tile_anomalies.csv"
PUBLIC_ALWAYS = ("night_calendar.csv", "slots.csv", "tiles.csv", "targets.csv", "tile_windows.csv", "observation_requests.csv",
                 "observation_request_tiles.csv", "scenario_manifest.json", "calendar_metadata.json", "catalog_metadata.json",
                 "observation_request_metadata.json")
HIDDEN_BY_FLAG = {"weather.csv": "weather_public", "weather_metadata.json": "weather_public", "weather_forecasts.csv": "forecasts_public",
                  "weather_events.csv": "events_public"}


class ScenarioError(ValueError):
    pass


def config_dir(root: Path) -> Path:
    return root / CONFIG_DIRNAME


def data_dir(root: Path) -> Path:
    return root / DATA_DIRNAME


def scenario_files(root: Path) -> list[Path]:
    """Every file that belongs to the scenario, in a stable order."""
    files = [config_dir(root) / n for n in CONFIG_FILES]
    files += [data_dir(root) / n for n in DATA_FILES]
    for extra in (ANOMALY_FILE, "tile_windows.csv", "scenario_manifest.json", "calendar_metadata.json", "catalog_metadata.json",
                  "weather_metadata.json", "observation_request_metadata.json"):
        p = data_dir(root) / extra
        if p.exists():
            files.append(p)
    return files


def build_manifest(root: Path) -> dict:
    scenario = json.loads((config_dir(root) / "scenario_config.json").read_text(encoding="utf-8"))
    files = {}
    for name in CONFIG_FILES:
        files[f"config/{name}"] = {"sha256": sha256_file(config_dir(root) / name)}
    data_names = list(DATA_FILES)
    if (data_dir(root) / ANOMALY_FILE).exists():
        data_names.append(ANOMALY_FILE)
    for name in data_names:
        p = data_dir(root) / name
        files[f"outputs/reference/{name}"] = {"sha256": sha256_file(p), "rows": csv_rows(p)}
    manifest = {"schema_version": "example3-scenario-manifest-v2", "scenario_id": scenario["scenario_id"], "seed": scenario["seed"],
                "global_wallclock_seconds": scenario["competition"]["global_wallclock_seconds"], "files": dict(sorted(files.items()))}
    write_text_lf(data_dir(root) / "scenario_manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def _run(module: str, *args: str) -> None:
    cmd = [sys.executable, "-B", "-m", f"challenge.{module}", *args]
    proc = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True)
    if proc.returncode != 0:
        raise ScenarioError(f"{module} failed: {proc.stderr[-2000:] or proc.stdout[-2000:]}")


def generate_tile_anomalies(tile_config: Mapping, tile_ids: Sequence[str]) -> list[dict[str, str]]:
    """Draw hidden nova/reddening tags from the tile config's own seed; the two tag sets may overlap."""
    spec = tile_config.get("anomaly_tags", {})
    rng = random.Random(int(tile_config["seed"]) + 4000)
    rows = []
    for tag, key in (("nova", "nova_count"), ("reddening", "reddening_count")):
        count = min(int(spec.get(key, 0)), len(tile_ids))
        rows.extend({"tile_id": tile_id, "anomaly_tag": tag} for tile_id in rng.sample(sorted(tile_ids), count))
    return sorted(rows, key=lambda row: (row["tile_id"], row["anomaly_tag"]))


def generate_scenario(root: Path, *, scenario_id: str, seed: int, days: int = 180, start_date: str | None = None,
                      global_wallclock_seconds: int = 7200, tile_overrides: dict | None = None, base: Path = EXAMPLE3_ROOT,
                      window_days: int = 3, coverage_bonus_weight: float | None = None,
                      anomaly_overrides: dict | None = None) -> dict:
    """Create a complete scenario directory from the reference configs with a new seed (deterministic)."""
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    config_dir(root).mkdir(parents=True)
    data_dir(root).mkdir(parents=True)
    for name in CONFIG_FILES:
        shutil.copyfile(config_dir(base) / name, config_dir(root) / name)
    # patch seeds / horizon
    def patch(name: str, fn):
        p = config_dir(root) / name
        cfg = json.loads(p.read_text(encoding="utf-8"))
        fn(cfg)
        write_text_lf(p, json.dumps(cfg, indent=2) + "\n")
    def _scn(c):
        c["scenario_id"] = scenario_id; c["seed"] = seed; c["competition"]["global_wallclock_seconds"] = global_wallclock_seconds
    def _cal(c):
        c["survey"]["days"] = days
        if start_date:
            c["survey"]["start_date"] = start_date
    def _tile(c):
        c["seed"] = seed
        # time-limited REQUIRED tiles need their window inside the survey
        c["catalog"]["time_limited_window_days"] = max(1, min(int(c["catalog"].get("time_limited_window_days", 14)), max(1, days - 1)))
        for k, v in (tile_overrides or {}).items():
            c["catalog"][k] = v
        if anomaly_overrides is not None:
            if any(int(v) > 0 for v in anomaly_overrides.values()):
                c.setdefault("anomaly_tags", {}).update({k: int(v) for k, v in anomaly_overrides.items()})
            else:
                c.pop("anomaly_tags", None)
    def _weather(c):
        c["seed"] = seed
        # forecasts need a few nights of headroom beyond their horizon
        c["forecast"]["horizon_days"] = max(1, min(int(c["forecast"].get("horizon_days", 7)), max(1, days - 3)))
    def _req(c):
        c["seed"] = seed
    def _wf(c):
        c["global_wallclock_seconds"] = float(global_wallclock_seconds)
    def _score(c):
        # Absent means zero, so a scenario that never sets it keeps the exact v3 totals it always had.
        if coverage_bonus_weight is not None:
            c["coverage_bonus_weight"] = float(coverage_bonus_weight)
    patch("scenario_config.json", _scn); patch("calendar_config.json", _cal); patch("tile_config.json", _tile)
    patch("weather_config.json", _weather); patch("request_config.json", _req); patch("workflow_config.json", _wf)
    if coverage_bonus_weight is not None:
        patch("score_config.json", _score)
    cd, dd = config_dir(root), data_dir(root)
    _run("observing_calendar", "--config", str(cd / "calendar_config.json"), "--output-dir", str(dd), "generate")
    _run("tile_geometry_simulator", "--tile-config", str(cd / "tile_config.json"), "--calendar-config", str(cd / "calendar_config.json"),
         "--nights", str(dd / "night_calendar.csv"), "--slots", str(dd / "slots.csv"), "--tiles", str(dd / "tiles.csv"), "generate", "--output-dir", str(dd))
    first = json.loads((cd / "calendar_config.json").read_text())["survey"]["start_date"]
    _run("tile_geometry_simulator", "--tile-config", str(cd / "tile_config.json"), "--calendar-config", str(cd / "calendar_config.json"),
         "--nights", str(dd / "night_calendar.csv"), "--slots", str(dd / "slots.csv"), "--tiles", str(dd / "tiles.csv"),
         "windows", "--date", first, "--days", str(window_days), "--output", str(dd / "tile_windows.csv"))
    _run("weather_simulator", "--config", str(cd / "weather_config.json"), "--calendar-config", str(cd / "calendar_config.json"),
         "--tile-config", str(cd / "tile_config.json"), "--nights", str(dd / "night_calendar.csv"), "--slots", str(dd / "slots.csv"),
         "--tiles", str(dd / "tiles.csv"), "generate", "--output-dir", str(dd))
    tile_config = json.loads((cd / "tile_config.json").read_text(encoding="utf-8"))
    tile_ids = [row["tile_id"] for row in read_exact_csv(dd / "tiles.csv", TILE_COLUMNS)]
    anomaly_rows = generate_tile_anomalies(tile_config, tile_ids)
    if anomaly_rows:
        write_exact_csv(dd / ANOMALY_FILE, TILE_ANOMALY_COLUMNS, anomaly_rows)
    _run("observation_request_simulator", "--config", str(cd / "request_config.json"), "--nights", str(dd / "night_calendar.csv"),
         "--tiles", str(dd / "tiles.csv"), "generate", "--output-dir", str(dd))
    build_manifest(root)
    return describe_scenario(root)


def describe_scenario(root: Path) -> dict:
    """Validate a scenario directory with the authoritative scorer and return counts + manifest."""
    root = Path(root)
    missing = [str(p.relative_to(root)) for p in [config_dir(root) / n for n in CONFIG_FILES] + [data_dir(root) / n for n in DATA_FILES] if not p.exists()]
    if missing:
        raise ScenarioError("missing scenario files: " + ", ".join(missing))
    try:
        scorer = ChallengeScorer.from_files(root)
    except Exception as exc:  # noqa: BLE001
        raise ScenarioError(f"scenario rejected by the scorer: {exc}") from exc
    manifest_path = data_dir(root) / "scenario_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else build_manifest(root)
    def rows(name):
        return csv_rows(data_dir(root) / name)
    nights = rows("night_calendar.csv")
    scenario_cfg = json.loads((config_dir(root) / "scenario_config.json").read_text(encoding="utf-8"))
    calendar_cfg = json.loads((config_dir(root) / "calendar_config.json").read_text(encoding="utf-8"))
    info = {
        "scenario_id": scenario_cfg["scenario_id"], "seed": scenario_cfg["seed"],
        "global_wallclock_seconds": int(scenario_cfg["competition"]["global_wallclock_seconds"]),
        "n_nights": nights, "n_slots": rows("slots.csv"), "n_tiles": rows("tiles.csv"), "n_targets": rows("targets.csv"),
        "n_requests": rows("observation_requests.csv"), "n_events": rows("weather_events.csv"), "manifest": manifest,
        "checksum": sha256_file(manifest_path) if manifest_path.exists() else "",
        "contract": "challenge-score-v3",
        # first observing night, so a rotation can rebuild the scenario on the same calendar
        "start_date": calendar_cfg.get("survey", {}).get("start_date"),
    }
    if (data_dir(root) / ANOMALY_FILE).exists():
        info["n_anomaly_tags"] = csv_rows(data_dir(root) / ANOMALY_FILE)
    return info


def public_relpaths(flags: dict) -> list[str]:
    """Relative paths a participant may download given the scenario visibility flags."""
    out = [f"config/{n}" for n in CONFIG_FILES] + [f"outputs/reference/{n}" for n in PUBLIC_ALWAYS]
    for name, flag in HIDDEN_BY_FLAG.items():
        if flags.get(flag):
            out.append(f"outputs/reference/{name}")
    return out


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Generate or validate a challenge scenario directory.")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("root", type=Path); g.add_argument("--scenario-id", required=True); g.add_argument("--seed", type=int, required=True)
    g.add_argument("--days", type=int, default=180); g.add_argument("--start-date"); g.add_argument("--wallclock", type=int, default=7200)
    g.add_argument("--regions", type=int); g.add_argument("--tiles-per-region", type=int)
    g.add_argument("--coverage-weight", type=float, default=None)
    g.add_argument("--nova-tags", type=int, default=None); g.add_argument("--reddening-tags", type=int, default=None)
    v = sub.add_parser("validate"); v.add_argument("root", type=Path)
    a = p.parse_args(argv)
    if a.cmd == "generate":
        ov = {}
        if a.regions: ov["n_regions"] = a.regions
        if a.tiles_per_region: ov["tiles_per_region"] = a.tiles_per_region
        an = {}
        if a.nova_tags is not None: an["nova_count"] = a.nova_tags
        if a.reddening_tags is not None: an["reddening_count"] = a.reddening_tags
        info = generate_scenario(a.root, scenario_id=a.scenario_id, seed=a.seed, days=a.days, start_date=a.start_date,
                                 global_wallclock_seconds=a.wallclock, tile_overrides=ov,
                                 coverage_bonus_weight=a.coverage_weight, anomaly_overrides=an or None)
    else:
        info = describe_scenario(a.root)
    info = {k: v for k, v in info.items() if k != "manifest"}
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
