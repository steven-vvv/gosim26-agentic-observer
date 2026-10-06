"""Nightly "flat-field / dome-cover" windows in the style of A1-D1 (version 6).

The organizer's engineering team runs short flat-field-lamp / dome-cover tests during
the night: three 15-30 minute windows with the frame effectively blank (instrument
efficiency multiplied down to about 0.005), picked at an arbitrary point in the night.
Unlike an instrument fault this is not a malfunction and is never reported or repaired;
it is simply overlaid on the truth weather so a research card can reproduce it for the
agent to learn to tolerate (rather than mis-diagnose as a fault).

Windows are chosen uniformly at random over each night's slots, deterministically from
``seed``, and never overlap each other. Only the ``instrument_efficiency`` column of the
selected slots is touched; every other column and the row order are left unchanged.
"""

from __future__ import annotations

import csv
import os
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIELDS = ("night_id", "start_utc", "end_utc")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _format(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _choose_windows(rng: random.Random, night_length: int, per_night: int,
                     slots_range: tuple[int, int]) -> list[tuple[int, int]]:
    """``per_night`` non-overlapping [start, end) slot-index windows within one night."""
    low, high = slots_range
    for _ in range(2000):
        windows: list[tuple[int, int]] = []
        ok = True
        for _ in range(per_night):
            length = rng.randint(low, high)
            if length > night_length:
                ok = False
                break
            start = rng.randint(0, night_length - length)
            end = start + length
            if any(start < w_end and end > w_start for w_start, w_end in windows):
                ok = False
                break
            windows.append((start, end))
        if ok:
            return sorted(windows)
    raise ValueError(f"could not place {per_night} non-overlapping windows in a {night_length}-slot night")


def _atomic_write(path: Path, write) -> None:
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}-", suffix=".tmp",
                                      encoding="utf-8", newline="", delete=False) as handle:
        write(handle)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def add_flat_windows(card: Path, seed: int, per_night: int, slots_range: tuple[int, int] = (1, 2),
                      factor: float = 0.005) -> list[dict]:
    rng = random.Random(seed)
    path = card / "truth" / "v4_weather_truth.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        rows = list(reader)
    nights: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        nights.setdefault(row["night_id"], []).append(index)
    windows: list[dict] = []
    for night_id in sorted(nights):
        indices = nights[night_id]
        for start, end in _choose_windows(rng, len(indices), per_night, slots_range):
            selected = indices[start:end]
            for row_index in selected:
                row = rows[row_index]
                if row["instrument_efficiency"]:
                    row["instrument_efficiency"] = f"{float(row['instrument_efficiency']) * factor:.6f}"
            first_row = rows[selected[0]]
            last_row = rows[selected[-1]]
            end_utc = _utc(last_row["timestamp_utc"]) + timedelta(seconds=int(last_row["duration_seconds"]))
            windows.append({
                "night_id": night_id,
                "start_utc": first_row["timestamp_utc"],
                "end_utc": _format(end_utc),
            })

    def write_weather(handle):
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    _atomic_write(path, write_weather)

    def write_windows(handle):
        writer = csv.DictWriter(handle, fieldnames=list(FIELDS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(windows)

    _atomic_write(card / "truth" / "v4_flat_windows.csv", write_windows)
    return windows
