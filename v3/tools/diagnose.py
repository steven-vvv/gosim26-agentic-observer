#!/usr/bin/env python3
"""把一次运行的观测质量损失拆成 airmass、月光、天气三部分。

    uv run python tools/diagnose.py runs/eval/diag-obs1/comp-a-s101 scenarios/comp-a-s101

需要运行目录里的 score_report.json（用 evaluate.py --keep 保留）。每块天区能达到的最小 airmass 按
上中天高度估算：sec(|台址纬度 − 赤纬|)。
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
import sys
from collections import Counter
from pathlib import Path


def seg_avg(segs, key) -> float:
    """按分段时长加权平均。"""
    return sum(s[key] * s["duration_seconds"] for s in segs) / sum(s["duration_seconds"] for s in segs)


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def main(run_dir: Path, scenario: Path) -> None:
    report = json.loads((run_dir / "score_report.json").read_text(encoding="utf-8"))
    ref = scenario / "outputs" / "reference"
    latitude = json.loads((scenario / "config" / "calendar_config.json").read_text())["site"]["latitude_deg"]
    with (ref / "tiles.csv").open(encoding="utf-8-sig") as handle:
        tiles = {row["tile_id"]: row for row in csv.DictReader(handle)}
    with (ref / "weather.csv").open(encoding="utf-8-sig") as handle:
        weather = [row for row in csv.DictReader(handle) if row["is_observable"] == "true"]
    site_q = [float(w["transparency"]) * float(w["sky_quality"]) / float(w["seeing_arcsec"]) for w in weather]

    rows = []
    for action in report["actions"]:
        if action["outcome"] != "completed" or not action["segments"]:
            continue
        segs = action["segments"]
        tile = tiles[action["tile_id"]]
        best_airmass = 1.0 / math.cos(math.radians(abs(latitude - float(tile["dec_deg"]))))
        airmass = seg_avg(segs, "airmass")
        rows.append({
            "airmass": airmass, "best_airmass": best_airmass, "airmass_loss": best_airmass / airmass,
            "lunar": seg_avg(segs, "lunar_quality_factor"), "atm_no_airmass": seg_avg(segs, "atmospheric_quality") * airmass,
            "combined": seg_avg(segs, "combined_quality"), "matched": sum(s["program_matched"] for s in segs) / len(segs),
            "band": Counter(s["quality_band"] for s in segs).most_common(1)[0][0],
            "exptime": int(tile["nominal_exptime_seconds"]),
        })
    print(f"完成的曝光: {len(rows)}")
    for key, label in (("airmass", "观测时 airmass"), ("best_airmass", "该天区最小可达 airmass"),
                       ("airmass_loss", "airmass 损失系数 best/obs"), ("lunar", "月光因子"),
                       ("atm_no_airmass", "天气项 T·S·eff/seeing"), ("combined", "综合质量")):
        values = [r[key] for r in rows]
        print(f"  {label:<24} 均值 {st.mean(values):.3f}  P10 {pct(values, .1):.3f}  中位 {pct(values, .5):.3f}  P90 {pct(values, .9):.3f}")
    print(f"  整场开放时隙的天气项 T·S/seeing: 均值 {st.mean(site_q):.3f}  P90 {pct(site_q, .9):.3f}（不含效率）")
    print(f"  档位分布: {dict(Counter(r['band'] for r in rows))}  档位匹配率: {st.mean(r['matched'] for r in rows):.3f}")
    print(f"  曝光时长分布: {dict(sorted(Counter(r['exptime'] for r in rows).items()))}")
    moon = [r for r in rows if r["lunar"] < 0.9]
    print(f"  月光因子 < 0.9 的曝光: {len(moon)} ({len(moon) / len(rows):.1%})，这些曝光的平均月光因子 {st.mean(r['lunar'] for r in moon) if moon else 0:.3f}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
