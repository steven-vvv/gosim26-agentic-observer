"""Public-state lifecycle and safe survey actions around the numerical planner."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .advice import ModelGuidance
from .domain import Task
from .geometry import format_utc, parse_utc
from .planning import Planner
from .pointing import PointingModel
from .quality import QualityModel
from .state import SurveyState


class Observer:
    def __init__(self, publication: dict, config: dict, emit=None):
        self.config = config
        self.emit = emit or (lambda *_args, **_kwargs: None)
        self.task = Task(publication, config["policy"]["altitude_margin_deg"])
        self.state = SurveyState(self.task)
        self.quality = QualityModel(self.task, config)
        self.pointing = PointingModel(self.task)
        self.planner = Planner(self.task, self.state, self.quality, self.pointing, config)
        self.guidance = ModelGuidance(self.task, self.state, self.quality, self.pointing, config, self.emit)
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
                "wallclock_seconds": self.task.wallclock,
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
        if self.pending and result and result.get("action") == "observe":
            samples = self.state.feedback(result, self.pending)
            self.pointing.feedback(self.pending, result)
            self.quality.update(samples, self.pending, now, self.pending["night"])
        self.pending = None
        self.quality.messages(snapshot, now)
        for message in snapshot.get("new_messages", []):
            if message.get("record_type") == "state_resync":
                self.emit("state_resync", self.state.resync(message))
        if night is None:
            action = self.wait_for_next_night(now)
        elif self.quality.closed():
            action = self.wait_for_bulletin(now, night)
        else:
            budget = float(snapshot.get("wallclock", {}).get("remaining_seconds", self.task.wallclock))
            if night != self.last_night:
                self.last_night = night
                strategy = (self.guidance.weekly_plan["strategy"]
                            if self.guidance.weekly_plan["source"] == "model"
                            else self.guidance.deterministic_strategy(night))
                self.planner.set_strategy(strategy, model_selected=self.guidance.weekly_plan["source"] == "model")
                self.emit(
                    "night_plan",
                    {
                        "night": night,
                        "strategy": strategy,
                        "required_missing_conservative": self.state.required_missing(),
                        "quality": self.quality.status(),
                        "pointing": self.pointing.status(),
                    },
                )
            strategy = self.guidance.weekly(snapshot, night, budget, self.sequence)
            if strategy is not None:
                self.planner.set_strategy(strategy, model_selected=self.guidance.weekly_plan["source"] == "model")
            if self.guidance.weekly_plan["source"] == "model":
                self.decision_source = "model-v4"
            evidence = self.quality.report_evidence(now)
            if evidence:
                self.emit("fault_report", {"now_utc": snapshot["now_utc"], **evidence})
                self.quality.reported(now)
                action = {"action": "report"}
            elif self.quality.zero_observes >= 2:
                self.quality.zero_observes = 0
                action = self.wait_for_bulletin(now, night)
            else:
                action = self.planner.plan(now, night, budget)
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
            "quality": self.quality.status(),
            "pointing": self.pointing.status(),
            "model": self.guidance.status(),
        }
