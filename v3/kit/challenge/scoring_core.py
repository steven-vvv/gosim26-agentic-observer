"""Authoritative replay engine for example3 decisions."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import (
    ANOMALY_TAG_VALUES,
    anomaly_mechanics_enabled,
    DECISION_COLUMNS,
    REPORT_ACTIONS,
    TARGET_COLUMNS,
    TILE_ANOMALY_COLUMNS,
    format_utc,
    read_exact_csv,
    sha256_file,
    write_text_lf,
)
from .observation_request_simulator import ObservationRequest, load_request_tiles, load_requests
from .observing_calendar import Slot, load_slots
from .tile_geometry_simulator import Tile, TileGeometrySimulator, load_tiles
from .weather_simulator import WeatherSimulator, load_config as load_weather_config, load_events, load_forecasts, load_weather, weather_quality


PROGRAMS = {"DARK", "BRIGHT", "BACKUP"}


@dataclass(frozen=True)
class Decision:
    decision_id: str
    slot_id: str
    action: str
    tile_id: str
    program: str
    request_id: str
    reason: str

    def csv_row(self) -> dict[str, object]:
        return {key: getattr(self, key) for key in DECISION_COLUMNS}


def load_decisions(path: Path, *, allow_reports: bool = True) -> list[Decision]:
    rows = []
    seen = set()
    for row in read_exact_csv(path, DECISION_COLUMNS):
        item = Decision(*(row[key].strip() for key in DECISION_COLUMNS))
        if not item.decision_id or item.decision_id in seen:
            raise ValueError("decision_id must be non-empty and unique")
        if item.action not in {"observe", "wait", *REPORT_ACTIONS}:
            raise ValueError(f"{item.decision_id}: unknown action {item.action!r}")
        if item.action in REPORT_ACTIONS and not allow_reports:
            raise ValueError(f"{item.decision_id}: action must be observe or wait")
        if item.action == "wait" and (item.tile_id or item.program or item.request_id):
            raise ValueError(f"{item.decision_id}: wait must not name tile, program, or request")
        if item.action == "observe" and (not item.tile_id or item.program not in PROGRAMS):
            raise ValueError(f"{item.decision_id}: invalid observe fields")
        if item.action in REPORT_ACTIONS:
            kind = REPORT_ACTIONS[item.action]
            if item.program or item.request_id:
                raise ValueError(f"{item.decision_id}: report rows must not name program or request")
            if (kind == "Instrument_Failure") == bool(item.tile_id):
                raise ValueError(f"{item.decision_id}: fault reports name no tile; tag reports require one")
        seen.add(item.decision_id)
        rows.append(item)
    return rows


def load_tile_anomalies(path: Path, tiles: Mapping[str, Tile]) -> dict[str, frozenset[str]]:
    """Hidden per-tile anomaly tags (nova/reddening); absent file means no tags."""
    tags: defaultdict[str, set[str]] = defaultdict(set)
    for row in read_exact_csv(path, TILE_ANOMALY_COLUMNS):
        tile_id, tag = row["tile_id"].strip(), row["anomaly_tag"].strip()
        if tile_id not in tiles:
            raise ValueError(f"tile_anomalies: unknown tile_id {tile_id!r}")
        if tag not in ANOMALY_TAG_VALUES:
            raise ValueError(f"tile_anomalies: unknown anomaly_tag {tag!r}")
        tags[tile_id].add(tag)
    return {tile_id: frozenset(values) for tile_id, values in tags.items()}


@dataclass(frozen=True)
class Report:
    """One anomaly report; lives as a report_* action row inside decisions.csv."""

    report_id: str
    kind: str
    tile_id: str


def load_score_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("schema_version") != "challenge-score-v3":
        raise ValueError("unsupported score config")
    aggregation = config.get("repeat_observation", {}).get("tile_score_aggregation", "max")
    if aggregation != "max":
        raise ValueError(f"unsupported repeat_observation.tile_score_aggregation {aggregation!r}")
    return config


def load_tile_values(path: Path, tiles: Mapping[str, Tile]) -> dict[str, float]:
    values: defaultdict[str, float] = defaultdict(float)
    seen = set()
    for row in read_exact_csv(path, TARGET_COLUMNS):
        if not row["target_id"] or row["target_id"] in seen or row["tile_id"] not in tiles:
            raise ValueError("invalid targets catalog relation")
        value = float(row["science_weight"])
        if not math.isfinite(value) or value <= 0:
            raise ValueError("science_weight must be positive and finite")
        seen.add(row["target_id"])
        values[row["tile_id"]] += value
    if set(values) != set(tiles):
        raise ValueError("every tile must have target-derived value")
    return dict(values)


def _coverage_evenness(completed_by_region: "Counter[str]", regions: "list[str]") -> float:
    """How evenly the finished tiles are spread over the survey regions, as Jain's fairness index.

    (sum x)^2 / (n * sum x^2): 1.0 when every region got the same number of tiles, 1/n when one region took
    everything, 0 when nothing was observed. Wide surveys need even coverage to support the statistics they
    exist for, but a score that only adds up per-tile science is indifferent to where those tiles are — an agent
    can abandon a whole region for free. Weighting this term makes that choice cost something.
    """
    if not regions:
        return 0.0
    counts = [float(completed_by_region.get(region, 0)) for region in regions]
    total = sum(counts)
    if total <= 0.0:
        return 0.0
    return (total * total) / (len(counts) * sum(value * value for value in counts))


class ChallengeScorer:
    def __init__(
        self,
        slots: Sequence[Slot],
        tiles: Sequence[Tile],
        tile_values: Mapping[str, float],
        geometry: TileGeometrySimulator,
        weather: WeatherSimulator,
        requests: Sequence[ObservationRequest],
        request_tiles: Mapping[str, Mapping[str, int]],
        score_config: Mapping,
        tile_anomalies: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self.slots = list(slots)
        self.slot_indices = {slot.slot_id: index for index, slot in enumerate(slots)}
        self.tiles = {tile.tile_id: tile for tile in tiles}
        self.tile_values = dict(tile_values)
        self.geometry = geometry
        self.weather = weather
        self.requests = {item.request_id: item for item in requests}
        self.request_tiles = {key: dict(value) for key, value in request_tiles.items()}
        self.config = score_config
        self.mechanics = anomaly_mechanics_enabled(score_config)
        self.tile_anomalies = {key: frozenset(value) for key, value in (tile_anomalies or {}).items()}
        tag_config = score_config.get("anomaly_tags", {})
        self._tag_factors = {
            "nova": float(tag_config.get("nova_factor", 1.5)),
            "reddening": float(tag_config.get("reddening_factor", 0.8)),
        }
        reporting = score_config.get("reporting", {})
        self._report_reward = float(reporting.get("reward_correct", 100.0))
        self._report_penalty = float(reporting.get("penalty_wrong", 150.0))
        self._misreport_allowance = int(reporting.get("fault_misreport_free_allowance", 1))
        self._misreport_penalty = float(reporting.get("fault_misreport_penalty", 100.0))
        fault_response = score_config.get("fault_response", {})
        self._repair_duration = timedelta(days=float(fault_response.get("repair_duration_days", 2)))
        self.slot_index = 0
        self.offset_seconds = 0
        self.completed_tiles: set[str] = set()
        self.tile_best_scores: dict[str, tuple[float, float]] = {}
        self.request_visits: Counter[tuple[str, str]] = Counter()
        self.fault_acknowledgements: dict[str, datetime] = {}
        self.fault_correct_reports = 0
        self.misreport_count = 0
        self.misreport_total = 0
        self.tag_reports: dict[tuple[str, str], str] = {}
        self.actions: list[dict[str, object]] = []
        self.base_science_score = 0.0
        self.program_bonus_score = 0.0
        self.penalties: Counter[str] = Counter()
        self.wait_seconds: Counter[str] = Counter()

    @classmethod
    def from_files(cls, root: Path) -> "ChallengeScorer":
        config = root / "config"
        output = root / "outputs" / "reference"
        tiles = load_tiles(output / "tiles.csv")
        geometry = TileGeometrySimulator.from_files(output / "tiles.csv", config / "tile_config.json", config / "calendar_config.json", output / "night_calendar.csv", output / "slots.csv")
        weather = WeatherSimulator(load_weather(output / "weather.csv"), load_forecasts(output / "weather_forecasts.csv"), load_events(output / "weather_events.csv"), load_weather_config(config / "weather_config.json"), geometry)
        tile_map = {item.tile_id: item for item in tiles}
        anomalies_path = output / "tile_anomalies.csv"
        return cls(load_slots(output / "slots.csv"), tiles, load_tile_values(output / "targets.csv", tile_map), geometry, weather,
                   load_requests(output / "observation_requests.csv"), load_request_tiles(output / "observation_request_tiles.csv"), load_score_config(config / "score_config.json"),
                   load_tile_anomalies(anomalies_path, tile_map) if anomalies_path.exists() else None)

    def current_slot(self) -> Slot | None:
        return self.slots[self.slot_index] if self.slot_index < len(self.slots) else None

    def current_time(self) -> datetime | None:
        slot = self.current_slot()
        return None if slot is None else slot.timestamp_utc + timedelta(seconds=self.offset_seconds)

    def _advance(self, seconds: int) -> None:
        slot = self.current_slot()
        if slot is None or not 0 <= seconds <= slot.duration_seconds - self.offset_seconds:
            raise RuntimeError("invalid simulation cursor advance")
        self.offset_seconds += seconds
        if self.offset_seconds == slot.duration_seconds:
            self.slot_index += 1
            self.offset_seconds = 0

    def _quality_band(self, quality: float) -> str:
        return "DARK" if quality >= float(self.config["quality_thresholds"]["dark"]) else "BRIGHT" if quality >= float(self.config["quality_thresholds"]["bright"]) else "BACKUP"

    def _anomaly_factor(self, tile_id: str) -> float:
        """Hidden per-tile truth multiplier; the published tile_science_value stays untagged."""
        factor = 1.0
        for tag in self.tile_anomalies.get(tile_id, ()):
            factor *= self._tag_factors[tag]
        return factor

    def _tile_legal(self, tile: Tile, moment: datetime) -> bool:
        if not tile.available_from_utc <= moment < tile.available_until_utc:
            return False
        sample = self.geometry.get_tile_geometry(tile.tile_id, moment)
        return float(sample["altitude_deg"]) >= float(
            self.geometry.tile_config["geometry"]["minimum_altitude_deg"]
        )

    def _has_actionable_tile(self) -> bool:
        if self.current_slot() is None:
            return False
        for tile in self.tiles.values():
            if tile.tile_id not in self.completed_tiles:
                if self._can_complete_from(tile, self.slot_index, self.offset_seconds):
                    return True
                continue
            if not self.mechanics:
                continue
            # A completed tile stays actionable while a repeat started now could beat its banked best.
            best = self.tile_best_scores.get(tile.tile_id)
            banked = 0.0 if best is None else best[0] + best[1]
            if self._repeat_score_potential(tile, self.slot_index, self.offset_seconds) > banked + 1e-9:
                return True
        return False

    def _repeat_score_potential(self, tile: Tile, slot_index: int, offset_seconds: int) -> float:
        """Best-program score a repeat observation started at the cursor would earn under truth weather (0 when it cannot complete)."""
        if slot_index >= len(self.slots):
            return 0.0
        first_night = self.slots[slot_index].night_id
        remaining = tile.nominal_exptime_seconds
        segments = []
        while remaining > 0 and slot_index < len(self.slots):
            slot = self.slots[slot_index]
            if slot.night_id != first_night:
                return 0.0
            start = slot.timestamp_utc + timedelta(seconds=offset_seconds)
            seconds = min(remaining, slot.duration_seconds - offset_seconds)
            midpoint = start + timedelta(seconds=seconds / 2)
            if not self._tile_legal(tile, start) or not self._tile_legal(tile, midpoint):
                return 0.0
            conditions = self.weather.get_effective_conditions(slot.slot_id, tile.tile_id)
            if not conditions["is_observable"]:
                return 0.0
            geometry = self.geometry.get_tile_geometry(tile.tile_id, midpoint)
            combined = weather_quality(conditions, float(geometry["airmass"]), self.weather.config) * float(geometry["lunar_quality_factor"])
            band_quality = weather_quality(conditions, float(geometry["airmass"]), self.weather.config, include_efficiency=False) * float(geometry["lunar_quality_factor"])
            base = self.tile_values[tile.tile_id] * seconds / tile.nominal_exptime_seconds * combined * self._anomaly_factor(tile.tile_id)
            segments.append((base, self._quality_band(band_quality)))
            remaining -= seconds
            slot_index += 1
            offset_seconds = 0
        if remaining > 0:
            return 0.0
        bonuses = self.config["program_bonus"]
        return max(
            sum(base * (1.0 + float(bonuses[program]) if band == program else 1.0) for base, band in segments)
            for program in PROGRAMS
        )

    def _can_complete_from(
        self, tile: Tile, slot_index: int, offset_seconds: int = 0
    ) -> bool:
        if slot_index >= len(self.slots):
            return False
        first_night = self.slots[slot_index].night_id
        remaining = tile.nominal_exptime_seconds
        while remaining > 0 and slot_index < len(self.slots):
            slot = self.slots[slot_index]
            if slot.night_id != first_night:
                return False
            start = slot.timestamp_utc + timedelta(seconds=offset_seconds)
            seconds = min(remaining, slot.duration_seconds - offset_seconds)
            midpoint = start + timedelta(seconds=seconds / 2)
            if not self._tile_legal(tile, start) or not self._tile_legal(tile, midpoint):
                return False
            if not self.weather.get_effective_conditions(
                slot.slot_id, tile.tile_id
            )["is_observable"]:
                return False
            remaining -= seconds
            slot_index += 1
            offset_seconds = 0
        return remaining == 0

    def _tile_has_request_opportunity(
        self, tile: Tile, available_from: datetime, deadline: datetime
    ) -> bool:
        for index, slot in enumerate(self.slots):
            if slot.timestamp_utc < available_from:
                continue
            if slot.timestamp_utc >= deadline:
                break
            if slot.timestamp_utc + timedelta(
                seconds=tile.nominal_exptime_seconds
            ) > deadline:
                continue
            if self._can_complete_from(tile, index):
                return True
        return False

    def _consume_wait(self, seconds: int, category: str) -> None:
        avoidable = self._has_actionable_tile()
        self.wait_seconds[category] += seconds
        self.wait_seconds["avoidable" if avoidable else "unavailable"] += seconds
        if avoidable:
            self.penalties["avoidable_wait"] += seconds * float(self.config["penalties"]["avoidable_wait_per_second"])
        self._advance(seconds)

    def _consume_until(self, target_index: int) -> None:
        while self.slot_index < target_index and self.current_slot() is not None:
            self._consume_wait(self.current_slot().duration_seconds - self.offset_seconds, "implicit")

    def _invalid(self, decision: Decision, outcome: str, unsafe: bool = False) -> dict[str, object]:
        slot, started = self.current_slot(), self.current_time()
        elapsed = 0
        if slot is not None:
            elapsed = slot.duration_seconds - self.offset_seconds
            self._consume_wait(elapsed, "invalid")
        key = "unsafe_observation" if unsafe else "invalid_action"
        penalty = float(self.config["penalties"][key])
        self.penalties[key] += penalty
        action = {"decision_id": decision.decision_id, "slot_id": decision.slot_id, "action": decision.action, "tile_id": decision.tile_id,
                  "program": decision.program, "request_id": decision.request_id, "start_utc": "" if started is None else format_utc(started),
                  "elapsed_seconds": elapsed, "outcome": outcome, "base_science_score": 0.0, "program_bonus_score": 0.0,
                  "penalty": penalty, "segments": []}
        self.actions.append(action)
        return action

    def apply_decision(self, decision: Decision) -> dict[str, object]:
        if decision.action in REPORT_ACTIONS and not self.mechanics:
            return self._invalid(decision, "unknown_action")
        if decision.action in REPORT_ACTIONS:
            # Report rows ride the trace: they never touch the slot cursor and act
            # at the current cursor time (right after their carrier decision).
            as_of = self.current_time() or self.slots[-1].end_utc
            kind = REPORT_ACTIONS[decision.action]
            if kind != "Instrument_Failure" and decision.tile_id not in self.tiles:
                action = {"decision_id": decision.decision_id, "slot_id": decision.slot_id, "action": decision.action, "tile_id": decision.tile_id,
                          "program": "", "request_id": "", "start_utc": format_utc(as_of), "elapsed_seconds": 0,
                          "outcome": "report_dropped", "base_science_score": 0.0, "program_bonus_score": 0.0, "penalty": 0.0, "segments": [],
                          "report_result": {"report_id": decision.decision_id, "kind": kind, "result": "dropped_unknown_tile"}}
                self.actions.append(action)
                return action
            before = float(self.penalties["fault_misreport"])
            outcome = self.apply_report(Report(decision.decision_id, kind, decision.tile_id), as_of)
            action = {"decision_id": decision.decision_id, "slot_id": decision.slot_id, "action": decision.action, "tile_id": decision.tile_id,
                      "program": "", "request_id": "", "start_utc": format_utc(as_of), "elapsed_seconds": 0,
                      "outcome": f"report_{outcome['result']}", "base_science_score": 0.0, "program_bonus_score": 0.0,
                      "penalty": round(float(self.penalties["fault_misreport"]) - before, 6), "segments": [],
                      "report_result": outcome}
            self.actions.append(action)
            return action
        if decision.slot_id not in self.slot_indices:
            return self._invalid(decision, "unknown_slot")
        target = self.slot_indices[decision.slot_id]
        if target < self.slot_index:
            penalty = float(self.config["penalties"]["invalid_action"])
            self.penalties["invalid_action"] += penalty
            action = {"decision_id": decision.decision_id, "slot_id": decision.slot_id, "action": decision.action, "tile_id": decision.tile_id,
                      "program": decision.program, "request_id": decision.request_id, "start_utc": "", "elapsed_seconds": 0,
                      "outcome": "stale_decision", "base_science_score": 0.0, "program_bonus_score": 0.0, "penalty": penalty, "segments": []}
            self.actions.append(action)
            return action
        self._consume_until(target)
        if decision.action == "wait":
            started, slot = self.current_time(), self.current_slot()
            elapsed = 0 if slot is None else slot.duration_seconds - self.offset_seconds
            if slot is not None:
                self._consume_wait(elapsed, "explicit")
            action = {"decision_id": decision.decision_id, "slot_id": decision.slot_id, "action": "wait", "tile_id": "", "program": "",
                      "request_id": "", "start_utc": "" if started is None else format_utc(started), "elapsed_seconds": elapsed, "outcome": "wait",
                      "base_science_score": 0.0, "program_bonus_score": 0.0, "penalty": 0.0, "segments": []}
            self.actions.append(action)
            return action
        tile = self.tiles.get(decision.tile_id)
        slot, started = self.current_slot(), self.current_time()
        if tile is None or slot is None or started is None or decision.program not in PROGRAMS:
            return self._invalid(decision, "invalid_observe")
        request = self.requests.get(decision.request_id) if decision.request_id else None
        if decision.request_id and (request is None or tile.tile_id not in self.request_tiles.get(decision.request_id, {}) or not request.available_from_utc <= started < request.deadline_utc):
            return self._invalid(decision, "invalid_request_tag")
        if not self.mechanics and tile.tile_id in self.completed_tiles and request is None:
            return self._invalid(decision, "duplicate_tile")
        initial_weather = self.weather.get_effective_conditions(slot.slot_id, tile.tile_id)
        if not initial_weather["is_observable"]:
            return self._invalid(decision, "unsafe_observation", unsafe=True)
        if not self._tile_legal(tile, started):
            return self._invalid(decision, "outside_tile_window")
        remaining = tile.nominal_exptime_seconds
        pending_base = pending_bonus = 0.0
        segments = []
        outcome = "completed"
        while remaining > 0:
            current_slot, moment = self.current_slot(), self.current_time()
            if current_slot is None or moment is None or current_slot.night_id != slot.night_id or not self._tile_legal(tile, moment):
                outcome = "geometry_or_night_interrupted"
                break
            conditions = self.weather.get_effective_conditions(current_slot.slot_id, tile.tile_id)
            if not conditions["is_observable"]:
                outcome = "weather_interrupted"
                break
            seconds = min(remaining, current_slot.duration_seconds - self.offset_seconds)
            midpoint = moment + timedelta(seconds=seconds / 2)
            geometry = self.geometry.get_tile_geometry(tile.tile_id, midpoint)
            atmospheric_quality = weather_quality(
                conditions, float(geometry["airmass"]), self.weather.config
            )
            # Bands never see instrument efficiency: preview and replay always agree.
            band_quality = weather_quality(
                conditions, float(geometry["airmass"]), self.weather.config, include_efficiency=False
            )
            lunar_quality = float(geometry["lunar_quality_factor"])
            combined_quality = atmospheric_quality * lunar_quality
            band = self._quality_band((band_quality if self.mechanics else atmospheric_quality) * lunar_quality)
            base = (
                self.tile_values[tile.tile_id]
                * seconds
                / tile.nominal_exptime_seconds
                * combined_quality
                * self._anomaly_factor(tile.tile_id)
            )
            bonus = base * (float(self.config["program_bonus"][decision.program]) if decision.program == band else 0.0)
            pending_base += base
            pending_bonus += bonus
            segments.append({"slot_id": current_slot.slot_id, "start_utc": format_utc(moment), "duration_seconds": seconds,
                             "airmass": round(float(geometry["airmass"]), 6), "active_event_ids": conditions["active_event_ids"],
                             "atmospheric_quality": round(atmospheric_quality, 6),
                             "lunar_quality_factor": round(lunar_quality, 6),
                             "combined_quality": round(combined_quality, 6),
                             "quality_band": band, "program_matched": decision.program == band,
                             "base_science_score": round(base, 6), "program_bonus_score": round(bonus, 6)})
            self._advance(seconds)
            remaining -= seconds
        completed = remaining == 0 and outcome == "completed"
        action_penalty = 0.0
        if not completed and outcome == "geometry_or_night_interrupted":
            action_penalty = float(self.config["penalties"]["invalid_action"])
            self.penalties["invalid_action"] += action_penalty
        if completed:
            if not self.mechanics:
                # Pre-anomaly semantics: ordinary science/completion credit banks once.
                if tile.tile_id not in self.completed_tiles:
                    self.completed_tiles.add(tile.tile_id)
                    self.base_science_score += pending_base
                    self.program_bonus_score += pending_bonus
                else:
                    # A request-tagged revisit is operationally valid but cannot
                    # duplicate the tile's ordinary science/completion credit.
                    pending_base = pending_bonus = 0.0
            else:
                # Completion banks once, on the first legal observation; the tile's
                # science contribution is the per-observation maximum and only grows.
                self.completed_tiles.add(tile.tile_id)
                banked = self.tile_best_scores.get(tile.tile_id, (0.0, 0.0))
                if pending_base + pending_bonus > banked[0] + banked[1]:
                    self.base_science_score += pending_base - banked[0]
                    self.program_bonus_score += pending_bonus - banked[1]
                    self.tile_best_scores[tile.tile_id] = (pending_base, pending_bonus)
            if request is not None:
                self.request_visits[(request.request_id, tile.tile_id)] += 1
        else:
            pending_base = pending_bonus = 0.0
        action = {"decision_id": decision.decision_id, "slot_id": decision.slot_id, "action": "observe", "tile_id": tile.tile_id,
                  "program": decision.program, "request_id": decision.request_id, "start_utc": format_utc(started),
                  "elapsed_seconds": tile.nominal_exptime_seconds - remaining, "outcome": outcome, "base_science_score": round(pending_base, 6),
                  "program_bonus_score": round(pending_bonus, 6), "penalty": action_penalty, "segments": segments}
        self.actions.append(action)
        return action

    def apply_report(self, report: Report, as_of: datetime) -> dict[str, object]:
        """Book one participant report at cursor time as_of; never touches the cursor.

        Fault reports are settled in-run (they gate the fault publication and
        repair); tag reports are pure detection judgments settled in finalize.
        """
        if report.kind == "Instrument_Failure":
            active = [
                event for event in self.weather.events
                if event.condition == "instrument_fault"
                and event.actual_start_utc <= as_of < self.weather.end_overrides.get(event.event_id, event.actual_end_utc)
            ]
            unacknowledged = [event for event in active if event.event_id not in self.fault_acknowledgements]
            if unacknowledged:
                repair_at = as_of + self._repair_duration
                for event in unacknowledged:
                    self.fault_acknowledgements[event.event_id] = repair_at
                    self.weather.end_overrides[event.event_id] = min(event.actual_end_utc, repair_at)
                self.fault_correct_reports += 1
                self.misreport_count = 0
                result = "correct"
            elif active:
                result = "neutral"  # acknowledged fault, still under repair
            else:
                self.misreport_count += 1
                self.misreport_total += 1
                if self.misreport_count > self._misreport_allowance:
                    self.penalties["fault_misreport"] += self._misreport_penalty
                result = "misreport"
            return {"report_id": report.report_id, "kind": report.kind, "result": result,
                    "acknowledged_event_ids": [event.event_id for event in unacknowledged] if result == "correct" else [],
                    "repair_complete_utc": None if result != "correct" else format_utc(repair_at)}
        tag = "nova" if report.kind == "NOVA" else "reddening"
        key = (report.tile_id, tag)
        if key in self.tag_reports:
            return {"report_id": report.report_id, "kind": report.kind, "result": "duplicate_ignored"}
        self.tag_reports[key] = report.report_id
        return {"report_id": report.report_id, "kind": report.kind, "result": "recorded"}

    def _settle_tag_reports(self) -> tuple[list[dict[str, object]], float]:
        rows = []
        reward_total = 0.0
        penalty_total = 0.0
        for tile_id, tag in sorted(self.tag_reports):
            correct = tag in self.tile_anomalies.get(tile_id, frozenset())
            delta = self._report_reward if correct else -self._report_penalty
            reward_total += max(0.0, delta)
            penalty_total += max(0.0, -delta)
            rows.append({"tile_id": tile_id, "tag": tag, "report_id": self.tag_reports[(tile_id, tag)],
                         "settled": "correct" if correct else "wrong", "delta": round(delta, 6)})
        if penalty_total:
            self.penalties["wrong_tag_report"] += penalty_total
        return rows, reward_total

    def finalize(self, termination_reason: str = "trace_complete") -> dict[str, object]:
        required_missing = sorted(tile.tile_id for tile in self.tiles.values() if tile.scheduling_class == "REQUIRED" and tile.tile_id not in self.completed_tiles)
        self.penalties["required_miss"] = len(required_missing) * float(self.config["penalties"]["required_miss"])
        flexible = Counter(self.tiles[tile].region_id for tile in self.completed_tiles if self.tiles[tile].scheduling_class == "FLEXIBLE")
        regions = sorted({tile.region_id for tile in self.tiles.values()})
        quota = int(self.config["flexible_quota_per_region"])
        shortfall = {region: max(0, quota - flexible[region]) for region in regions}
        self.penalties["flexible_shortfall"] = sum(shortfall.values()) * float(self.config["penalties"]["flexible_shortfall_per_tile"])
        final_time = self.current_time()
        if final_time is None and self.slots:
            final_time = self.slots[-1].end_utc
        request_rows = []
        request_reward = 0.0
        request_penalty = 0.0
        for request in sorted(self.requests.values(), key=lambda item: item.request_id):
            if final_time is None or request.issued_at_utc > final_time:
                continue
            satisfied = sum(self.request_visits[(request.request_id, tile_id)] >= visits for tile_id, visits in self.request_tiles[request.request_id].items())
            completed = satisfied >= request.required_tile_count
            expired = request.deadline_utc <= final_time
            feasible_count = (
                sum(
                    self._tile_has_request_opportunity(
                        self.tiles[tile_id],
                        request.available_from_utc,
                        request.deadline_utc,
                    )
                    for tile_id in self.request_tiles[request.request_id]
                )
                if expired and not completed
                else None
            )
            excused = (
                feasible_count is not None
                and feasible_count < request.required_tile_count
            )
            status = (
                "completed"
                if completed
                else "excused_unobservable"
                if excused
                else "missed"
                if expired
                else "active_incomplete"
            )
            reward = request.completion_reward if completed else 0.0
            penalty = request.miss_penalty if expired and not completed and not excused else 0.0
            request_reward += reward
            request_penalty += penalty
            request_rows.append({"request_id": request.request_id, "status": status, "satisfied_tile_count": satisfied,
                                 "required_tile_count": request.required_tile_count, "feasible_tile_count": feasible_count,
                                 "reward": reward, "penalty": penalty})
        self.penalties["request_miss"] = request_penalty
        tag_settlements, report_reward = self._settle_tag_reports()
        coverage_evenness = _coverage_evenness(
            Counter(self.tiles[tile].region_id for tile in self.completed_tiles), regions
        )
        coverage_weight = float(self.config.get("coverage_bonus_weight", 0.0))
        coverage_bonus = coverage_weight * self.base_science_score * coverage_evenness
        subtotal = self.base_science_score + self.program_bonus_score + request_reward + coverage_bonus + report_reward
        total_penalty = sum(self.penalties.values())
        slot = self.current_slot()
        return {
            "schema_version": "score-report-v3", "termination_reason": termination_reason,
            "final_cursor": {"slot_id": None if slot is None else slot.slot_id, "slot_index": self.slot_index, "offset_seconds": self.offset_seconds,
                             "timestamp_utc": None if final_time is None else format_utc(final_time)},
            "score": {"total": round(subtotal - total_penalty, 6), "base_science": round(self.base_science_score, 6),
                      "program_bonus": round(self.program_bonus_score, 6), "request_reward": round(request_reward, 6),
                      "coverage_bonus": round(coverage_bonus, 6), "coverage_evenness": round(coverage_evenness, 6),
                      "report_reward": round(report_reward, 6),
                      "penalties": {key: round(value, 6) for key, value in sorted(self.penalties.items())}},
            "completion": {"completed_tiles": sorted(self.completed_tiles), "required_missing": required_missing,
                           "flexible_by_region": dict(sorted(flexible.items())), "flexible_shortfall": shortfall},
            "requests": request_rows, "wait_seconds": dict(sorted(self.wait_seconds.items())),
            "reports": {
                "tag_settlements": tag_settlements,
                "fault_correct_reports": self.fault_correct_reports,
                "fault_misreports": self.misreport_total,
                "fault_acknowledged_event_ids": sorted(self.fault_acknowledgements),
            },
            "actions": self.actions,
            "parameters": {
                "score_config": self.config,
                "weather_score_interface": self.weather.config["score_interface"],
                "lunar_model": self.geometry.tile_config["lunar_model"],
            },
        }


def score_files(root: Path, decisions_path: Path, output_path: Path, termination_reason: str = "trace_complete") -> dict[str, object]:
    scorer = ChallengeScorer.from_files(root)
    for decision in load_decisions(decisions_path, allow_reports=scorer.mechanics):
        scorer.apply_decision(decision)
    report = scorer.finalize(termination_reason)
    report["input_sha256"] = {"decisions": sha256_file(decisions_path), "score_config": sha256_file(root / "config" / "score_config.json")}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(output_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report
