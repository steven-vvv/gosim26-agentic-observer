#!/usr/bin/env python3
"""Generate tiles and publish calendar-aligned tile geometry and windows."""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import (
    write_text_lf,
    TARGET_COLUMNS,
    TILE_COLUMNS,
    TILE_WINDOW_COLUMNS,
    format_utc,
    parse_utc,
    read_exact_csv,
    sha256_file,
    write_exact_csv,
)
from .observing_calendar import Night, Slot, load_nights, load_slots
from .project_paths import CONFIG_DIR, REFERENCE_OUTPUT_DIR


SCHEMA_VERSION = "tile-geometry-v2"
TARGET_CLASSES = ("LRG", "ELG", "QSO", "BGS")


@dataclass(frozen=True)
class Tile:
    tile_id: str
    ra_deg: float
    dec_deg: float
    nominal_exptime_seconds: int
    region_id: str
    scheduling_class: str
    available_from_utc: datetime
    available_until_utc: datetime
    n_lrg: int = 0
    n_elg: int = 0
    n_qso: int = 0
    n_bgs: int = 0

    def csv_row(self) -> dict[str, object]:
        return {
            "tile_id": self.tile_id,
            "ra_deg": f"{self.ra_deg:.6f}",
            "dec_deg": f"{self.dec_deg:.6f}",
            "nominal_exptime_seconds": self.nominal_exptime_seconds,
            "region_id": self.region_id,
            "scheduling_class": self.scheduling_class,
            "available_from_utc": format_utc(self.available_from_utc),
            "available_until_utc": format_utc(self.available_until_utc),
            "n_lrg": self.n_lrg,
            "n_elg": self.n_elg,
            "n_qso": self.n_qso,
            "n_bgs": self.n_bgs,
        }


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    validate_config(config)
    return config


def validate_config(config: Mapping) -> None:
    required = {"schema_version", "seed", "geometry", "catalog", "lunar_model", "target_models"}
    # anomaly_tags is optional: absent means the scenario ships no hidden tile tags.
    if not required <= set(config) <= required | {"anomaly_tags"} or config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid tile geometry config keys or schema_version")
    catalog = config["catalog"]
    for key in (
        "n_regions",
        "tiles_per_region",
        "required_per_region",
        "time_limited_required_per_region",
        "time_limited_window_days",
    ):
        if int(catalog[key]) < 0:
            raise ValueError(f"catalog {key} must be non-negative")
    if int(catalog["n_regions"]) < 1 or int(catalog["tiles_per_region"]) < 1:
        raise ValueError("catalog must contain regions and tiles")
    if int(catalog["required_per_region"]) > int(catalog["tiles_per_region"]):
        raise ValueError("required_per_region exceeds tiles_per_region")
    if int(catalog["time_limited_required_per_region"]) > int(catalog["required_per_region"]):
        raise ValueError("time-limited required count exceeds required count")
    if not catalog["nominal_exptime_choices_seconds"] or any(
        int(value) <= 0 for value in catalog["nominal_exptime_choices_seconds"]
    ):
        raise ValueError("nominal exposure choices must be positive")
    if not 0.0 < float(config["geometry"]["minimum_altitude_deg"]) < 90.0:
        raise ValueError("minimum altitude must lie in (0, 90)")
    lunar = config["lunar_model"]
    if set(lunar) != {
        "angular_decay_scale_deg",
        "altitude_exponent",
        "maximum_penalty",
    }:
        raise ValueError("invalid lunar_model keys")
    if float(lunar["angular_decay_scale_deg"]) <= 0.0:
        raise ValueError("lunar angular decay scale must be positive")
    if float(lunar["altitude_exponent"]) <= 0.0:
        raise ValueError("lunar altitude exponent must be positive")
    if not 0.0 <= float(lunar["maximum_penalty"]) < 1.0:
        raise ValueError("lunar maximum penalty must lie in [0, 1)")
    if set(config["target_models"]) != set(TARGET_CLASSES):
        raise ValueError("target_models must define LRG, ELG, QSO, and BGS")


