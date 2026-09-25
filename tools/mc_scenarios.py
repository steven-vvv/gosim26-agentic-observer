#!/usr/bin/env python3
"""蒙特卡洛场景：真实的决赛天区目录 + 我们自己的随机天气与隐藏标签。

    uv run python tools/mc_scenarios.py --base data/scenarios/eval-a --prefix mc-a --seeds 9001 9002 9003

公开文件（天区、目标、日历、时隙、窗口、临时请求）原样复用，大文件用符号链接；只把天气配置与天区配置里的种子
换成给定值，重新生成天气真值、预报、方向性事件（含故障）与隐藏标签。天区目录不会重新生成。
决赛配置文件里带有的 seed 字段不得使用（见 data/README.md），脚本遇到会直接拒绝。
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))

from challenge import weather_simulator  # 官方生成器
from challenge.contracts import TILE_ANOMALY_COLUMNS, write_exact_csv
from challenge.scenario_builder import generate_tile_anomalies

PUBLIC = ("night_calendar.csv", "slots.csv", "tiles.csv", "targets.csv", "tile_windows.csv",
          "observation_requests.csv", "observation_request_tiles.csv")


def build(base: Path, out: Path, seed: int) -> None:
    forbidden = {int(json.loads((base / "config" / name).read_text())["seed"])
                 for name in ("scenario_config.json", "weather_config.json", "tile_config.json")}
    if seed in forbidden:
        raise SystemExit(f"seed {seed} 是决赛配置里的种子，不能使用")
    if out.exists():
        shutil.rmtree(out)
    (out / "config").mkdir(parents=True)
    ref = out / "outputs" / "reference"
    ref.mkdir(parents=True)
    for path in (base / "config").glob("*.json"):
        config = json.loads(path.read_text(encoding="utf-8"))
        if path.name in ("weather_config.json", "tile_config.json", "scenario_config.json"):
            config["seed"] = seed
        if path.name == "scenario_config.json":
            config["scenario_id"] = out.name
        (out / "config" / path.name).write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name in PUBLIC:
        (ref / name).symlink_to((base / "outputs" / "reference" / name).resolve())
    weather_simulator.generate(out / "config" / "weather_config.json", ref / "night_calendar.csv", ref / "slots.csv",
                               ref / "tiles.csv", ref)
    tile_config = json.loads((out / "config" / "tile_config.json").read_text(encoding="utf-8"))
    with (ref / "tiles.csv").open(encoding="utf-8-sig") as handle:
        tile_ids = [row["tile_id"] for row in csv.DictReader(handle)]
    rows = generate_tile_anomalies(tile_config, tile_ids)
    if rows:
        write_exact_csv(ref / "tile_anomalies.csv", TILE_ANOMALY_COLUMNS, rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    args = parser.parse_args(argv)
    for seed in args.seeds:
        out = ROOT / "scenarios" / f"{args.prefix}-{seed}"
        build(args.base.resolve(), out, seed)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
