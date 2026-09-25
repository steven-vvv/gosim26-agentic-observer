#!/usr/bin/env python3
"""Generate and validate the shared, site-configured observing calendar."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from .contracts import (
    write_text_lf,
    NIGHT_COLUMNS,
    SLOT_COLUMNS,
    format_utc,
    parse_utc,
    read_exact_csv,
    sha256_file,
    write_exact_csv,
)
from .project_paths import CONFIG_DIR, REFERENCE_OUTPUT_DIR


SCHEMA_VERSION = "observing-calendar-v1"


@dataclass(frozen=True)
class Night:
    night_id: str
    night_date: date
    solar_dusk_utc: datetime
    solar_dawn_utc: datetime
    observing_start_utc: datetime
    observing_end_utc: datetime
    slot_count: int

    @property
    def night_seconds(self) -> int:
        return int((self.observing_end_utc - self.observing_start_utc).total_seconds())

    def csv_row(self) -> dict[str, object]:
        return {
            "night_id": self.night_id,
            "night_date": self.night_date.isoformat(),
            "solar_dusk_utc": format_utc(self.solar_dusk_utc),
            "solar_dawn_utc": format_utc(self.solar_dawn_utc),
            "observing_start_utc": format_utc(self.observing_start_utc),
            "observing_end_utc": format_utc(self.observing_end_utc),
            "night_seconds": self.night_seconds,
            "slot_count": self.slot_count,
        }


@dataclass(frozen=True)
class Slot:
    slot_id: str
    night_id: str
    timestamp_utc: datetime
    duration_seconds: int

    @property
    def end_utc(self) -> datetime:
        return self.timestamp_utc + timedelta(seconds=self.duration_seconds)

    def csv_row(self) -> dict[str, object]:
        return {
            "slot_id": self.slot_id,
            "night_id": self.night_id,
            "timestamp_utc": format_utc(self.timestamp_utc),
            "duration_seconds": self.duration_seconds,
        }


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    validate_config(config)
    return config


def validate_config(config: Mapping) -> None:
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported calendar schema_version")
    if set(config) != {"schema_version", "survey", "site", "solver"}:
        raise ValueError("calendar config must define schema_version, survey, site, solver")
    survey = config["survey"]
    date.fromisoformat(str(survey["start_date"]))
    if int(survey["days"]) < 1:
        raise ValueError("survey days must be positive")
    slot_seconds = int(survey["slot_seconds"])
    if slot_seconds < 1 or 86400 % slot_seconds:
        raise ValueError("slot_seconds must be positive and divide one day")
    site = config["site"]
    if not -90.0 <= float(site["latitude_deg"]) <= 90.0:
        raise ValueError("site latitude is outside [-90, 90]")
    if not -180.0 < float(site["longitude_deg"]) <= 180.0:
        raise ValueError("site longitude is outside (-180, 180]")
    if not -14.0 <= float(site["utc_offset_hours"]) <= 14.0:
        raise ValueError("UTC offset is outside [-14, 14]")
    if not -90.0 < float(site["sun_altitude_limit_deg"]) < 0.0:
        raise ValueError("sun altitude limit must lie in (-90, 0)")
    solver = config["solver"]
    coarse = int(solver["coarse_step_seconds"])
    tolerance = int(solver["crossing_tolerance_seconds"])
    if coarse < 10 or tolerance < 1 or tolerance >= coarse:
        raise ValueError("invalid crossing solver step or tolerance")


def _julian_date(moment: datetime) -> float:
    return moment.timestamp() / 86400.0 + 2440587.5


def _sun_equatorial_deg(moment: datetime) -> tuple[float, float]:
    days = _julian_date(moment) - 2451545.0
    mean_longitude = (280.460 + 0.9856474 * days) % 360.0
    mean_anomaly = math.radians((357.528 + 0.9856003 * days) % 360.0)
    ecliptic_longitude = math.radians(
        (mean_longitude + 1.915 * math.sin(mean_anomaly) + 0.020 * math.sin(2 * mean_anomaly))
        % 360.0
    )
    obliquity = math.radians(23.439 - 0.0000004 * days)
    ra = math.degrees(
        math.atan2(
            math.cos(obliquity) * math.sin(ecliptic_longitude),
            math.cos(ecliptic_longitude),
        )
    ) % 360.0
    dec = math.degrees(math.asin(math.sin(obliquity) * math.sin(ecliptic_longitude)))
    return ra, dec


def _local_sidereal_deg(moment: datetime, longitude_deg: float) -> float:
    days = _julian_date(moment) - 2451545.0
    return (280.46061837 + 360.98564736629 * days + longitude_deg) % 360.0


def _equatorial_altitude_deg(
    ra_deg: float,
    dec_deg: float,
    moment: datetime,
    latitude_deg: float,
    longitude_deg: float,
) -> float:
    hour_angle = math.radians(
        (_local_sidereal_deg(moment, longitude_deg) - ra_deg + 180.0) % 360.0 - 180.0
    )
    latitude = math.radians(latitude_deg)
    declination = math.radians(dec_deg)
    sin_altitude = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_altitude))))


def sun_altitude_deg(moment: datetime, config: Mapping) -> float:
    ra, dec = _sun_equatorial_deg(moment)
    site = config["site"]
    return _equatorial_altitude_deg(
        ra,
        dec,
        moment,
        float(site["latitude_deg"]),
        float(site["longitude_deg"]),
    )


def _crossing(
    left: datetime,
    right: datetime,
    threshold: float,
    config: Mapping,
) -> datetime:
    left_below = sun_altitude_deg(left, config) <= threshold
    tolerance = int(config["solver"]["crossing_tolerance_seconds"])
    while (right - left).total_seconds() > tolerance:
        middle = left + (right - left) / 2
        if (sun_altitude_deg(middle, config) <= threshold) == left_below:
            left = middle
        else:
            right = middle
    return right.replace(microsecond=0)


def _ceil_epoch(moment: datetime, quantum: int) -> datetime:
    seconds = int(moment.timestamp())
    aligned = ((seconds + quantum - 1) // quantum) * quantum
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


def _floor_epoch(moment: datetime, quantum: int) -> datetime:
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp((seconds // quantum) * quantum, tz=timezone.utc)


def _night_crossings(observing_date: date, config: Mapping) -> tuple[datetime, datetime]:
    site = config["site"]
    local_zone = timezone(timedelta(hours=float(site["utc_offset_hours"])))
    local_noon = datetime.combine(observing_date, time(hour=12), tzinfo=local_zone)
    start = local_noon.astimezone(timezone.utc)
    end = start + timedelta(days=1)
    threshold = float(site["sun_altitude_limit_deg"])
    step = timedelta(seconds=int(config["solver"]["coarse_step_seconds"]))
    crossings: list[tuple[datetime, bool]] = []
    previous = start
    previous_below = sun_altitude_deg(previous, config) <= threshold
    cursor = previous + step
    while cursor <= end:
        below = sun_altitude_deg(cursor, config) <= threshold
        if below != previous_below:
            crossings.append((_crossing(previous, cursor, threshold, config), below))
        previous = cursor
        previous_below = below
        cursor += step
    dusk = next((moment for moment, entered_below in crossings if entered_below), None)
    dawn = next(
        (moment for moment, entered_below in crossings if not entered_below and dusk and moment > dusk),
        None,
    )
    if dusk is None or dawn is None:
        raise ValueError(
            f"no complete observing night for {observing_date.isoformat()} at configured site"
        )
    return dusk, dawn


def build_calendar(config: Mapping) -> tuple[list[Night], list[Slot]]:
    survey = config["survey"]
    first = date.fromisoformat(str(survey["start_date"]))
    slot_seconds = int(survey["slot_seconds"])
    nights: list[Night] = []
    slots: list[Slot] = []
    for offset in range(int(survey["days"])):
        observing_date = first + timedelta(days=offset)
        dusk, dawn = _night_crossings(observing_date, config)
        observing_start = _ceil_epoch(dusk, slot_seconds)
        observing_end = _floor_epoch(dawn, slot_seconds)
        if observing_end <= observing_start:
            raise ValueError(f"no full slots for {observing_date.isoformat()}")
        count = int((observing_end - observing_start).total_seconds()) // slot_seconds
        night_id = f"N{observing_date.strftime('%Y%m%d')}"
        night = Night(
            night_id,
            observing_date,
            dusk,
            dawn,
            observing_start,
            observing_end,
            count,
        )
        nights.append(night)
        for index in range(count):
            slots.append(
                Slot(
                    slot_id=f"{night_id}-S{index + 1:03d}",
                    night_id=night_id,
                    timestamp_utc=observing_start + timedelta(seconds=index * slot_seconds),
                    duration_seconds=slot_seconds,
                )
            )
    return nights, slots


def load_nights(path: Path) -> list[Night]:
    result: list[Night] = []
    for line, row in enumerate(read_exact_csv(path, NIGHT_COLUMNS), start=2):
        try:
            result.append(
                Night(
                    row["night_id"].strip(),
                    date.fromisoformat(row["night_date"]),
                    parse_utc(row["solar_dusk_utc"]),
                    parse_utc(row["solar_dawn_utc"]),
                    parse_utc(row["observing_start_utc"]),
                    parse_utc(row["observing_end_utc"]),
                    int(row["slot_count"]),
                )
            )
        except ValueError as exc:
            raise ValueError(f"{path}: row {line}: {exc}") from exc
    return result


def load_slots(path: Path) -> list[Slot]:
    result: list[Slot] = []
    for line, row in enumerate(read_exact_csv(path, SLOT_COLUMNS), start=2):
        try:
            result.append(
                Slot(
                    row["slot_id"].strip(),
                    row["night_id"].strip(),
                    parse_utc(row["timestamp_utc"]),
                    int(row["duration_seconds"]),
                )
            )
        except ValueError as exc:
            raise ValueError(f"{path}: row {line}: {exc}") from exc
    return result


def validate_calendar(nights: Sequence[Night], slots: Sequence[Slot]) -> dict[str, object]:
    if not nights or not slots:
        raise ValueError("calendar must contain nights and slots")
    if len({night.night_id for night in nights}) != len(nights):
        raise ValueError("night IDs are not unique")
    if len({slot.slot_id for slot in slots}) != len(slots):
        raise ValueError("slot IDs are not unique")
    if list(slots) != sorted(slots, key=lambda item: item.timestamp_utc):
        raise ValueError("slots are not chronological")
    by_night: dict[str, list[Slot]] = {night.night_id: [] for night in nights}
    for slot in slots:
        if slot.night_id not in by_night:
            raise ValueError(f"slot references unknown night {slot.night_id}")
        by_night[slot.night_id].append(slot)
    for night in nights:
        owned = by_night[night.night_id]
        if len(owned) != night.slot_count:
            raise ValueError(f"slot count mismatch for {night.night_id}")
        if night.night_seconds != sum(slot.duration_seconds for slot in owned):
            raise ValueError(f"night duration mismatch for {night.night_id}")
        if owned[0].timestamp_utc != night.observing_start_utc:
            raise ValueError(f"first slot mismatch for {night.night_id}")
        if owned[-1].end_utc != night.observing_end_utc:
            raise ValueError(f"last slot mismatch for {night.night_id}")
        if not (
            night.solar_dusk_utc <= night.observing_start_utc
            < night.observing_end_utc <= night.solar_dawn_utc
        ):
            raise ValueError(f"observing interval lies outside solar night for {night.night_id}")
        for left, right in zip(owned, owned[1:]):
            if left.end_utc != right.timestamp_utc:
                raise ValueError(f"non-contiguous slots in {night.night_id}")
    counts = [night.slot_count for night in nights]
    return {
        "status": "valid",
        "night_count": len(nights),
        "slot_count": len(slots),
        "minimum_slots_per_night": min(counts),
        "maximum_slots_per_night": max(counts),
    }


def generate(config_path: Path, output_dir: Path) -> dict[str, object]:
    config = load_config(config_path)
    nights, slots = build_calendar(config)
    report = validate_calendar(nights, slots)
    output_dir.mkdir(parents=True, exist_ok=True)
    nights_path = output_dir / "night_calendar.csv"
    slots_path = output_dir / "slots.csv"
    write_exact_csv(nights_path, NIGHT_COLUMNS, (night.csv_row() for night in nights))
    write_exact_csv(slots_path, SLOT_COLUMNS, (slot.csv_row() for slot in slots))
    metadata = {
        "schema_version": "observing-calendar-metadata-v1",
        "generator": "observing_calendar.py",
        "row_counts": {"nights": len(nights), "slots": len(slots)},
        "slot_count_range": [
            report["minimum_slots_per_night"],
            report["maximum_slots_per_night"],
        ],
        "sha256": {
            "config": sha256_file(config_path),
            "night_calendar": sha256_file(nights_path),
            "slots": sha256_file(slots_path),
        },
        "semantics": {
            "night_source": "configured site coordinates and solar altitude",
            "slot_alignment": "full slots wholly inside [solar_dusk, solar_dawn)",
            "intervals": "half-open [start, end)",
        },
    }
    metadata_path = output_dir / "calendar_metadata.json"
    write_text_lf(metadata_path, json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def _validate_files(config_path: Path, output_dir: Path) -> dict[str, object]:
    config = load_config(config_path)
    nights = load_nights(output_dir / "night_calendar.csv")
    slots = load_slots(output_dir / "slots.csv")
    report = validate_calendar(nights, slots)
    slot_seconds = int(config["survey"]["slot_seconds"])
    if any(slot.duration_seconds != slot_seconds for slot in slots):
        raise ValueError("slot duration does not match calendar config")
    if len(nights) != int(config["survey"]["days"]):
        raise ValueError("night count does not match calendar config")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "calendar_config.json")
    parser.add_argument("--output-dir", type=Path, default=REFERENCE_OUTPUT_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("generate", help="generate calendar CSV and metadata")
    subparsers.add_parser("validate", help="validate existing calendar files")
    args = parser.parse_args()
    if args.command == "generate":
        result = generate(args.config, args.output_dir)
    else:
        result = _validate_files(args.config, args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