def load_tiles(path: Path) -> list[Tile]:
    result: list[Tile] = []
    seen: set[str] = set()
    for line, row in enumerate(read_exact_csv(path, TILE_COLUMNS), start=2):
        try:
            tile = Tile(
                tile_id=row["tile_id"].strip(),
                ra_deg=float(row["ra_deg"]),
                dec_deg=float(row["dec_deg"]),
                nominal_exptime_seconds=int(row["nominal_exptime_seconds"]),
                region_id=row["region_id"].strip(),
                scheduling_class=row["scheduling_class"].strip(),
                available_from_utc=parse_utc(row["available_from_utc"]),
                available_until_utc=parse_utc(row["available_until_utc"]),
                n_lrg=int(row["n_lrg"]),
                n_elg=int(row["n_elg"]),
                n_qso=int(row["n_qso"]),
                n_bgs=int(row["n_bgs"]),
            )
        except ValueError as exc:
            raise ValueError(f"{path}: row {line}: {exc}") from exc
        if not tile.tile_id or tile.tile_id in seen:
            raise ValueError(f"{path}: row {line}: tile_id must be non-empty and unique")
        if tile.scheduling_class not in {"REQUIRED", "FLEXIBLE"}:
            raise ValueError(f"{path}: row {line}: invalid scheduling_class")
        if not 0.0 <= tile.ra_deg < 360.0 or not -90.0 <= tile.dec_deg <= 90.0:
            raise ValueError(f"{path}: row {line}: invalid coordinates")
        if tile.available_until_utc <= tile.available_from_utc:
            raise ValueError(f"{path}: row {line}: empty availability interval")
        seen.add(tile.tile_id)
        result.append(tile)
    return result


def _julian_date(moment: datetime) -> float:
    return moment.timestamp() / 86400.0 + 2440587.5


def _local_sidereal_deg(moment: datetime, longitude_deg: float) -> float:
    days = _julian_date(moment) - 2451545.0
    return (280.46061837 + 360.98564736629 * days + longitude_deg) % 360.0


def _sun_equatorial_deg(moment: datetime) -> tuple[float, float]:
    days = _julian_date(moment) - 2451545.0
    mean_longitude = (280.460 + 0.9856474 * days) % 360.0
    mean_anomaly = math.radians((357.528 + 0.9856003 * days) % 360.0)
    longitude = math.radians(
        (mean_longitude + 1.915 * math.sin(mean_anomaly) + 0.020 * math.sin(2 * mean_anomaly))
        % 360.0
    )
    obliquity = math.radians(23.439 - 0.0000004 * days)
    return (
        math.degrees(math.atan2(math.cos(obliquity) * math.sin(longitude), math.cos(longitude)))
        % 360.0,
        math.degrees(math.asin(math.sin(obliquity) * math.sin(longitude))),
    )


def _moon_equatorial_deg(moment: datetime) -> tuple[float, float]:
    days = _julian_date(moment) - 2451545.0
    mean_longitude = math.radians((218.316 + 13.176396 * days) % 360.0)
    mean_anomaly = math.radians((134.963 + 13.064993 * days) % 360.0)
    argument_latitude = math.radians((93.272 + 13.229350 * days) % 360.0)
    longitude = mean_longitude + math.radians(6.289) * math.sin(mean_anomaly)
    latitude = math.radians(5.128) * math.sin(argument_latitude)
    obliquity = math.radians(23.439 - 0.0000004 * days)
    x = math.cos(longitude) * math.cos(latitude)
    y = (
        math.sin(longitude) * math.cos(latitude) * math.cos(obliquity)
        - math.sin(latitude) * math.sin(obliquity)
    )
    z = (
        math.sin(longitude) * math.cos(latitude) * math.sin(obliquity)
        + math.sin(latitude) * math.cos(obliquity)
    )
    return math.degrees(math.atan2(y, x)) % 360.0, math.degrees(math.asin(z))


