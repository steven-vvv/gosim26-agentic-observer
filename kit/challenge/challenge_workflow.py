"""Global-wallclock workflow joining all example3 simulator interfaces."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping

from .contracts import (
    write_text_lf,
    ACCEPTED_PROTOCOL_VERSIONS,
    DECISION_COLUMNS,
    DECISION_SNAPSHOT_VERSION,
    INITIAL_PUBLICATION_VERSION,
    LEGACY_DECISION_SNAPSHOT_VERSION,
    REPORT_KINDS,
    TARGET_COLUMNS,
    WORKFLOW_RESULT_VERSION,
    format_utc,
    parse_utc,
    read_exact_csv,
    write_exact_csv,
)
from .observation_request_simulator import ObservationRequestSimulator
from .project_paths import EXAMPLE3_ROOT
from .scoring_core import ChallengeScorer, Decision


DecisionProvider = Callable[[Mapping[str, object], float], Mapping[str, object]]


FEEDBACK_OUTCOMES = ("completed", "weather_interrupted", "geometry_or_night_interrupted")


def _public_weather(conditions: Mapping[str, object]) -> dict[str, object]:
    """The agent-visible weather view never carries instrument_efficiency: the gap
    between the efficiency-free preview baseline and realized scores is exactly the
    anomaly signal (jitter x fault multiplier x tag multiplier)."""
    return {key: value for key, value in conditions.items() if key != "instrument_efficiency"}


class GlobalDeadlineExpired(TimeoutError):
    """Raised by a cancellable agent transport at the global cutoff."""


def load_workflow_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("schema_version") != "challenge-workflow-v1":
        raise ValueError("unsupported workflow schema_version")
    if float(config["global_wallclock_seconds"]) <= 0:
        raise ValueError("global wallclock must be positive")
    if config["per_decision_timeout_seconds"] is not None or config["synthetic_timeout_action"] is not None:
        raise ValueError("example3 forbids per-decision timeout rules and synthetic timeout actions")
    return config


class ChallengeWorkflow:
    """Authority-neutral harness; production transports may cancel in-flight I/O."""

    def __init__(self, root: Path = EXAMPLE3_ROOT, clock: Callable[[], float] = time.monotonic) -> None:
        self.root = root
        self.config = load_workflow_config(root / "config" / "workflow_config.json")
        self.scorer = ChallengeScorer.from_files(root)
        self.requests = ObservationRequestSimulator.from_files(
            root / "outputs" / "reference" / "observation_requests.csv",
            root / "outputs" / "reference" / "observation_request_tiles.csv",
        )
        self.target_catalog = read_exact_csv(
            root / "outputs" / "reference" / "targets.csv", TARGET_COLUMNS
        )
        self.clock = clock
        self.mechanics = self.scorer.mechanics
        self.committed: list[Decision] = []
        self.commit_log: list[dict[str, object]] = []
        self._row_seq = 0
        self._last_finished: dict[str, object] | None = None
        self._fault_feed: list[dict[str, object]] = []
        self._normal_notices: list[dict[str, object]] = []
        self._fault_latency_days = float(self.scorer.config.get("fault_response", {}).get("response_latency_days", 1))
        self._night_cache: dict[str, list[dict[str, object]]] = {}
        self._week_cache: dict[str, dict[str, object]] = {}

    def initial_publication(self) -> dict[str, object]:
        """Return immutable public catalogs and the exact official score contract."""
        nights = list(self.scorer.geometry.nights.values())
        tiles = list(self.scorer.tiles.values())
        return {
            "schema_version": INITIAL_PUBLICATION_VERSION,
            "calendar": {
                "first_night": nights[0].night_date.isoformat(),
                "last_night": nights[-1].night_date.isoformat(),
                "night_count": len(nights),
                "slot_count": len(self.scorer.slots),
                "slot_duration_seconds": self.scorer.slots[0].duration_seconds,
            },
            "site": self.scorer.geometry.calendar_config["site"],
            "tile_catalog": {
                "tile_count": len(tiles),
                "required_tile_ids": sorted(item.tile_id for item in tiles if item.scheduling_class == "REQUIRED"),
                "region_ids": sorted({item.region_id for item in tiles}),
                "tiles": [
                    {
                        **item.csv_row(),
                        "tile_science_value": round(
                            self.scorer.tile_values[item.tile_id], 6
                        ),
                    }
                    for item in sorted(tiles, key=lambda value: value.tile_id)
                ],
            },
            "target_catalog": self.target_catalog,
            "scoring_contract": {
                "score_config": self.scorer.config,
                "weather_score_interface": self.scorer.weather.config[
                    "score_interface"
                ],
                "lunar_model": self.scorer.geometry.tile_config["lunar_model"],
                "preview_semantics": (
                    "Current-snapshot estimates use the official public formula but "
                    "cannot know unreleased future slot weather. Authoritative scores "
                    "are computed by segmented replay."
                ),
            },
            "global_wallclock_seconds": float(self.config["global_wallclock_seconds"]),
        }

    def _night_windows(self, night_id: str) -> list[dict[str, object]]:
        if night_id not in self._night_cache:
            night = self.scorer.geometry.nights[night_id]
            self._night_cache[night_id] = self.scorer.geometry.get_tile_windows(night.night_date, 1)
        return self._night_cache[night_id]

    def _weekly_publication(self, slot) -> dict[str, object]:
        if slot.night_id not in self._week_cache:
            night = self.scorer.geometry.nights[slot.night_id]
            ordered_nights = sorted(self.scorer.geometry.nights)
            remaining_nights = len(ordered_nights) - ordered_nights.index(slot.night_id)
            days = min(int(self.config["tile_window_horizon_days"]), remaining_nights)
            windows = self.scorer.geometry.get_tile_windows(night.night_date, days)
            self._week_cache[slot.night_id] = {
                "issued_at_utc": format_utc(slot.timestamp_utc),
                "weather_forecast": self.scorer.weather.get_weather_forecast(slot.timestamp_utc, int(self.config["weekly_horizon_days"])),
                "tile_windows": windows,
                "observation_requests": self.requests.get_observation_requests(slot.timestamp_utc),
            }
        return self._week_cache[slot.night_id]

    def _active_requests(self, moment: datetime) -> list[dict[str, object]]:
        """Add current visit progress to time-safe request publications."""
        publications = self.requests.get_observation_requests(moment)
        enriched = []
        for request in publications:
            payload = dict(request)
            requirements = []
            satisfied = 0
            for requirement in request["tile_requirements"]:
                tile_id = str(requirement["tile_id"])
                required = int(requirement["required_visits"])
                completed = int(
                    self.scorer.request_visits[(str(request["request_id"]), tile_id)]
                )
                satisfied += completed >= required
                requirements.append(
                    {
                        **requirement,
                        "completed_visits": completed,
                        "remaining_visits": max(0, required - completed),
                    }
                )
            payload["tile_requirements"] = requirements
            payload["satisfied_tile_count"] = satisfied
            payload["is_complete"] = satisfied >= int(request["required_tile_count"])
            enriched.append(payload)
        return enriched

    def _fault_status(self, moment: datetime) -> dict[str, object] | None:
        """Night-start publication: acknowledged unrepaired fault, or a one-shot 'instrument normal' answer."""
        latency = timedelta(days=self._fault_latency_days)
        publishable = [
            feed for feed in self._fault_feed
            if feed["reported_at"] + latency <= moment < feed["repair_complete_utc"]
        ]
        if publishable:
            feed = max(publishable, key=lambda item: item["reported_at"])
            event = next(item for item in self.scorer.weather.events if item.event_id == feed["event_id"])
            return {
                "status": "fault",
                "event_id": event.event_id,
                "spatial_scope_type": event.spatial_scope_type,
                "spatial_scope_payload": event.spatial_scope_payload,
                "instrument_efficiency_multiplier": round(event.instrument_efficiency_multiplier, 6),
                "reported_at_utc": format_utc(feed["reported_at"]),
                "published_at_utc": format_utc(moment),
                "repair_complete_utc": format_utc(feed["repair_complete_utc"]),
            }
        for notice in self._normal_notices:
            if not notice["published"] and notice["respond_at"] <= moment:
                notice["published"] = True
                return {
                    "status": "normal",
                    "reference_report_id": notice["reference_report_id"],
                    "published_at_utc": format_utc(moment),
                }
        return None

    def decision_snapshot(self, sequence: int) -> dict[str, object]:
        slot = self.scorer.current_slot()
        moment = self.scorer.current_time()
        if slot is None or moment is None:
            raise RuntimeError("survey is complete")
        active_windows = [
            row for row in self._night_windows(slot.night_id)
            if datetime.fromisoformat(str(row["window_start_utc"]).replace("Z", "+00:00")) <= moment
            < datetime.fromisoformat(str(row["window_end_utc"]).replace("Z", "+00:00"))
        ]
        candidates = []
        for row in active_windows:
            tile_id = str(row["tile_id"])
            conditions = (_public_weather(self.scorer.weather.get_effective_conditions(slot.slot_id, tile_id, include_instrument_faults=False))
                          if self.mechanics else self.scorer.weather.get_effective_conditions(slot.slot_id, tile_id))
            geometry = self.scorer.geometry.get_tile_geometry(tile_id, moment)
            if float(geometry["altitude_deg"]) < float(
                self.scorer.geometry.tile_config["geometry"]["minimum_altitude_deg"]
            ):
                continue
            candidates.append({"tile_id": tile_id, "region_id": row["region_id"], "scheduling_class": row["scheduling_class"],
                               "nominal_exptime_seconds": row["nominal_exptime_seconds"],
                               "tile_science_value": round(self.scorer.tile_values[tile_id], 6),
                               "window_start_utc": row["window_start_utc"], "window_end_utc": row["window_end_utc"],
                               "geometry": geometry, "effective_weather": conditions,
                               "already_completed": tile_id in self.scorer.completed_tiles})
        night = self.scorer.geometry.nights[slot.night_id]
        night_index = sorted(self.scorer.geometry.nights).index(slot.night_id)
        night_open = self.scorer.offset_seconds == 0 and slot == self.scorer.geometry.slots_by_night[slot.night_id][0]
        snapshot = {
            "schema_version": DECISION_SNAPSHOT_VERSION if self.mechanics else LEGACY_DECISION_SNAPSHOT_VERSION, "decision_sequence": sequence,
            "cursor": {"slot_id": slot.slot_id, "night_id": slot.night_id, "timestamp_utc": format_utc(moment), "slot_offset_seconds": self.scorer.offset_seconds},
            "current_site_weather": (_public_weather(self.scorer.weather.get_effective_conditions(slot.slot_id, include_instrument_faults=False))
                                     if self.mechanics else self.scorer.weather.get_effective_conditions(slot.slot_id)),
            "candidate_tiles": candidates,
            "active_requests": self._active_requests(moment),
            "night_start": {"night": night.csv_row(), "tile_windows": self._night_windows(slot.night_id)} if night_open else None,
            "weekly": self._weekly_publication(slot) if night_index % int(self.config["weekly_horizon_days"]) == 0 and night_open else None,
            "progress": {
                "completed_tile_ids": sorted(self.scorer.completed_tiles),
                "flexible_completed_by_region": dict(
                    sorted(
                        Counter(
                            self.scorer.tiles[tile_id].region_id
                            for tile_id in self.scorer.completed_tiles
                            if self.scorer.tiles[tile_id].scheduling_class
                            == "FLEXIBLE"
                        ).items()
                    )
                ),
            },
        }
        if self.mechanics:
            snapshot["tile_last_finished"] = self._last_finished
            if night_open:
                fault_status = self._fault_status(moment)
                if fault_status is not None:
                    snapshot["fault_status"] = fault_status
        return snapshot

    def _decision_from_response(self, sequence: int, slot_id: str, response: Mapping[str, object]) -> tuple[Decision, list[dict[str, str]], int]:
        """Validate one response; malformed report entries are dropped (counted), never the action."""
        if "protocol_version" in response and response["protocol_version"] not in ACCEPTED_PROTOCOL_VERSIONS:
            raise ValueError("unsupported participant protocol_version")
        if "message_type" in response and response["message_type"] != "decision_response":
            raise ValueError("agent response message_type must be decision_response")
        if "decision_sequence" in response and int(response["decision_sequence"]) != sequence:
            raise ValueError("agent response decision_sequence does not match request")
        action = str(response.get("action", ""))
        if action not in {"observe", "wait"}:
            raise ValueError("agent response action must be observe or wait")
        tile_id = str(response.get("tile_id", ""))
        program = str(response.get("program", ""))
        request_id = str(response.get("request_id", ""))
        reason = str(response.get("reason", ""))
        if action == "wait":
            tile_id = program = request_id = ""
        reports, dropped = [], 0
        raw_reports = response.get("reports", [])
        if not self.mechanics:
            # Legacy scenarios have nothing to report against; note and drop.
            dropped = len(raw_reports) if isinstance(raw_reports, list) else 1
            raw_reports = []
        for entry in raw_reports if isinstance(raw_reports, list) else []:
            if not isinstance(entry, Mapping):
                dropped += 1
                continue
            kind = str(entry.get("kind", ""))
            report_tile = str(entry.get("tile_id", ""))
            if kind not in REPORT_KINDS or (kind == "Instrument_Failure") == bool(report_tile):
                dropped += 1
                continue
            if report_tile and report_tile not in self.scorer.tiles:
                dropped += 1
                continue
            reports.append({"kind": kind, "tile_id": report_tile})
        dropped += 0 if isinstance(raw_reports, list) else 1
        return Decision(f"D{sequence:06d}", slot_id, action, tile_id, program, request_id, reason), reports, dropped

    def _commit(self, sequence: int, decision: Decision, reports: list[dict[str, str]]) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Apply one decision and its riding reports, flattened into the decisions.csv trace.

        The carrier decision and each report row share one incrementing row-id
        sequence; report rows act at the post-decision cursor time and never
        move the cursor.
        """
        self._row_seq += 1
        decision = replace(decision, decision_id=f"D{self._row_seq:06d}")
        result = self.scorer.apply_decision(decision)
        self.committed.append(decision)
        if result["outcome"] in FEEDBACK_OUTCOMES:
            self._last_finished = {"tile_id": result["tile_id"],
                                   "score": round(float(result["base_science_score"]) + float(result["program_bonus_score"]), 6)}
        report_outcomes = []
        for accepted in reports:
            kind = accepted["kind"]
            action = "report_instrument_failure" if kind == "Instrument_Failure" else f"report_{kind.lower()}"
            self._row_seq += 1
            row = Decision(f"D{self._row_seq:06d}", decision.slot_id, action, accepted["tile_id"], "", "", "")
            record = self.scorer.apply_decision(row)
            self.committed.append(row)
            outcome = record["report_result"]
            report_outcomes.append(outcome)
            if outcome["result"] == "correct":
                as_of = parse_utc(record["start_utc"])
                for event_id in outcome["acknowledged_event_ids"]:
                    self._fault_feed.append({"event_id": event_id, "reported_at": as_of, "repair_complete_utc": parse_utc(outcome["repair_complete_utc"])})
            elif outcome["result"] == "misreport":
                as_of = parse_utc(record["start_utc"])
                self._normal_notices.append({"reference_report_id": row.decision_id,
                                             "respond_at": as_of + timedelta(days=self._fault_latency_days), "published": False})
        return result, report_outcomes

    def run(self, provider: DecisionProvider, wallclock_seconds: float | None = None) -> dict[str, object]:
        initial = self.initial_publication()
        budget = float(wallclock_seconds if wallclock_seconds is not None else self.config["global_wallclock_seconds"])
        if budget <= 0:
            raise ValueError("wallclock_seconds must be positive")
        publisher = getattr(provider, "publish_initial", None)
        if callable(publisher):
            try:
                publisher(initial)
            except Exception as exc:
                report = self.scorer.finalize("agent_initialization_error")
                return {
                    "schema_version": WORKFLOW_RESULT_VERSION,
                    "initial_publication": initial,
                    "termination_reason": "agent_initialization_error",
                    "global_wallclock_seconds": budget,
                    "accounted_wallclock_seconds": 0.0,
                    "ignored_in_flight_response": False,
                    "committed_action_count": 0,
                    "commit_log": [
                        {
                            "sequence": 0,
                            "committed": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    ],
                    "score_report": report,
                }
        started = self.clock()
        deadline = started + budget
        termination = "survey_complete"
        ignored_in_flight = False
        sequence = 1
        while self.scorer.current_slot() is not None:
            now = self.clock()
            if now >= deadline:
                termination = "global_wallclock_expired"
                break
            snapshot = self.decision_snapshot(sequence)
            try:
                response = provider(snapshot, deadline)
                completed_at = self.clock()
                if completed_at >= deadline:
                    termination = "global_wallclock_expired"
                    ignored_in_flight = True
                    break
                decision, reports, dropped_reports = self._decision_from_response(sequence, self.scorer.current_slot().slot_id, response)
            except GlobalDeadlineExpired:
                termination = "global_wallclock_expired"
                ignored_in_flight = True
                break
            except Exception as exc:
                termination = "agent_error"
                self.commit_log.append({"sequence": sequence, "committed": False, "error": f"{type(exc).__name__}: {exc}"})
                break
            result, report_outcomes = self._commit(sequence, decision, reports)
            log_entry = {"sequence": sequence, "committed": True, "completed_wallclock_seconds": completed_at - started,
                         "decision_id": decision.decision_id, "outcome": result["outcome"]}
            if report_outcomes:
                log_entry["reports"] = report_outcomes
            if dropped_reports:
                log_entry["dropped_reports"] = dropped_reports
            self.commit_log.append(log_entry)
            sequence += 1
        elapsed = max(0.0, min(self.clock(), deadline) - started)
        report = self.scorer.finalize(termination)
        return {"schema_version": WORKFLOW_RESULT_VERSION, "initial_publication": initial, "termination_reason": termination,
                "global_wallclock_seconds": budget, "accounted_wallclock_seconds": elapsed, "ignored_in_flight_response": ignored_in_flight,
                "committed_action_count": len(self.committed), "commit_log": self.commit_log, "score_report": report}

    def write_outputs(self, output_dir: Path, result: Mapping[str, object]) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        # decisions.csv carries the whole trace, including report_* action rows.
        write_exact_csv(output_dir / "decisions.csv", DECISION_COLUMNS, (item.csv_row() for item in self.committed))
        write_text_lf(output_dir / "workflow_result.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
