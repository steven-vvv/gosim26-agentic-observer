"""Versioned file contracts and shared serialization helpers for example3."""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


UTC = timezone.utc

PARTICIPANT_PROTOCOL_VERSION = "participant-agent-protocol-v2"
INITIAL_PUBLICATION_VERSION = "initial-publication-v2"
DECISION_SNAPSHOT_VERSION = "decision-snapshot-v3"
WORKFLOW_RESULT_VERSION = "workflow-result-v2"

# Scenarios without the anomaly-mechanics config sections keep speaking the pre-anomaly
# contract, so agents built and submitted before the mechanics release keep working.
LEGACY_PARTICIPANT_PROTOCOL_VERSION = "participant-agent-protocol-v1"
LEGACY_DECISION_SNAPSHOT_VERSION = "decision-snapshot-v2"
ACCEPTED_PROTOCOL_VERSIONS = (LEGACY_PARTICIPANT_PROTOCOL_VERSION, PARTICIPANT_PROTOCOL_VERSION)

ANOMALY_CONFIG_SECTIONS = ("repeat_observation", "reporting", "anomaly_tags", "fault_response")


def anomaly_mechanics_enabled(score_config) -> bool:
    """The single switch: a scenario opts into the anomaly mechanics through its score config."""
    return any(section in score_config for section in ANOMALY_CONFIG_SECTIONS)

NIGHT_COLUMNS = [
    "night_id",
    "night_date",
    "solar_dusk_utc",
    "solar_dawn_utc",
    "observing_start_utc",
    "observing_end_utc",
    "night_seconds",
    "slot_count",
]

SLOT_COLUMNS = [
    "slot_id",
    "night_id",
    "timestamp_utc",
    "duration_seconds",
]

TILE_COLUMNS = [
    "tile_id",
    "ra_deg",
    "dec_deg",
    "nominal_exptime_seconds",
    "region_id",
    "scheduling_class",
    "available_from_utc",
    "available_until_utc",
    "n_lrg",
    "n_elg",
    "n_qso",
    "n_bgs",
]

TARGET_COLUMNS = [
    "target_id",
    "tile_id",
    "target_class",
    "feature_flux",
    "redshift",
    "science_weight",
]

TILE_WINDOW_COLUMNS = [
    "window_id",
    "night_id",
    "night_date",
    "tile_id",
    "window_start_utc",
    "window_end_utc",
    "window_seconds",
    "best_time_utc",
    "best_airmass",
    "mean_airmass",
    "mean_lunar_quality_factor",
    "minimum_lunar_quality_factor",
    "region_id",
    "scheduling_class",
    "nominal_exptime_seconds",
    "available_until_utc",
]

WEATHER_COLUMNS = [
    "slot_id",
    "night_id",
    "timestamp_utc",
    "duration_seconds",
    "is_observable",
    "seeing_arcsec",
    "transparency",
    "sky_quality",
    "instrument_efficiency",
]

FORECAST_COLUMNS = [
    "forecast_id",
    "event_id",
    "revision",
    "issued_at_utc",
    "condition",
    "predicted_start_utc",
    "predicted_end_utc",
    "spatial_scope_type",
    "spatial_scope_payload",
    "severity",
    "probability",
    "start_uncertainty_seconds",
    "end_uncertainty_seconds",
]

EVENT_COLUMNS = [
    "event_id",
    "condition",
    "actual_start_utc",
    "actual_end_utc",
    "spatial_scope_type",
    "spatial_scope_payload",
    "severity",
    "force_close",
    "seeing_multiplier",
    "transparency_multiplier",
    "sky_quality_multiplier",
    "instrument_efficiency_multiplier",
]

REQUEST_COLUMNS = [
    "request_id",
    "issued_at_utc",
    "available_from_utc",
    "deadline_utc",
    "deadline_class",
    "completion_mode",
    "required_tile_count",
    "completion_reward",
    "miss_penalty",
    "reason",
]

REQUEST_TILE_COLUMNS = ["request_id", "tile_id", "required_visits"]

DECISION_COLUMNS = [
    "decision_id",
    "slot_id",
    "action",
    "tile_id",
    "program",
    "request_id",
    "reason",
]

REPORT_KINDS = ("Instrument_Failure", "NOVA", "Reddening")

REPORT_ACTIONS = {
    "report_instrument_failure": "Instrument_Failure",
    "report_nova": "NOVA",
    "report_reddening": "Reddening",
}

ANOMALY_TAG_VALUES = ("nova", "reddening")

TILE_ANOMALY_COLUMNS = ["tile_id", "anomaly_tag"]


def parse_utc(value: str) -> datetime:
    """Parse the contract UTC timestamp form and require an aware value."""
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"invalid UTC timestamp {value!r}") from exc
    if moment.tzinfo is None:
        raise ValueError(f"timestamp lacks a timezone: {value!r}")
    return moment.astimezone(UTC)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("cannot format a naive datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"invalid boolean {value!r}; expected true or false")


def read_exact_csv(path: Path, columns: Sequence[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(columns):
            raise ValueError(
                f"{path} columns are {reader.fieldnames}; expected {list(columns)}"
            )
        return list(reader)


def write_exact_csv(
    path: Path,
    columns: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(columns), lineterminator="\n", extrasaction="raise"
        )
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text_lf(path: "Path", text: str) -> None:
    """Write UTF-8 text with LF line endings on every platform, so generated files hash identically on Windows."""
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
