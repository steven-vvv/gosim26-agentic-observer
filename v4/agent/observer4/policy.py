"""Public-state lifecycle and safe survey actions around the numerical planner."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np

from .advice import ModelGuidance
from .domain import Task
from .geometry import format_utc, parse_utc
from .instrument import InstrumentMonitor, build_record, engine_revision
from .logbook import Logbook
from .planning import Planner
from .pointing import PointingModel
from .quality import QualityModel
from .state import SurveyState


class Observer:
    def __init__(self, publication: dict, config: dict, emit=None):
        self.config = config
        self.emit = emit or (lambda *_args, **_kwargs: None)
        self.task = Task(publication, config["policy"]["altitude_margin_deg"])
        self.task.revision = engine_revision(publication)
        self.state = SurveyState(self.task)
        self.quality = QualityModel(self.task, config)
        self.pointing = PointingModel(self.task)
        self.planner = Planner(self.task, self.state, self.quality, self.pointing, config)
        self.guidance = ModelGuidance(self.task, self.state, self.quality, self.pointing, config, self.emit)
        self.revision = engine_revision(publication)
        self.instrument = InstrumentMonitor(self.task, config["fault"], self.revision)
        self.quality.attach(self.instrument)
        self.planner.instrument = self.instrument
        self.logbook = Logbook(self.task, publication, config, self.emit)
        self.fault_mode = config["fault"].get("mode", "legacy")
        self.instrument_enabled = True
        self.level_errors = []
        self.pending = None
        self.last_trace = {}
        self.last_night = -1
        self.decisions = 0
        self.last_now = self.task.start
        self.decision_seconds = 0.0
        self.sequence = 0
        self.decision_source = "deterministic-v4"
        self.emit(
            "initialize",
            {
                "card": self.task.card,
                "targets": len(self.task.ids),
                "nights": len(self.task.nights),
                "required": int(self.task.required.sum()),
                "fibers": self.task.grid.n,
                "fiber_grid_side": self.task.grid.side,
                "fiber_contract": {"expected": 16, "matches": self.task.grid.n == 16},
                "wallclock_seconds": self.task.wallclock,
                "structure": self.planner.structure,
            },
        )

    def respond(self, snapshot: dict) -> dict:
        started = time.perf_counter()
        self.decision_source = "deterministic-v4"
        now = parse_utc(snapshot["now_utc"]).timestamp()
        if now < self.last_now:
            raise ValueError("decision time moved backwards")
        self.last_now = now
        night = self.task.current_night(now)
        result = snapshot.get("last_result")
        messages = snapshot.get("new_messages", [])
        bulletins = [m for m in messages if m.get("record_type") == "bulletin"]
        if self.pending and result and result.get("action") == "observe":
            samples = self.state.feedback(result, self.pending)
            self.pointing.feedback(self.pending, result)
            if not self.logbook.in_quiet(self.pending["start"], self.pending["start"] + self.pending["duration"]):
                # Frames of an announced test window say nothing about the sky or the instrument.
                self.quality.update(samples, self.pending, now, self.pending["night"])
                self._guarded(self._instrument_record, result, bulletins)
        self.pending = None
        self._guarded(self.instrument.notices, bulletins, now)
        outcomes = [m for m in messages if m.get("record_type") == "report_result"]
        if not outcomes and result and result.get("action") == "report" and "correct" in result:
            outcomes = [result]
        for outcome in outcomes:
            logged = self.instrument.pending is not None and self.instrument.pending.get("kind") == "logbook"
            self._guarded(self.instrument.report_result, outcome)
            if logged:
                self.logbook.outcome(bool(outcome.get("correct")))
            self.emit("report_result", {"correct": bool(outcome.get("correct")), **self.instrument.status()})
        self._read_log(messages)
        self.quality.messages(snapshot, now)
        self.planner.set_requests(snapshot.get("active_requests") or [])
        if self.planner.opportunity is not None:
            self.planner.opportunity.forecast(snapshot)
        for message in snapshot.get("new_messages", []):
            if message.get("record_type") == "state_resync":
                self.emit("state_resync", self.state.resync(message))
        named = (self.logbook.due(now, self._last_correct_report(), self.instrument.free_left())
                 if self.instrument_enabled and self.instrument.pending is None else None)
        quiet = self._quiet_end(now) if night is not None else None
        if named is not None:
            # The duty log names an instrument drop that no correct report has repaired: report
            # it now, closed dome, test window or daytime alike.
            evidence = {"kind": "logbook", "reference": None, "level": self.instrument._last_level(), "drop": 0.0}
            self.instrument.reported(evidence, now, night)
            self.logbook.reported(named, now)
            self.emit("fault_report", {"now_utc": snapshot["now_utc"], **evidence})
            action = {"action": "report"}
        elif night is None:
            action = self.wait_for_next_night(now)
        elif self.quality.closed():
            action = self.wait_for_bulletin(now, night)
        elif quiet is not None:
            # The log announces a flat-lamp or mirror-cover test (or a maintenance stop): its
            # frames are lost and a report then is a false one, so the time is waited out.
            action = {"action": "wait", "until_utc": format_utc(datetime.fromtimestamp(quiet, timezone.utc))}
        else:
            budget = float(snapshot.get("wallclock", {}).get("remaining_seconds", self.task.wallclock))
            if night != self.last_night:
                self.last_night = night
                self.planner.set_strategy(self.guidance.weekly_plan["strategy"])
                self.emit(
                    "night_plan",
                    {
                        "night": night,
                        "strategy": self.guidance.weekly_plan["strategy"],
                        "required_missing_conservative": self.state.required_missing(),
                        "quality": self.quality.status(),
                        "pointing": self.pointing.status(),
                    },
                )
            strategy = self.guidance.weekly(snapshot, night, budget, self.sequence)
            if strategy is not None:
                self.planner.set_strategy(strategy)
            if self.guidance.weekly_plan["source"] == "model":
                self.decision_source = "model-v4"
            evidence = None
            if self.fault_mode == "monitor":
                self.instrument.reserve = 1 if self.logbook.faults else 0
                evidence = self._guarded(self.instrument.decide, now, night)
                if evidence:
                    self.instrument.reported(evidence, now, night)
                    evidence = {key: value for key, value in evidence.items() if key != "before"}
            if self.fault_mode != "monitor":
                evidence = self.quality.report_evidence(now)
                if evidence:
                    self.quality.reported(now)
            if evidence:
                self.emit("fault_report", {"now_utc": snapshot["now_utc"], **evidence})
                action = {"action": "report"}
            elif self.quality.zero_observes >= 2:
                self.quality.zero_observes = 0
                action = self.wait_for_bulletin(now, night)
            else:
                boundary = self.logbook.next_boundary(now)
                self.planner.horizon_limit = boundary if boundary is not None and boundary > now else None
                action = self.planner.plan(now, night, budget)
                self.planner.horizon_limit = None
                self.last_trace = dict(self.planner.last_trace)
                if action is None:
                    action = self.wait_for_bulletin(now, night)
                elif action["action"] == "observe":
                    self.pending = self.planner.pending
                    choice = self.guidance.action(snapshot, night, budget, self.sequence, self.planner.alternatives)
                    if choice is not None:
                        selected = self.planner.alternatives[choice]
                        action = selected["action"]
                        self.pending = selected["pending"]
                        self.decision_source = "model-v4"
                        self.last_trace["model_action_candidate"] = choice
        self.decisions += 1
        self.decision_seconds += time.perf_counter() - started
        return action

    def _read_log(self, messages: list[dict]) -> None:
        """Read the duty log in request texts; a failure only switches the reader off."""
        if self.logbook.reader is None and not self.logbook.rules:
            return
        try:
            self.logbook.collect(messages)
            self.logbook.process(self.sequence)
            model = self.quality.terrain_model
            if model is not None:
                margin = float(self.logbook.settings.get("terrain_margin_deg", 1.0))
                for direction, altitude in self.logbook.terrain.items():
                    if self.logbook.applied_terrain.get(direction) == altitude:
                        continue
                    self.logbook.applied_terrain[direction] = altitude
                    if model.pin(direction, altitude + margin):
                        self.emit("logbook_terrain", {"direction": direction, "top": altitude + margin})
        except Exception as error:  # noqa: BLE001 - the log is optional evidence
            self.logbook.reader = None
            self.logbook.rules = False
            self.emit("logbook_disabled", {"error_type": type(error).__name__, "detail": str(error)[:200]})

    def _quiet_end(self, now: float) -> float | None:
        """End of the announced quiet window that holds now (or the next appointment in it)."""
        ends = [high for low, high in self.logbook.quiet if low <= now < high]
        if not ends:
            return None
        night = self.task.current_night(now)
        end = max(ends)
        if night is None or end >= self.task.nights[night].end:
            return None  # a stop past the night's end: the night handling waits it out
        appointments = [item["start"] for item in self.logbook.faults if item["state"] == "open" and now < item["start"] < end]
        return min([end, *appointments])

    def _last_correct_report(self) -> float | None:
        times = [entry["time"] for entry in self.instrument.reports if entry.get("resolved") == "correct"]
        return max(times) if times else None

    def _level_error_summary(self) -> dict:
        errors = np.array([error for error, _ in self.level_errors], dtype=float)
        if not len(errors):
            return {"count": 0}
        return {"count": len(errors), "mean": round(float(np.mean(errors)), 4),
                "sd": round(float(np.std(errors)), 4),
                "p05": round(float(np.percentile(errors, 5)), 4), "p95": round(float(np.percentile(errors, 95)), 4)}

    def _guarded(self, function, *args):
        """Run an optional evidence model; a failure falls back to the frozen behaviour."""
        if not self.instrument_enabled:
            return None
        try:
            return function(*args)
        except Exception as error:  # noqa: BLE001 - optional models must not stop the survey
            self.instrument_enabled = False
            self.fault_mode = "legacy"
            self.quality.monitor = None
            self.emit("instrument_disabled", {"error_type": type(error).__name__, "detail": str(error)[:200]})
            return None

    def _instrument_record(self, result: dict, bulletins: list[dict]) -> None:
        pending = self.pending
        start = pending["start"]
        hits = {row["target_id"]: float(row["score"]) for row in result.get("hits", [])}
        signature = pending.get("signature", frozenset())
        mixed = False
        directions = set()
        stable = set(pending.get("sectors", ()))
        for bulletin in bulletins:
            try:
                issued = parse_utc(bulletin["issued_at_utc"]).timestamp()
            except (KeyError, TypeError, ValueError):
                continue
            if start < issued < start + pending["duration"]:
                kinds = frozenset(n.get("event_kind") for n in bulletin.get("notices", []) if n.get("direction") == "ALL")
                if kinds != signature:
                    mixed = True
                directions.update(n.get("direction") for n in bulletin.get("notices", [])
                                  if n.get("direction") != "ALL" and n.get("event_kind") != "terrain_obstruction")
                stable &= {(n.get("event_kind"), n.get("direction")) for n in bulletin.get("notices", [])}
        reference = self.instrument.reference_level(pending["night"], signature, start)
        record = build_record(self.task, pending, hits, signature, self.revision >= 2, reference, mixed=mixed)
        self.instrument.add(record)
        if self.config["observability"].get("level_trace", False) and record["level"] is not None:
            # Research runs: the monitor's evidence per exposure, for calibrating its rules.
            self.emit("level", {"start": round(start), "duration": pending["duration"], "night": pending["night"],
                                "level": round(record["level"], 4), "eff": round(self.instrument.log_eff(record), 4),
                                "program": pending["program"], "samples": record["samples"], "mixed": mixed,
                                "signature": sorted(signature), "directions": sorted(d for d in directions if d)})
        try:
            self.quality.terrain_evidence(record, pending, self.task, directions)
        except Exception as error:  # noqa: BLE001 - fall back to the announced-sector envelope
            self.quality.terrain_model = None
            self.emit("terrain_disabled", {"error_type": type(error).__name__, "detail": str(error)[:200]})
        try:
            self.quality.sector_evidence(record, pending, self.task, stable)
        except Exception as error:  # noqa: BLE001 - fall back to the sector prior
            self.quality.sector_tracker = None
            self.emit("sectors_disabled", {"error_type": type(error).__name__, "detail": str(error)[:200]})
        if pending.get("predicted_level") is not None and record["level"] is not None:
            self.level_errors.append((round(record["level"] - pending["predicted_level"][0], 4),
                                      pending["predicted_level"][1]))

    def wait_for_next_night(self, now: float) -> dict:
        next_start = self.task.next_night(now)
        until = next_start if next_start is not None else self.task.end
        if until <= now:
            return {"action": "finish"}
        return {"action": "wait", "until_utc": format_utc(datetime.fromtimestamp(until, timezone.utc))}

    def wait_for_bulletin(self, now: float, night: int) -> dict:
        remaining = self.task.nights[night].end - now
        if remaining < self.task.minimum_exposure:
            return self.wait_for_next_night(now)
        slot = self.task.slot_seconds
        offset = (now - self.task.nights[night].start) % slot
        seconds = max(self.task.minimum_exposure, int(slot - offset))
        boundary = self.logbook.next_boundary(now)
        if boundary is not None and now + self.task.minimum_exposure <= boundary < now + seconds:
            seconds = int(boundary - now)  # wake for the announced moment, not after it
        return {"action": "wait", "duration_seconds": min(self.task.maximum_exposure, seconds)}

    def summary(self):
        return {
            "card": self.task.card,
            "best_score_sum_feedback": round(float(self.state.best_score.sum()), 6),
            "required_missing_conservative": self.state.required_missing(),
            "resyncs": self.state.resyncs,
            "feedback_contributions": len(self.state.history),
            "pending_result_not_delivered": self.pending is not None,
            "policy_seconds": round(self.decision_seconds, 6),
            "pacing": self.planner.pacing_status(),
            "structure": self.planner.structure,
            "quality": self.quality.status(),
            "pointing": self.pointing.status(),
            "instrument": self.instrument.status(),
            "level_errors": self._level_error_summary(),
            "model": self.guidance.status(),
            "logbook": self.logbook.status(),
        }
