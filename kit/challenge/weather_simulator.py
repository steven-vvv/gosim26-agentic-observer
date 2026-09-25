#!/usr/bin/env python3
"""Generate, replay, and query calendar-aligned directional weather."""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import (
    write_text_lf,
    EVENT_COLUMNS,
    FORECAST_COLUMNS,
    TILE_COLUMNS,
    WEATHER_COLUMNS,
    format_utc,
    parse_bool,
    parse_utc,
    read_exact_csv,
    sha256_file,
    write_exact_csv,
)
from .observing_calendar import Night, Slot, load_nights, load_slots
from .project_paths import CONFIG_DIR, REFERENCE_OUTPUT_DIR
from .tile_geometry_simulator import TileGeometrySimulator


SCHEMA_VERSION = "directional-weather-v2"
LEGACY_SCHEMA_VERSION = "directional-weather-v1"
CONDITIONS = ("rainy", "cloudy", "smoggy", "rocket_launch", "cold_wave", "tornado", "instrument_fault")
UNFORECASTABLE_CONDITIONS = ("instrument_fault",)
FORECASTABLE_CONDITIONS = tuple(c for c in CONDITIONS if c not in UNFORECASTABLE_CONDITIONS)
SCOPE_TYPES = {"ALL", "REGION_SET", "SKY_CAP_ICRS", "HORIZON_SECTOR", "TILE_SET"}


