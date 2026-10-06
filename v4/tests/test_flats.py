"""Tests for v4.evaluation.flats: nightly flat-field / dome-cover windows."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from v4.evaluation.flats import add_flat_windows

COLUMNS = (
    "slot_id",
    "night_id",
    "timestamp_utc",
    "duration_seconds",
    "is_observable",
    "seeing_arcsec",
    "transparency",
    "sky_quality",
    "instrument_efficiency",
)


def _make_weather_truth(path: Path, nights: int, slots_per_night: int) -> list[dict]:
    rows = []
    start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    for night in range(nights):
        night_id = f"N2026{10:02d}{night + 1:02d}"
        for slot in range(slots_per_night):
            moment = start + timedelta(days=night, minutes=15 * slot)
            rows.append({
                "slot_id": f"{night_id}-S{slot + 1:03d}",
                "night_id": night_id,
                "timestamp_utc": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_seconds": "900",
                "is_observable": "true",
                "seeing_arcsec": "1.000000",
                "transparency": "0.900000",
                "sky_quality": "1.000000",
                "instrument_efficiency": "1.000000",
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_window_count_matches_nights_times_per_night(tmp_path: Path) -> None:
    card = tmp_path / "card"
    truth = card / "truth" / "v4_weather_truth.csv"
    _make_weather_truth(truth, nights=5, slots_per_night=20)

    windows = add_flat_windows(card, seed=1, per_night=3)

    assert len(windows) == 5 * 3
    nights_seen = {w["night_id"] for w in windows}
    assert len(nights_seen) == 5
    for night_id in nights_seen:
        assert sum(1 for w in windows if w["night_id"] == night_id) == 3


def test_windows_within_night_do_not_overlap(tmp_path: Path) -> None:
    card = tmp_path / "card"
    truth = card / "truth" / "v4_weather_truth.csv"
    _make_weather_truth(truth, nights=4, slots_per_night=16)

    windows = add_flat_windows(card, seed=42, per_night=3)

    by_night: dict[str, list[tuple[datetime, datetime]]] = {}
    for w in windows:
        start = datetime.fromisoformat(w["start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(w["end_utc"].replace("Z", "+00:00"))
        assert end > start
        by_night.setdefault(w["night_id"], []).append((start, end))
    for spans in by_night.values():
        spans.sort()
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            assert e1 <= s2


def test_only_selected_slots_scaled_and_other_columns_unchanged(tmp_path: Path) -> None:
    card = tmp_path / "card"
    truth = card / "truth" / "v4_weather_truth.csv"
    original_rows = _make_weather_truth(truth, nights=3, slots_per_night=12)

    windows = add_flat_windows(card, seed=7, per_night=2, factor=0.005)

    updated_rows = _read_rows(truth)
    assert [row["slot_id"] for row in updated_rows] == [row["slot_id"] for row in original_rows]

    selected_timestamps: set[str] = set()
    for w in windows:
        start = datetime.fromisoformat(w["start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(w["end_utc"].replace("Z", "+00:00"))
        cursor = start
        while cursor < end:
            selected_timestamps.add(cursor.strftime("%Y-%m-%dT%H:%M:%SZ"))
            cursor += timedelta(minutes=15)

    touched = 0
    for before, after in zip(original_rows, updated_rows):
        for key in ("slot_id", "night_id", "timestamp_utc", "duration_seconds", "is_observable",
                    "seeing_arcsec", "transparency", "sky_quality"):
            assert before[key] == after[key]
        if before["timestamp_utc"] in selected_timestamps:
            touched += 1
            expected = float(before["instrument_efficiency"]) * 0.005
            assert after["instrument_efficiency"] == f"{expected:.6f}"
        else:
            assert after["instrument_efficiency"] == before["instrument_efficiency"]
    assert touched == len(selected_timestamps)
    assert touched > 0


def test_deterministic_for_same_seed(tmp_path: Path) -> None:
    card_a = tmp_path / "a"
    card_b = tmp_path / "b"
    _make_weather_truth(card_a / "truth" / "v4_weather_truth.csv", nights=6, slots_per_night=24)
    _make_weather_truth(card_b / "truth" / "v4_weather_truth.csv", nights=6, slots_per_night=24)

    windows_a = add_flat_windows(card_a, seed=99, per_night=3)
    windows_b = add_flat_windows(card_b, seed=99, per_night=3)

    assert windows_a == windows_b
    assert _read_rows(card_a / "truth" / "v4_weather_truth.csv") == _read_rows(
        card_b / "truth" / "v4_weather_truth.csv"
    )


def test_different_seed_can_change_windows(tmp_path: Path) -> None:
    card_a = tmp_path / "a"
    card_b = tmp_path / "b"
    _make_weather_truth(card_a / "truth" / "v4_weather_truth.csv", nights=6, slots_per_night=24)
    _make_weather_truth(card_b / "truth" / "v4_weather_truth.csv", nights=6, slots_per_night=24)

    windows_a = add_flat_windows(card_a, seed=1, per_night=3)
    windows_b = add_flat_windows(card_b, seed=2, per_night=3)

    assert windows_a != windows_b


def test_flat_windows_csv_written(tmp_path: Path) -> None:
    card = tmp_path / "card"
    _make_weather_truth(card / "truth" / "v4_weather_truth.csv", nights=2, slots_per_night=10)

    windows = add_flat_windows(card, seed=5, per_night=2)

    rows = _read_rows(card / "truth" / "v4_flat_windows.csv")
    assert [row["night_id"] for row in rows] == [w["night_id"] for w in windows]
    assert [row["start_utc"] for row in rows] == [w["start_utc"] for w in windows]
    assert [row["end_utc"] for row in rows] == [w["end_utc"] for w in windows]