def _angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1r, dec1r, ra2r, dec2r = map(math.radians, (ra1, dec1, ra2, dec2))
    cosine = (
        math.sin(dec1r) * math.sin(dec2r)
        + math.cos(dec1r) * math.cos(dec2r) * math.cos(ra1r - ra2r)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _normalized_airmass(altitude_deg: float) -> float:
    if altitude_deg <= 0.0:
        return float("inf")
    zenith_deg = 90.0 - altitude_deg
    raw = 1.0 / (
        math.cos(math.radians(zenith_deg))
        + 0.50572 * (96.07995 - zenith_deg) ** -1.6364
    )
    zenith_raw = 1.0 / (1.0 + 0.50572 * 96.07995**-1.6364)
    return raw / zenith_raw


def geometry_sample(
    tile: Tile,
    moment: datetime,
    tile_config: Mapping,
    calendar_config: Mapping,
) -> dict[str, float]:
    site = calendar_config["site"]
    latitude = math.radians(float(site["latitude_deg"]))
    longitude = float(site["longitude_deg"])
    declination = math.radians(tile.dec_deg)
    hour_angle_deg = (_local_sidereal_deg(moment, longitude) - tile.ra_deg + 180.0) % 360.0 - 180.0
    hour_angle = math.radians(hour_angle_deg)
    sin_altitude = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    altitude = math.asin(max(-1.0, min(1.0, sin_altitude)))
    cos_altitude = max(1e-12, math.cos(altitude))
    sin_azimuth = -math.sin(hour_angle) * math.cos(declination) / cos_altitude
    cos_azimuth = (
        math.sin(declination) - math.sin(altitude) * math.sin(latitude)
    ) / (cos_altitude * max(1e-12, math.cos(latitude)))
    azimuth = math.degrees(math.atan2(sin_azimuth, cos_azimuth)) % 360.0
    altitude_deg = math.degrees(altitude)

    sun_ra, sun_dec = _sun_equatorial_deg(moment)
    moon_ra, moon_dec = _moon_equatorial_deg(moment)
    sun_moon_separation = _angular_separation_deg(sun_ra, sun_dec, moon_ra, moon_dec)
    illumination = (1.0 - math.cos(math.radians(sun_moon_separation))) / 2.0
    tile_moon_separation = _angular_separation_deg(tile.ra_deg, tile.dec_deg, moon_ra, moon_dec)
    moon_tile = replace(tile, ra_deg=moon_ra, dec_deg=moon_dec)
    moon_altitude = geometry_sample_without_lunar(moon_tile, moment, calendar_config)["altitude_deg"]
    lunar = tile_config["lunar_model"]
    altitude_weight = math.sin(math.radians(max(0.0, moon_altitude))) ** float(
        lunar["altitude_exponent"]
    )
    angular_weight = math.exp(
        -tile_moon_separation / float(lunar["angular_decay_scale_deg"])
    )
    lunar_quality = (
        1.0
        - float(lunar["maximum_penalty"])
        * illumination
        * altitude_weight
        * angular_weight
    )
    return {
        "altitude_deg": altitude_deg,
        "azimuth_deg": azimuth,
        "hour_angle_deg": hour_angle_deg,
        "airmass": _normalized_airmass(altitude_deg),
        "moon_separation_deg": tile_moon_separation,
        "lunar_quality_factor": max(0.0, min(1.0, lunar_quality)),
    }


def geometry_sample_without_lunar(
    tile: Tile, moment: datetime, calendar_config: Mapping
) -> dict[str, float]:
    site = calendar_config["site"]
    latitude = math.radians(float(site["latitude_deg"]))
    declination = math.radians(tile.dec_deg)
    hour_angle_deg = (
        _local_sidereal_deg(moment, float(site["longitude_deg"])) - tile.ra_deg + 180.0
    ) % 360.0 - 180.0
    hour_angle = math.radians(hour_angle_deg)
    sin_altitude = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    altitude = math.asin(max(-1.0, min(1.0, sin_altitude)))
    cos_altitude = max(1e-12, math.cos(altitude))
    sin_azimuth = -math.sin(hour_angle) * math.cos(declination) / cos_altitude
    cos_azimuth = (
        math.sin(declination) - math.sin(altitude) * math.sin(latitude)
    ) / (cos_altitude * max(1e-12, math.cos(latitude)))
    return {
        "altitude_deg": math.degrees(altitude),
        "azimuth_deg": math.degrees(math.atan2(sin_azimuth, cos_azimuth)) % 360.0,
    }


# A REQUIRED tile offered on a single night is a coin flip on the weather, not something to plan around.
MIN_REQUIRED_NIGHTS = 2


def _assign_required(
    tiles: Sequence[Tile],
    config: Mapping,
    calendar_config: Mapping,
    nights: Sequence[Night],
) -> list[Tile]:
    """Mark `required_per_region` tiles per region REQUIRED, choosing only tiles the site can actually reach.

    Right ascension decides when a tile transits, so over a short survey roughly a sixth of the sky only ever
    crosses the meridian in daylight. Designating those tiles REQUIRED by array index charged every agent the
    miss penalty for an exposure it was never offered — the scorer has no notion of an excused REQUIRED tile the
    way it does for requests. Over a long survey the Sun works through every right ascension, so nothing is
    excluded there and existing long scenarios keep the catalogue they had.
    """
    required_per_region = int(config["catalog"]["required_per_region"])
    by_region: dict[str, list[int]] = {}
    for index, tile in enumerate(tiles):
        by_region.setdefault(tile.region_id, []).append(index)

    chances = {
        index: completable_nights(tile, config, calendar_config, nights)
        for index, tile in enumerate(tiles)
    }
    required: set[int] = set()
    for indices in by_region.values():
        # A region whose right ascension only transits in daylight for this survey contributes no REQUIRED tiles
        # at all: `required_per_region` is an upper bound, not a quota to fill with unreachable tiles.
        reachable = [i for i in indices if chances[i] >= MIN_REQUIRED_NIGHTS]
        required.update(reachable[:required_per_region])
    return [
        replace(tile, scheduling_class="REQUIRED") if index in required else tile
        for index, tile in enumerate(tiles)
    ]


def completable_nights(
    tile: Tile,
    config: Mapping,
    calendar_config: Mapping,
    nights: Sequence[Night],
) -> int:
    """How many nights offer an unbroken stretch long enough to finish one exposure of this tile.

    Mirrors how the platform builds tile windows (`SkyGeometry.get_tile_windows`): walk the night's slots, sample
    the altitude at each slot midpoint, group the consecutive eligible ones. Two details matter beyond simply
    clearing the altitude limit — an exposure that cannot finish before the tile sets is an invalid action, so
    the stretch has to be at least as long as the exposure; and a tile offered on exactly one night is a coin
    flip on the weather rather than something an agent can plan for.
    """
    slot_seconds = float(calendar_config["survey"]["slot_seconds"])
    minimum_altitude = float(config["geometry"]["minimum_altitude_deg"])
    exposure = float(tile.nominal_exptime_seconds)
    count = 0
    for night in nights:
        start = night.observing_start_utc
        span = (night.observing_end_utc - start).total_seconds()
        run = 0.0
        offset = 0.0
        fits = False
        while offset < span:
            midpoint = start + timedelta(seconds=offset + slot_seconds / 2)
            if geometry_sample_without_lunar(tile, midpoint, calendar_config)["altitude_deg"] >= minimum_altitude:
                run += slot_seconds
                if run >= exposure:
                    fits = True
                    break
            else:
                run = 0.0
            offset += slot_seconds
        if fits:
            count += 1
    return count


def build_catalog(
    config: Mapping,
    calendar_config: Mapping,
    nights: Sequence[Night],
) -> tuple[list[Tile], list[dict[str, object]]]:
    rng = random.Random(int(config["seed"]))
    catalog = config["catalog"]
    n_regions = int(catalog["n_regions"])
    tiles_per_region = int(catalog["tiles_per_region"])
    region_width = 360.0 / n_regions
    sin_dec_min = math.sin(math.radians(float(catalog["dec_min_deg"])))
    sin_dec_max = math.sin(math.radians(float(catalog["dec_max_deg"])))
    survey_start = nights[0].observing_start_utc
    survey_end = nights[-1].observing_end_utc
    tiles: list[Tile] = []
    for region_index in range(n_regions):
        for local_index in range(tiles_per_region):
            number = region_index * tiles_per_region + local_index + 1
            tiles.append(
                Tile(
                    tile_id=f"T{number:05d}",
                    ra_deg=(region_index * region_width + rng.uniform(0.04, 0.96) * region_width) % 360.0,
                    dec_deg=math.degrees(math.asin(rng.uniform(sin_dec_min, sin_dec_max))),
                    nominal_exptime_seconds=int(rng.choice(catalog["nominal_exptime_choices_seconds"])),
                    region_id=f"R{region_index:02d}",
                    scheduling_class="FLEXIBLE",
                    available_from_utc=survey_start,
                    available_until_utc=survey_end,
                )
            )
    tiles = _assign_required(tiles, config, calendar_config, nights)
    limited = int(catalog["time_limited_required_per_region"])
    window_days = int(catalog["time_limited_window_days"])
    adjusted: list[Tile] = []
    for index, tile in enumerate(tiles):
        local_index = index % tiles_per_region
        if tile.scheduling_class == "REQUIRED" and local_index < limited:
            best_index = max(
                range(len(nights)),
                key=lambda item: geometry_sample_without_lunar(
                    tile,
                    nights[item].observing_start_utc
                    + (nights[item].observing_end_utc - nights[item].observing_start_utc) / 2,
                    calendar_config,
                )["altitude_deg"],
            )
            first_index = max(0, min(best_index - window_days // 2, len(nights) - window_days))
            last_index = first_index + window_days - 1
            tile = replace(
                tile,
                available_from_utc=nights[first_index].observing_start_utc,
                available_until_utc=nights[last_index].observing_end_utc,
            )
        adjusted.append(tile)
    tiles = adjusted

    target_rows: list[dict[str, object]] = []
    counts: Counter[tuple[str, str]] = Counter()
    target_number = 0
    for tile in tiles:
        for target_class in TARGET_CLASSES:
            model = config["target_models"][target_class]
            mean = float(model["mean_count_per_tile"])
            count = max(1, int(round(rng.gauss(mean, mean * float(model["count_fractional_sigma"])))))
            for _ in range(count):
                target_number += 1
                target_rows.append(
                    {
                        "target_id": f"TG{target_number:08d}",
                        "tile_id": tile.tile_id,
                        "target_class": target_class,
                        "feature_flux": f"{rng.lognormvariate(math.log(float(model['flux_median'])), float(model['flux_log_sigma'])):.8f}",
                        "redshift": f"{max(0.0, rng.gauss(float(model['redshift_mean']), float(model['redshift_sigma']))):.6f}",
                        "science_weight": f"{float(model['science_weight']):.4f}",
                    }
                )
                counts[(tile.tile_id, target_class)] += 1
    tiles = [
        replace(
            tile,
            n_lrg=counts[(tile.tile_id, "LRG")],
            n_elg=counts[(tile.tile_id, "ELG")],
            n_qso=counts[(tile.tile_id, "QSO")],
            n_bgs=counts[(tile.tile_id, "BGS")],
        )
        for tile in tiles
    ]
    return tiles, target_rows


def generate_catalog(
    tile_config_path: Path,
    calendar_config_path: Path,
    nights_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    tile_config = load_config(tile_config_path)
    with calendar_config_path.open("r", encoding="utf-8") as handle:
        calendar_config = json.load(handle)
    nights = load_nights(nights_path)
    tiles, targets = build_catalog(tile_config, calendar_config, nights)
    output_dir.mkdir(parents=True, exist_ok=True)
    tiles_path = output_dir / "tiles.csv"
    targets_path = output_dir / "targets.csv"
    write_exact_csv(tiles_path, TILE_COLUMNS, (tile.csv_row() for tile in tiles))
    write_exact_csv(targets_path, TARGET_COLUMNS, targets)
    metadata = {
        "schema_version": "tile-catalog-metadata-v3",
        "generator": "tile_geometry_simulator.py",
        "seed": int(tile_config["seed"]),
        "row_counts": {"tiles": len(tiles), "targets": len(targets)},
        "sha256": {
            "tile_config": sha256_file(tile_config_path),
            "calendar_config": sha256_file(calendar_config_path),
            "night_calendar": sha256_file(nights_path),
            "tiles": sha256_file(tiles_path),
            "targets": sha256_file(targets_path),
        },
        "airmass_semantics": "instantaneous altitude-dependent value normalized to zenith=1",
        "lunar_quality_semantics": (
            "continuous factor in (0, 1]; no lunar exclusion threshold; "
            "one when Moon altitude is non-positive"
        ),
    }
    write_text_lf(output_dir / "catalog_metadata.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


class TileGeometrySimulator:
    def __init__(
        self,
        tiles: Sequence[Tile],
        tile_config: Mapping,
        calendar_config: Mapping,
        nights: Sequence[Night],
        slots: Sequence[Slot],
    ) -> None:
        self.tiles = {tile.tile_id: tile for tile in tiles}
        self.tile_config = tile_config
        self.calendar_config = calendar_config
        self.nights = {night.night_id: night for night in nights}
        self.slots_by_night: defaultdict[str, list[Slot]] = defaultdict(list)
        for slot in slots:
            self.slots_by_night[slot.night_id].append(slot)

    @classmethod
    def from_files(
        cls,
        tiles_path: Path,
        tile_config_path: Path,
        calendar_config_path: Path,
        nights_path: Path,
        slots_path: Path,
    ) -> "TileGeometrySimulator":
        with calendar_config_path.open("r", encoding="utf-8") as handle:
            calendar_config = json.load(handle)
        return cls(
            load_tiles(tiles_path),
            load_config(tile_config_path),
            calendar_config,
            load_nights(nights_path),
            load_slots(slots_path),
        )

    def get_tile_geometry(self, tile_id: str, timestamp_utc: datetime) -> dict[str, object]:
        try:
            tile = self.tiles[tile_id]
        except KeyError as exc:
            raise ValueError(f"unknown tile_id {tile_id!r}") from exc
        return {
            "tile_id": tile_id,
            "timestamp_utc": format_utc(timestamp_utc),
            **geometry_sample(tile, timestamp_utc, self.tile_config, self.calendar_config),
        }

    def get_tile_windows(self, first_night: date, days: int = 1) -> list[dict[str, object]]:
        if days < 1:
            raise ValueError("days must be positive")
        night_ids = [f"N{(first_night + timedelta(days=offset)).strftime('%Y%m%d')}" for offset in range(days)]
        missing = [night_id for night_id in night_ids if night_id not in self.nights]
        if missing:
            raise ValueError(f"requested nights outside calendar: {missing}")
        rows: list[dict[str, object]] = []
        for night_id in night_ids:
            night = self.nights[night_id]
            slots = self.slots_by_night[night_id]
            for tile in sorted(self.tiles.values(), key=lambda item: item.tile_id):
                groups: list[list[tuple[Slot, dict[str, float]]]] = []
                current: list[tuple[Slot, dict[str, float]]] = []
                for slot in slots:
                    midpoint = slot.timestamp_utc + timedelta(seconds=slot.duration_seconds / 2)
                    geometry = geometry_sample(tile, midpoint, self.tile_config, self.calendar_config)
                    eligible = (
                        slot.timestamp_utc >= tile.available_from_utc
                        and slot.end_utc <= tile.available_until_utc
                        and geometry["altitude_deg"] >= float(self.tile_config["geometry"]["minimum_altitude_deg"])
                    )
                    if eligible:
                        current.append((slot, geometry))
                    elif current:
                        groups.append(current)
                        current = []
                if current:
                    groups.append(current)
                valid = [
                    group
                    for group in groups
                    if sum(item[0].duration_seconds for item in group) >= tile.nominal_exptime_seconds
                ]
                for sequence, group in enumerate(valid, start=1):
                    start = group[0][0].timestamp_utc
                    end = group[-1][0].end_utc
                    best_slot, best_geometry = min(group, key=lambda item: item[1]["airmass"])
                    airmasses = [item[1]["airmass"] for item in group]
                    lunar_factors = [item[1]["lunar_quality_factor"] for item in group]
                    rows.append(
                        {
                            "window_id": f"{night.night_date.isoformat()}_{tile.tile_id}_W{sequence:02d}",
                            "night_id": night_id,
                            "night_date": night.night_date.isoformat(),
                            "tile_id": tile.tile_id,
                            "window_start_utc": format_utc(start),
                            "window_end_utc": format_utc(end),
                            "window_seconds": int((end - start).total_seconds()),
                            "best_time_utc": format_utc(
                                best_slot.timestamp_utc
                                + timedelta(seconds=best_slot.duration_seconds / 2)
                            ),
                            "best_airmass": round(best_geometry["airmass"], 6),
                            "mean_airmass": round(sum(airmasses) / len(airmasses), 6),
                            "mean_lunar_quality_factor": round(
                                sum(lunar_factors) / len(lunar_factors), 6
                            ),
                            "minimum_lunar_quality_factor": round(min(lunar_factors), 6),
                            "region_id": tile.region_id,
                            "scheduling_class": tile.scheduling_class,
                            "nominal_exptime_seconds": tile.nominal_exptime_seconds,
                            "available_until_utc": format_utc(tile.available_until_utc),
                        }
                    )
        return rows


def _default_simulator(args: argparse.Namespace) -> TileGeometrySimulator:
    return TileGeometrySimulator.from_files(
        args.tiles,
        args.tile_config,
        args.calendar_config,
        args.nights,
        args.slots,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile-config", type=Path, default=CONFIG_DIR / "tile_config.json")
    parser.add_argument("--calendar-config", type=Path, default=CONFIG_DIR / "calendar_config.json")
    parser.add_argument("--nights", type=Path, default=REFERENCE_OUTPUT_DIR / "night_calendar.csv")
    parser.add_argument("--slots", type=Path, default=REFERENCE_OUTPUT_DIR / "slots.csv")
    parser.add_argument("--tiles", type=Path, default=REFERENCE_OUTPUT_DIR / "tiles.csv")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--output-dir", type=Path, default=REFERENCE_OUTPUT_DIR)
    windows_parser = subparsers.add_parser("windows")
    windows_parser.add_argument("--date", type=date.fromisoformat, required=True)
    windows_parser.add_argument("--days", type=int, default=1)
    windows_parser.add_argument("--output", type=Path)
    geometry_parser = subparsers.add_parser("geometry")
    geometry_parser.add_argument("--tile-id", required=True)
    geometry_parser.add_argument("--timestamp-utc", type=parse_utc, required=True)
    args = parser.parse_args()
    if args.command == "generate":
        payload = generate_catalog(
            args.tile_config,
            args.calendar_config,
            args.nights,
            args.output_dir,
        )
    else:
        simulator = _default_simulator(args)
        if args.command == "geometry":
            payload = simulator.get_tile_geometry(args.tile_id, args.timestamp_utc)
        else:
            payload = simulator.get_tile_windows(args.date, args.days)
            if args.output:
                write_exact_csv(args.output, TILE_WINDOW_COLUMNS, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
