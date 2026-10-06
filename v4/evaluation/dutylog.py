"""Duty-log requests in the style of cards A1-D1 (version 5), added to a generated card.

A1-D1 publish the night staff's handover log as the reason text of zero-reward requests:
the engineers announce each guide-camera move (the instrument fault) days ahead, restate it
in UTC, the absent-minded colleague gets a time wrong and is corrected, rumours name times
that never come, and the flat-lamp tests of each night are listed so that nobody reports
them. This writes such logs from the card's truth faults and flat windows, in the plain
wordings only, so that the log path of the agent (reader, appointments, quiet windows,
report timing) runs end to end through the engine on research cards.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

WEEKDAYS = "一二三四五六日"


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _day(moment: datetime) -> str:
    return f"{moment.month}/{moment.day}"


def _clock(moment: datetime) -> str:
    return f"{moment.hour}:{moment.minute:02d}"


def add_duty_log(card: Path, seed: int, every_nights: int = 3) -> int:
    """Append one log every few nights naming the faults and flat windows of the nights ahead."""
    rng = random.Random(seed)
    site = json.loads((card / "config" / "v4_fiber_config.json").read_text(encoding="utf-8"))["site"]
    offset = timedelta(hours=float(site["utc_offset_hours"]))
    events = list(csv.DictReader((card / "truth" / "v4_events.csv").open(encoding="utf-8")))
    faults = sorted(_utc(row["actual_start_utc"]) for row in events if row["event_type"] == "instrument_fault")
    windows_path = card / "truth" / "v4_flat_windows.csv"
    windows = []
    if windows_path.exists():
        windows = [(row["night_id"], _utc(row["start_utc"]), _utc(row["end_utc"]))
                   for row in csv.DictReader(windows_path.open(encoding="utf-8"))]
    starts: dict[str, datetime] = {}
    for row in csv.DictReader((card / "truth" / "v4_weather_truth.csv").open(encoding="utf-8")):
        starts.setdefault(row["night_id"], _utc(row["timestamp_utc"]))
    nights = sorted(starts)
    targets = [row["target_id"] for row in csv.DictReader((card / "public" / "targets.csv").open(encoding="utf-8"))]
    path = card / "truth" / "v4_observation_requests.jsonl"
    requests = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    added = 0
    for index in range(0, len(nights), every_nights):
        issued = starts[nights[index]]
        until = starts[nights[index + every_nights]] if index + every_nights < len(nights) else issued + timedelta(days=30)
        lines = [("【立希】规矩再讲一次：导星相机一动，数据就全废，直到我们报修才算完，所以到点就报，别等着看数据。"
                  "平场灯、镜盖测试那几段同样是废片，可那不是故障，不许报，报了就是误报。")]
        for fault in (moment for moment in faults if issued <= moment < until):
            local = fault + offset
            weekday = WEEKDAYS[local.weekday()]
            wrong = local + timedelta(minutes=rng.choice((-120, -60, 60, 120)))
            rumour = local + timedelta(minutes=rng.choice((-180, 90, 150)))
            lines += [
                f"【灯】……导星相机那次……是 {_day(wrong)} {_clock(wrong)}……吗？……",
                (f"【祥子】请记录工程组通知：{_day(local)}（周{weekday}）{_clock(local)} 前后动导星相机。"
                 "这是硬件问题，不报修画面就会持续报废，到点请报修。"),
                f"【立希】灯记错了，{_day(wrong)} {_clock(wrong)} 不对，导星相机是 {_day(local)} {_clock(local)}。以工程组通知为准。",
                f"【祥子】同一件事以 UTC 记：导星相机 {_day(fault)}（UTC）{_clock(fault)}。",
                f"【立希】听说 {_day(rumour)} {_clock(rumour)} 导星相机也要动？没确认，先别当真。",
            ]
        for night in nights[index:index + every_nights]:
            spans = sorted((low + offset, high + offset) for name, low, high in windows if name == night)
            if spans:
                evening = starts[night] + offset
                parts = "、".join(f"{_clock(low)}到{_clock(high)}" for low, high in spans)
                lines.append(f"【祥子】{_day(evening)} 夜里有平场灯和镜盖测试，一共 {len(spans)} 段：{parts}。"
                             "拍出来都是废片，但不是故障，请勿报修，报了算误报。")
        requests.append({
            "completion_factor_threshold": 0.5,
            "completion_reward": 0.0,
            "deadline_utc": (issued + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "issued_at_utc": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "minimum_completed": 1,
            "reason": "\n".join(lines),
            "record_type": "observation_request",
            "request_id": f"V4RQ{3001 + added:04d}",
            "schema_version": "v4-observation-request-v1",
            "target_ids": [rng.choice(targets)],
        })
        added += 1
    requests.sort(key=lambda row: (row["issued_at_utc"], row["request_id"]))
    path.write_text("".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in requests),
                    encoding="utf-8")
    return added