def _json_payload(value: Mapping) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_payload(scope_type: str, raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid spatial_scope_payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("spatial_scope_payload must be a JSON object")
    expected = {
        "ALL": set(),
        "REGION_SET": {"region_ids"},
        "TILE_SET": {"tile_ids"},
        "SKY_CAP_ICRS": {"ra_deg", "dec_deg", "radius_deg"},
        "HORIZON_SECTOR": {"azimuth_start_deg", "azimuth_end_deg", "min_altitude_deg", "max_altitude_deg"},
    }
    if scope_type not in expected or set(payload) != expected[scope_type]:
        raise ValueError(f"invalid payload keys for {scope_type}")
    if scope_type in {"REGION_SET", "TILE_SET"}:
        key = "region_ids" if scope_type == "REGION_SET" else "tile_ids"
        if not isinstance(payload[key], list) or not payload[key] or len(set(payload[key])) != len(payload[key]):
            raise ValueError(f"{key} must be a non-empty unique list")
    elif scope_type == "SKY_CAP_ICRS":
        if not (0 <= float(payload["ra_deg"]) < 360 and -90 <= float(payload["dec_deg"]) <= 90 and 0 < float(payload["radius_deg"]) <= 180):
            raise ValueError("invalid SKY_CAP_ICRS payload")
    elif scope_type == "HORIZON_SECTOR":
        if not all(0 <= float(payload[key]) <= 360 for key in ("azimuth_start_deg", "azimuth_end_deg")):
            raise ValueError("invalid horizon azimuth range")
        if not (-90 <= float(payload["min_altitude_deg"]) < float(payload["max_altitude_deg"]) <= 90):
            raise ValueError("invalid horizon altitude range")
    return payload


@dataclass(frozen=True)
class WeatherSlot:
    slot_id: str
    night_id: str
    timestamp_utc: datetime
    duration_seconds: int
    is_observable: bool
    seeing_arcsec: float | None
    transparency: float | None
    sky_quality: float | None
    instrument_efficiency: float | None

    @property
    def end_utc(self) -> datetime:
        return self.timestamp_utc + timedelta(seconds=self.duration_seconds)

    def public_dict(self) -> dict[str, object]:
        return {
            "slot_id": self.slot_id,
            "night_id": self.night_id,
            "timestamp_utc": format_utc(self.timestamp_utc),
            "duration_seconds": self.duration_seconds,
            "is_observable": self.is_observable,
            "seeing_arcsec": self.seeing_arcsec,
            "transparency": self.transparency,
            "sky_quality": self.sky_quality,
            "instrument_efficiency": self.instrument_efficiency,
        }

    def csv_row(self) -> dict[str, object]:
        row = self.public_dict()
        row["is_observable"] = str(self.is_observable).lower()
        for key in ("seeing_arcsec", "transparency", "sky_quality", "instrument_efficiency"):
            row[key] = "" if row[key] is None else f"{float(row[key]):.6f}"
        return row


@dataclass(frozen=True)
class WeatherEvent:
    event_id: str
    condition: str
    actual_start_utc: datetime
    actual_end_utc: datetime
    spatial_scope_type: str
    spatial_scope_payload: dict
    severity: float
    force_close: bool
    seeing_multiplier: float
    transparency_multiplier: float
    sky_quality_multiplier: float
    instrument_efficiency_multiplier: float

    def overlaps(self, slot: WeatherSlot) -> bool:
        return self.actual_start_utc < slot.end_utc and self.actual_end_utc > slot.timestamp_utc

    def csv_row(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "condition": self.condition,
            "actual_start_utc": format_utc(self.actual_start_utc),
            "actual_end_utc": format_utc(self.actual_end_utc),
            "spatial_scope_type": self.spatial_scope_type,
            "spatial_scope_payload": _json_payload(self.spatial_scope_payload),
            "severity": f"{self.severity:.6f}",
            "force_close": str(self.force_close).lower(),
            "seeing_multiplier": f"{self.seeing_multiplier:.6f}",
            "transparency_multiplier": f"{self.transparency_multiplier:.6f}",
            "sky_quality_multiplier": f"{self.sky_quality_multiplier:.6f}",
            "instrument_efficiency_multiplier": f"{self.instrument_efficiency_multiplier:.6f}",
        }


@dataclass(frozen=True)
class Forecast:
    forecast_id: str
    event_id: str
    revision: int
    issued_at_utc: datetime
    condition: str
    predicted_start_utc: datetime
    predicted_end_utc: datetime
    spatial_scope_type: str
    spatial_scope_payload: dict
    severity: float
    probability: float
    start_uncertainty_seconds: int
    end_uncertainty_seconds: int

    def public_dict(self) -> dict[str, object]:
        return {
            "forecast_id": self.forecast_id,
            "event_id": self.event_id,
            "revision": self.revision,
            "issued_at_utc": format_utc(self.issued_at_utc),
            "condition": self.condition,
            "predicted_start_utc": format_utc(self.predicted_start_utc),
            "predicted_end_utc": format_utc(self.predicted_end_utc),
            "spatial_scope_type": self.spatial_scope_type,
            "spatial_scope_payload": _json_payload(self.spatial_scope_payload),
            "severity": round(self.severity, 6),
            "probability": round(self.probability, 6),
            "start_uncertainty_seconds": self.start_uncertainty_seconds,
            "end_uncertainty_seconds": self.end_uncertainty_seconds,
        }


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("schema_version") not in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
        raise ValueError("unsupported weather schema_version")
    # v1 configs predate instrument_fault; every condition they do define must be known.
    if not set(config["events"]) <= set(CONDITIONS) or not set(config["events"]) >= set(FORECASTABLE_CONDITIONS):
        raise ValueError("weather config must define every condition exactly once")
    for definition in config["events"].values():
        scopes = definition["scope_weights"]
        if not scopes or not set(scopes) <= SCOPE_TYPES or any(float(weight) <= 0 for weight in scopes.values()):
            raise ValueError("invalid event scope weights")
        if definition.get("persists_until_survey_end"):
            if int(definition["count"]) > 1:
                raise ValueError("at most one persistent event can exist (it never ends on its own)")
        elif "duration_slots" not in definition:
            raise ValueError("event needs duration_slots unless persists_until_survey_end is set")
        severity_range = definition.get("severity_range")
        multiplier_range = definition.get("instrument_efficiency_multiplier_range")
        for name, value in (("severity_range", severity_range), ("instrument_efficiency_multiplier_range", multiplier_range)):
            if value is None:
                continue
            try:
                lo, hi = map(float, value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be [lo, hi]") from exc
            if not (math.isfinite(lo) and math.isfinite(hi)) or not 0 < lo <= hi:
                raise ValueError(f"{name} needs two finite numbers with 0 < lo <= hi")
        if multiplier_range is None and "instrument_efficiency_multiplier" not in definition:
            raise ValueError("event needs instrument_efficiency_multiplier or instrument_efficiency_multiplier_range")
    return config


def _optional_float(row: Mapping[str, str], key: str) -> float | None:
    return None if row[key].strip() == "" else float(row[key])


def load_weather(path: Path) -> list[WeatherSlot]:
    result = []
    for row in read_exact_csv(path, WEATHER_COLUMNS):
        item = WeatherSlot(
            row["slot_id"], row["night_id"], parse_utc(row["timestamp_utc"]),
            int(row["duration_seconds"]), parse_bool(row["is_observable"]),
            _optional_float(row, "seeing_arcsec"), _optional_float(row, "transparency"),
            _optional_float(row, "sky_quality"), _optional_float(row, "instrument_efficiency"),
        )
        values = (item.seeing_arcsec, item.transparency, item.sky_quality, item.instrument_efficiency)
        if item.is_observable != all(value is not None for value in values):
            raise ValueError(f"{item.slot_id}: quality fields must be all present iff observable")
        result.append(item)
    return result


def load_events(path: Path) -> list[WeatherEvent]:
    result = []
    for row in read_exact_csv(path, EVENT_COLUMNS):
        scope = row["spatial_scope_type"]
        result.append(WeatherEvent(
            row["event_id"], row["condition"], parse_utc(row["actual_start_utc"]),
            parse_utc(row["actual_end_utc"]), scope, _parse_payload(scope, row["spatial_scope_payload"]),
            float(row["severity"]), parse_bool(row["force_close"]), float(row["seeing_multiplier"]),
            float(row["transparency_multiplier"]), float(row["sky_quality_multiplier"]),
            float(row["instrument_efficiency_multiplier"]),
        ))
    return result


def load_forecasts(path: Path) -> list[Forecast]:
    result = []
    for row in read_exact_csv(path, FORECAST_COLUMNS):
        scope = row["spatial_scope_type"]
        result.append(Forecast(
            row["forecast_id"], row["event_id"], int(row["revision"]), parse_utc(row["issued_at_utc"]),
            row["condition"], parse_utc(row["predicted_start_utc"]), parse_utc(row["predicted_end_utc"]),
            scope, _parse_payload(scope, row["spatial_scope_payload"]), float(row["severity"]),
            float(row["probability"]), int(row["start_uncertainty_seconds"]), int(row["end_uncertainty_seconds"]),
        ))
    return result


def _clip(value: float, model: Mapping) -> float:
    return max(float(model["minimum"]), min(float(model["maximum"]), value))


def _scaled_multiplier(configured: float, severity: float) -> float:
    return 1.0 + severity * (configured - 1.0)


def _weighted_choice(rng: random.Random, weights: Mapping[str, float]) -> str:
    threshold = rng.random() * sum(float(value) for value in weights.values())
    total = 0.0
    for key, value in weights.items():
        total += float(value)
        if threshold <= total:
            return key
    return next(reversed(weights))


def _random_scope(scope_type: str, rng: random.Random, tile_ids: Sequence[str]) -> dict:
    if scope_type == "ALL":
        return {}
    if scope_type == "REGION_SET":
        return {"region_ids": sorted(rng.sample([f"R{i:02d}" for i in range(8)], rng.randint(1, 4)))}
    if scope_type == "TILE_SET":
        return {"tile_ids": sorted(rng.sample(list(tile_ids), min(len(tile_ids), rng.randint(1, 6))))}
    if scope_type == "SKY_CAP_ICRS":
        return {"ra_deg": round(rng.uniform(0, 360), 6), "dec_deg": round(rng.uniform(-10, 70), 6), "radius_deg": round(rng.uniform(12, 42), 6)}
    start = rng.uniform(0, 360)
    return {"azimuth_start_deg": round(start, 6), "azimuth_end_deg": round((start + rng.uniform(30, 100)) % 360, 6), "min_altitude_deg": 0.0, "max_altitude_deg": round(rng.uniform(35, 80), 6)}


def generate_events(config: Mapping, slots: Sequence[Slot], tile_ids: Sequence[str]) -> list[WeatherEvent]:
    rng = random.Random(int(config["seed"]) + 2000)
    events = []
    sequence = 0
    fault_intervals: list[tuple[datetime, datetime]] = []
    for condition in CONDITIONS:
        if condition not in config["events"]:
            continue
        definition = config["events"][condition]
        for occurrence in range(int(definition["count"])):
            sequence += 1
            nominal = int((occurrence + 1) * len(slots) / (int(definition["count"]) + 1))
            for _attempt in range(100):
                start_slot = slots[max(0, min(len(slots) - 1, nominal + rng.randint(-80, 80)))]
                if definition.get("persists_until_survey_end"):
                    # No natural lifetime: only an acknowledged report's repair clock ends it.
                    duration = int((slots[-1].end_utc - start_slot.timestamp_utc).total_seconds())
                else:
                    duration = rng.randint(*map(int, definition["duration_slots"])) * start_slot.duration_seconds
                start, end = start_slot.timestamp_utc, start_slot.timestamp_utc + timedelta(seconds=duration)
                if condition != "instrument_fault" or not any(start < e and end > s for s, e in fault_intervals):
                    break
            else:
                raise ValueError("could not place a non-overlapping instrument_fault event")
            if condition == "instrument_fault":
                fault_intervals.append((start, end))
            severity = rng.uniform(*map(float, definition.get("severity_range", [0.55, 1.0])))
            efficiency_range = definition.get("instrument_efficiency_multiplier_range")
            if efficiency_range is not None:
                # Direct final-multiplier draw; severity does not mediate it.
                efficiency_multiplier = rng.uniform(*map(float, efficiency_range))
            else:
                efficiency_multiplier = _scaled_multiplier(float(definition["instrument_efficiency_multiplier"]), severity)
            scope_type = _weighted_choice(rng, definition["scope_weights"])
            events.append(WeatherEvent(
                f"EV{sequence:04d}", condition, start, end, scope_type,
                _random_scope(scope_type, rng, tile_ids), severity, bool(definition["force_close"]),
                _scaled_multiplier(float(definition["seeing_multiplier"]), severity),
                _scaled_multiplier(float(definition["transparency_multiplier"]), severity),
                _scaled_multiplier(float(definition["sky_quality_multiplier"]), severity),
                efficiency_multiplier,
            ))
    return sorted(events, key=lambda item: (item.actual_start_utc, item.event_id))


def generate_weather(config: Mapping, slots: Sequence[Slot], events: Sequence[WeatherEvent]) -> list[WeatherSlot]:
    rng = random.Random(int(config["seed"]) + 1000)
    quality = config["quality"]
    fields = ("seeing_arcsec", "transparency", "sky_quality")
    night_state = {field: float(quality[field]["nominal"]) for field in fields}
    efficiency_model = quality["instrument_efficiency"]
    # Without jitter keys the baseline efficiency stays the constant nominal (pre-v2 semantics).
    jitter = None
    if "jitter_minimum" in efficiency_model or "jitter_maximum" in efficiency_model:
        jitter = (float(efficiency_model["jitter_minimum"]), float(efficiency_model["jitter_maximum"]))
    rows = []
    by_night: defaultdict[str, list[Slot]] = defaultdict(list)
    for slot in slots:
        by_night[slot.night_id].append(slot)
    for night_slots in by_night.values():
        day_number = night_slots[0].timestamp_utc.timetuple().tm_yday
        phase = 2 * math.pi * (day_number - int(quality["seasonal_phase_day"])) / 365.2425
        for field in fields:
            model = quality[field]
            direction = 1.0 if field == "seeing_arcsec" else -1.0
            mean = float(model["nominal"]) + direction * float(model["seasonal_amplitude"]) * math.sin(phase)
            phi = float(quality["night_correlation"])
            night_state[field] = _clip(mean + phi * (night_state[field] - mean) + rng.gauss(0, float(model["night_sigma"]) * math.sqrt(1 - phi**2)), model)
        slot_state = dict(night_state)
        background_open = True
        closure = config["background_closure"]
        start_probability = float(closure["start_probability_per_open_slot"]) + float(closure["seasonal_probability_amplitude"]) * max(0.0, math.sin(phase))
        for slot in night_slots:
            phi = float(quality["slot_correlation"])
            for field in fields:
                model = quality[field]
                slot_state[field] = _clip(night_state[field] + phi * (slot_state[field] - night_state[field]) + rng.gauss(0, float(model["slot_sigma"]) * math.sqrt(1 - phi**2)), model)
            if background_open and rng.random() < start_probability:
                background_open = False
            elif not background_open and rng.random() < float(closure["reopen_probability_per_closed_slot"]):
                background_open = True
            provisional = WeatherSlot(slot.slot_id, slot.night_id, slot.timestamp_utc, slot.duration_seconds, True, 0, 0, 0, 0)
            global_events = [event for event in events if event.spatial_scope_type == "ALL" and event.overlaps(provisional)]
            observable = background_open and not any(event.force_close for event in global_events)
            if not observable:
                rows.append(WeatherSlot(slot.slot_id, slot.night_id, slot.timestamp_utc, slot.duration_seconds, False, None, None, None, None))
                continue
            values = dict(slot_state)
            efficiency = rng.uniform(*jitter) if jitter is not None else float(efficiency_model["nominal"])
            for event in global_events:
                values["seeing_arcsec"] *= event.seeing_multiplier
                values["transparency"] *= event.transparency_multiplier
                values["sky_quality"] *= event.sky_quality_multiplier
                efficiency *= event.instrument_efficiency_multiplier
            rows.append(WeatherSlot(slot.slot_id, slot.night_id, slot.timestamp_utc, slot.duration_seconds, True,
                _clip(values["seeing_arcsec"], quality["seeing_arcsec"]), _clip(values["transparency"], quality["transparency"]),
                _clip(values["sky_quality"], quality["sky_quality"]), _clip(efficiency, quality["instrument_efficiency"])))
    return rows


def _perturb_scope(scope_type: str, payload: Mapping, closeness: float, rng: random.Random, tile_ids: Sequence[str]) -> dict:
    if scope_type == "ALL":
        return {}
    if scope_type in {"REGION_SET", "TILE_SET"}:
        key = "region_ids" if scope_type == "REGION_SET" else "tile_ids"
        universe = [f"R{i:02d}" for i in range(8)] if key == "region_ids" else list(tile_ids)
        result = set(payload[key])
        if rng.random() < 0.5 * (1 - closeness):
            candidate = rng.choice(universe)
            if candidate in result and len(result) > 1:
                result.remove(candidate)
            else:
                result.add(candidate)
        return {key: sorted(result)}
    result = dict(payload)
    scale = max(0.05, 1 - closeness)
    if scope_type == "SKY_CAP_ICRS":
        result["ra_deg"] = round((float(result["ra_deg"]) + rng.gauss(0, 12 * scale)) % 360, 6)
        result["dec_deg"] = round(max(-90, min(90, float(result["dec_deg"]) + rng.gauss(0, 8 * scale))), 6)
        result["radius_deg"] = round(max(1, min(180, float(result["radius_deg"]) + rng.gauss(0, 8 * scale))), 6)
    else:
        for key in ("azimuth_start_deg", "azimuth_end_deg"):
            result[key] = round((float(result[key]) + rng.gauss(0, 15 * scale)) % 360, 6)
    return result


def generate_forecasts(config: Mapping, events: Sequence[WeatherEvent], nights: Sequence[Night], tile_ids: Sequence[str]) -> list[Forecast]:
    rng = random.Random(int(config["seed"]) + 3000)
    miss_rng = random.Random(int(config["seed"]) + 3100)
    horizon_days = int(config["forecast"]["horizon_days"])
    rows = []
    revisions: Counter[str] = Counter()
    for event in events:
        if event.condition in UNFORECASTABLE_CONDITIONS:
            continue
        if miss_rng.random() < float(config["forecast"]["miss_probability"]):
            continue
        for night in nights:
            issued = night.observing_start_utc
            if not (event.actual_end_utc > issued and event.actual_start_utc < issued + timedelta(days=horizon_days)):
                continue
            lead = max(0.0, (event.actual_start_utc - issued).total_seconds())
            closeness = 1 - min(1.0, lead / (horizon_days * 86400))
            uncertainty = max(900, int(round(((1 - closeness) * 4 + 0.25) * 4)) * 900)
            start = event.actual_start_utc + timedelta(seconds=round(rng.gauss(0, uncertainty) / 900) * 900)
            end = event.actual_end_utc + timedelta(seconds=round(rng.gauss(0, uncertainty) / 900) * 900)
            if end <= start:
                end = start + timedelta(seconds=900)
            revisions[event.event_id] += 1
            rows.append(Forecast("", event.event_id, revisions[event.event_id], issued, event.condition, start, end,
                event.spatial_scope_type, _perturb_scope(event.spatial_scope_type, event.spatial_scope_payload, closeness, rng, tile_ids),
                max(0, min(1, event.severity + rng.gauss(0, .16 * (1 - closeness) + .03))),
                max(.05, min(.99, .52 + .43 * closeness + rng.gauss(0, .035))), uncertainty, uncertainty))
    for index in range(int(config["forecast"]["false_positive_count"])):
        night_index = rng.randint(horizon_days, len(nights) - 2)
        issued = nights[night_index - rng.randint(2, horizon_days)].observing_start_utc
        start = nights[night_index].observing_start_utc + timedelta(seconds=rng.randint(0, max(0, nights[night_index].slot_count - 1)) * 900)
        condition = rng.choice(FORECASTABLE_CONDITIONS)
        scope_type = _weighted_choice(rng, config["events"][condition]["scope_weights"])
        rows.append(Forecast("", f"FP{index + 1:04d}", 1, issued, condition, start, start + timedelta(hours=rng.randint(1, 8)),
            scope_type, _random_scope(scope_type, rng, tile_ids), rng.uniform(.3, .8), rng.uniform(.25, .7), 7200, 7200))
    ordered = sorted(rows, key=lambda item: (item.issued_at_utc, item.event_id, item.revision))
    return [replace(item, forecast_id=f"FC{index:06d}") for index, item in enumerate(ordered, 1)]


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    values = map(math.radians, (ra1, dec1, ra2, dec2))
    ra1r, dec1r, ra2r, dec2r = values
    cosine = math.sin(dec1r) * math.sin(dec2r) + math.cos(dec1r) * math.cos(dec2r) * math.cos(ra1r - ra2r)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _azimuth_inside(value: float, start: float, end: float) -> bool:
    return start <= value <= end if start <= end else value >= start or value <= end


class WeatherSimulator:
    """Time-safe current conditions and as-of forecast publication."""

    def __init__(self, weather: Sequence[WeatherSlot], forecasts: Sequence[Forecast], events: Sequence[WeatherEvent], config: Mapping, geometry: TileGeometrySimulator | None = None) -> None:
        self.weather = list(weather)
        self.forecasts = list(forecasts)
        self.events = list(events)
        self.config = config
        self.geometry = geometry
        # Run-local overlay: an acknowledged instrument fault stops applying at its repair time.
        self.end_overrides: dict[str, datetime] = {}
        self._weather = {item.slot_id: item for item in weather}
        if len(self._weather) != len(weather):
            raise ValueError("weather slot IDs are not unique")

    def _event_active(self, event: WeatherEvent, slot: WeatherSlot) -> bool:
        end = self.end_overrides.get(event.event_id, event.actual_end_utc)
        return event.actual_start_utc < slot.end_utc and end > slot.timestamp_utc

    def _applies(self, event: WeatherEvent, tile_id: str, slot: WeatherSlot) -> bool:
        if event.spatial_scope_type == "ALL":
            return True
        if self.geometry is None or tile_id not in self.geometry.tiles:
            raise ValueError("directional weather query requires a known tile and geometry simulator")
        tile = self.geometry.tiles[tile_id]
        payload = event.spatial_scope_payload
        if event.spatial_scope_type == "REGION_SET":
            return tile.region_id in payload["region_ids"]
        if event.spatial_scope_type == "TILE_SET":
            return tile_id in payload["tile_ids"]
        if event.spatial_scope_type == "SKY_CAP_ICRS":
            return _separation_deg(tile.ra_deg, tile.dec_deg, float(payload["ra_deg"]), float(payload["dec_deg"])) <= float(payload["radius_deg"])
        midpoint = slot.timestamp_utc + timedelta(seconds=slot.duration_seconds / 2)
        sample = self.geometry.get_tile_geometry(tile_id, midpoint)
        return (float(payload["min_altitude_deg"]) <= float(sample["altitude_deg"]) <= float(payload["max_altitude_deg"]) and
                _azimuth_inside(float(sample["azimuth_deg"]), float(payload["azimuth_start_deg"]), float(payload["azimuth_end_deg"])))

    def get_effective_conditions(self, slot_id: str, tile_id: str | None = None, *, include_instrument_faults: bool = True) -> dict[str, object]:
        """Truth view by default; pass include_instrument_faults=False for the agent-visible snapshot view."""
        if slot_id not in self._weather:
            raise ValueError(f"unknown slot_id {slot_id!r}")
        base = self._weather[slot_id]
        active = [event for event in self.events if self._event_active(event, base) and (include_instrument_faults or event.condition != "instrument_fault")
                  and (event.spatial_scope_type == "ALL" or (tile_id is not None and self._applies(event, tile_id, base)))]
        payload = base.public_dict()
        payload["tile_id"] = tile_id
        payload["active_event_ids"] = [event.event_id for event in active]
        if not base.is_observable:
            return payload
        directional = [event for event in active if event.spatial_scope_type != "ALL"]
        if any(event.force_close for event in directional):
            payload.update({"is_observable": False, "seeing_arcsec": None, "transparency": None, "sky_quality": None, "instrument_efficiency": None})
            return payload
        multipliers = {"seeing_arcsec": 1.0, "transparency": 1.0, "sky_quality": 1.0, "instrument_efficiency": 1.0}
        for event in directional:
            multipliers["seeing_arcsec"] *= event.seeing_multiplier
            multipliers["transparency"] *= event.transparency_multiplier
            multipliers["sky_quality"] *= event.sky_quality_multiplier
            multipliers["instrument_efficiency"] *= event.instrument_efficiency_multiplier
        for key in multipliers:
            payload[key] = _clip(float(payload[key]) * multipliers[key], self.config["quality"][key])
        return payload

    def get_weather_forecast(self, as_of_utc: datetime, days: int | None = None) -> list[dict[str, object]]:
        horizon = timedelta(days=days or int(self.config["forecast"]["horizon_days"]))
        latest: dict[str, Forecast] = {}
        for item in self.forecasts:
            if item.issued_at_utc <= as_of_utc and (item.event_id not in latest or item.revision > latest[item.event_id].revision):
                latest[item.event_id] = item
        return [item.public_dict() for item in sorted(latest.values(), key=lambda value: (value.predicted_start_utc, value.event_id)) if item.predicted_end_utc > as_of_utc and item.predicted_start_utc < as_of_utc + horizon]


def weather_quality(weather: Mapping[str, object], airmass: float, config: Mapping, *, include_efficiency: bool = True) -> float:
    if not bool(weather["is_observable"]):
        return 0.0
    if not math.isfinite(airmass) or airmass <= 0:
        raise ValueError("airmass must be positive and finite")
    # include_efficiency=False gives the band-determination quality: program bands
    # never depend on instrument efficiency, so preview and replay agree on them.
    efficiency = float(weather["instrument_efficiency"]) if include_efficiency else 1.0
    raw = efficiency * float(weather["transparency"]) * float(weather["sky_quality"]) / (float(weather["seeing_arcsec"]) * airmass ** float(config["score_interface"]["airmass_exponent"]))
    return min(raw, float(config["score_interface"]["maximum_weather_quality"]))


def generate(config_path: Path, nights_path: Path, slots_path: Path, tiles_path: Path, output_dir: Path) -> dict[str, object]:
    config = load_config(config_path)
    nights = load_nights(nights_path)
    slots = load_slots(slots_path)
    tile_ids = [row["tile_id"] for row in read_exact_csv(tiles_path, TILE_COLUMNS)]
    events = generate_events(config, slots, tile_ids)
    weather = generate_weather(config, slots, events)
    forecasts = generate_forecasts(config, events, nights, tile_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {"weather": output_dir / "weather.csv", "forecasts": output_dir / "weather_forecasts.csv", "events": output_dir / "weather_events.csv"}
    write_exact_csv(paths["weather"], WEATHER_COLUMNS, (item.csv_row() for item in weather))
    write_exact_csv(paths["forecasts"], FORECAST_COLUMNS, (item.public_dict() for item in forecasts))
    write_exact_csv(paths["events"], EVENT_COLUMNS, (item.csv_row() for item in events))
    metadata = {
        "schema_version": "weather-metadata-v2", "seed": int(config["seed"]),
        "row_counts": {"weather": len(weather), "forecasts": len(forecasts), "events": len(events)},
        "closed_slot_count": sum(not item.is_observable for item in weather),
        "scope_counts": dict(sorted(Counter(item.spatial_scope_type for item in events).items())),
        "sha256": {"config": sha256_file(config_path), "slots": sha256_file(slots_path), "tiles": sha256_file(tiles_path), **{key: sha256_file(path) for key, path in paths.items()}},
        "participant_visible": ["weather.csv", "weather_forecasts.csv"], "organizer_internal": ["weather_events.csv"],
    }
    write_text_lf(output_dir / "weather_metadata.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def _default_runtime(args: argparse.Namespace) -> WeatherSimulator:
    geometry = TileGeometrySimulator.from_files(args.tiles, args.tile_config, args.calendar_config, args.nights, args.slots)
    return WeatherSimulator(load_weather(args.weather), load_forecasts(args.forecasts), load_events(args.events), load_config(args.config), geometry)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "weather_config.json")
    parser.add_argument("--calendar-config", type=Path, default=CONFIG_DIR / "calendar_config.json")
    parser.add_argument("--tile-config", type=Path, default=CONFIG_DIR / "tile_config.json")
    parser.add_argument("--nights", type=Path, default=REFERENCE_OUTPUT_DIR / "night_calendar.csv")
    parser.add_argument("--slots", type=Path, default=REFERENCE_OUTPUT_DIR / "slots.csv")
    parser.add_argument("--tiles", type=Path, default=REFERENCE_OUTPUT_DIR / "tiles.csv")
    parser.add_argument("--weather", type=Path, default=REFERENCE_OUTPUT_DIR / "weather.csv")
    parser.add_argument("--forecasts", type=Path, default=REFERENCE_OUTPUT_DIR / "weather_forecasts.csv")
    parser.add_argument("--events", type=Path, default=REFERENCE_OUTPUT_DIR / "weather_events.csv")
    commands = parser.add_subparsers(dest="command", required=True)
    generate_parser = commands.add_parser("generate")
    generate_parser.add_argument("--output-dir", type=Path, default=REFERENCE_OUTPUT_DIR)
    current = commands.add_parser("current")
    current.add_argument("--slot-id", required=True)
    current.add_argument("--tile-id")
    forecast = commands.add_parser("forecast")
    forecast.add_argument("--as-of-utc", required=True, type=parse_utc)
    forecast.add_argument("--days", type=int)
    args = parser.parse_args()
    if args.command == "generate":
        payload = generate(args.config, args.nights, args.slots, args.tiles, args.output_dir)
    else:
        runtime = _default_runtime(args)
        payload = runtime.get_effective_conditions(args.slot_id, args.tile_id) if args.command == "current" else runtime.get_weather_forecast(args.as_of_utc, args.days)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
