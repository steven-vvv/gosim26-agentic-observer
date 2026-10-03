"""Optional model decisions over already feasible weekly plans and observations."""

from __future__ import annotations

import math
import os

import numpy as np

from .planning import WEEKLY_STRATEGIES, strategy_settings
from .services import load_client


class ModelGuidance:
    def __init__(self, task, state, quality, pointing, config: dict, emit):
        self.task, self.state, self.quality, self.pointing = task, state, quality, pointing
        self.settings = config["model"]
        self.policy = config["policy"]
        self.emit = emit
        self.advisor = None
        self.last_forecast = None
        self.action_nights = set()
        self.weekly_plan = {"strategy": "steady", "source": "deterministic"}
        if not self.settings["enabled"]:
            return
        client = load_client("model_advisor")
        if client is None:
            self.emit("model_unavailable", {"reason": "client_missing"})
            return
        url = os.environ.get("OBSERVER_MODEL_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        key = os.environ.get("OBSERVER_MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not url or not key:
            self.emit("model_unavailable", {"reason": "endpoint_or_key_missing"})
            return
        options = client.AdvisorConfig(
            enabled=True,
            model=os.environ.get("OBSERVER_MODEL_NAME") or self.settings["model"],
            timeout=self.settings["timeout_seconds"],
            max_total_calls=self.settings["max_total_calls"],
            max_stage_calls=self.settings["max_stage_calls"],
            max_consecutive_failures=self.settings["max_consecutive_failures"],
            max_tokens=self.settings["max_tokens"],
            min_alternative_probability=self.settings["min_alternative_probability"],
        )
        self.advisor = client.ModelAdvisor(
            url, key, config=options, on_event=lambda event: self.emit("model_request", event)
        )

    def available(self, remaining: float) -> bool:
        return bool(
            self.advisor is not None
            and self.advisor.enabled
            and remaining >= self.settings["minimum_budget_seconds"] + self.settings["timeout_seconds"]
        )

    def context(self, snapshot: dict, night: int, remaining: float, sequence: int) -> dict:
        task = self.task
        threshold = float(task.scoring["required"]["observed_factor_threshold"])
        completed = int(np.count_nonzero(self.state.factor_lower >= threshold))
        last = snapshot.get("last_result") or {}
        forecast = snapshot.get("latest_forecast") or {}
        bulletin = snapshot.get("latest_bulletin") or {}
        return {
            "schema": "gosim26.v4.advice-context.v1",
            "public_contract": {
                "site": {"latitude_deg": task.latitude, "longitude_deg": task.longitude,
                         "minimum_altitude_deg": task.minimum_altitude},
                "scoring": task.scoring,
                "survey": {"nights": len(task.nights), "start_utc": task.start, "end_utc": task.end,
                           "slot_seconds": task.slot_seconds},
                "instrument": {"fibers": task.grid.n, "minimum_exposure": task.minimum_exposure,
                               "maximum_exposure": task.maximum_exposure},
            },
            "current_state": {
                "now_utc": snapshot.get("now_utc"),
                "night_index": night,
                "nights_remaining": len(task.nights) - night,
                "wallclock_remaining_seconds": round(remaining, 3),
                "running_total": snapshot.get("running_total"),
                "catalog_targets": len(task.ids),
                "targets_above_required_factor": completed,
                "required_total": int(np.count_nonzero(task.required)),
                "required_missing_conservative": self.state.required_missing(),
                "quality": self.quality.status(),
                "pointing": self.pointing.status(),
                "last_result": {"action": last.get("action"), "observe_index": last.get("observe_index"),
                                "hit_count": len(last.get("hits") or [])},
                "latest_bulletin": {"issued_at_utc": bulletin.get("issued_at_utc"),
                                    "notices": bulletin.get("notices") or []},
                "latest_forecast": {"issued_at_utc": forecast.get("issued_at_utc"),
                                    "coverage_start_utc": forecast.get("coverage_start_utc"),
                                    "coverage_end_utc": forecast.get("coverage_end_utc"),
                                    "notices": (forecast.get("notices") or [])[:64]},
                "active_requests": snapshot.get("active_requests") or [],
            },
            "decision_sequence": sequence,
        }

    def weekly(self, snapshot: dict, night: int, remaining: float, sequence: int) -> str | None:
        """Task planning and plan adaptation: the model picks the strategy for the coming week.

        Called at every new weekly forecast. The model reads the forecast notices, the
        required-target deficit, the active requests and the remaining nights, and chooses
        one of the planner strategies; its choice sets the planner weights until the next
        forecast. Without a valid answer the tuned season policy (``steady``) stays.
        """
        forecast = snapshot.get("latest_forecast") or {}
        issued = forecast.get("issued_at_utc")
        if not issued or issued == self.last_forecast:
            return None
        self.last_forecast = issued
        streak = getattr(self.advisor, "consecutive_failures", 0)
        limit = getattr(getattr(self.advisor, "config", None), "max_consecutive_failures", None)
        if limit is not None and streak >= limit:
            # A failure streak pauses the model; each new weekly forecast gives it one more try,
            # and a valid answer resumes both stages.
            self.advisor.consecutive_failures = limit - 1
        remaining_fraction = (len(self.task.nights) - night) / len(self.task.nights)
        missing_fraction = self.state.required_missing() / max(1, int(np.count_nonzero(self.task.required)))
        # Without a model answer the tuned season policy stays in force.
        deterministic = "steady"
        choice = None
        if self.available(remaining):
            descriptions = {
                "steady": "keep the tuned season policy; it already raises required targets as their nights run out",
                "required_push": "raise the required-target completion bonus by 30% and plan them to a safer factor; "
                                 "it costs science, so use it only when required_deficit_alarm is true (at the season "
                                 "start every required target is still missing, which is normal)",
                "science_push": "lower the required-target bonus by 25% to favour science value; use when required "
                                "targets are on track and the coming week is clear",
            }
            options = []
            for strategy in WEEKLY_STRATEGIES:
                settings = strategy_settings(self.policy, strategy)
                options.append({"id": strategy, "effect": descriptions[strategy],
                                "required_bonus": round(settings["required_bonus"], 3),
                                "required_safe_factor": settings["required_safe_factor"]})
            context = self.context(snapshot, night, remaining, sequence)
            context.update(
                {
                    "deterministic_choice": deterministic,
                    "eligible_choices": list(WEEKLY_STRATEGIES),
                    "required_deficit_alarm": bool(missing_fraction > min(1.0, 1.2 * remaining_fraction)),
                    "missing_required_fraction": round(missing_fraction, 6),
                    "remaining_night_fraction": round(remaining_fraction, 6),
                }
            )
            choice = self.advisor.choose("weekly_planning", options, context)
        selected = choice if choice in WEEKLY_STRATEGIES else deterministic
        self.weekly_plan = {"strategy": selected, "source": "model" if choice in WEEKLY_STRATEGIES else "deterministic",
                            "forecast_issued_at_utc": issued, "night_index": night}
        self.emit("weekly_plan", self.weekly_plan)
        return selected

    def action(self, snapshot: dict, night: int, remaining: float, sequence: int, alternatives: list[dict]) -> int | None:
        """Action decision: the model picks the observation to execute between the two best.

        At most once per night, when the planner's two best observations are within the
        merit tolerance, the model compares their pointings, programs, durations and
        predicted targets (required flags, science weights, prior best scores) against the
        live state and its choice is executed. Without a valid answer the planner's best
        observation stays.
        """
        if night in self.action_nights or len(alternatives) < 2 or not self.available(remaining):
            return None
        best, second = alternatives[:2]
        gap = max(0.0, (best["merit"] - second["merit"]) / max(best["merit"], 1e-9))
        if not math.isfinite(gap) or gap > self.settings["action_merit_tolerance"]:
            return None
        self.action_nights.add(night)
        options = []
        for index, candidate in enumerate((best, second)):
            targets = [
                {"target_id": self.task.ids[row["index"]], "required": bool(self.task.required[row["index"]]),
                 "science_weight": float(self.task.weight[row["index"]]),
                 "prior_best_score": float(self.state.best_score[row["index"]]),
                 "predicted_factor": row["predicted_factor"]}
                for row in candidate["pending"]["targets"]
            ]
            options.append({"id": f"C{index}", "action": candidate["action"], "predicted_targets": targets,
                            "estimated_merit_per_second": round(candidate["merit"], 8)})
        context = self.context(snapshot, night, remaining, sequence)
        context.update({"deterministic_choice": "C0", "eligible_choices": ["C0", "C1"],
                        "relative_merit_gap": round(gap, 8)})
        choice = self.advisor.choose("action_decision", options, context)
        if choice not in {"C0", "C1"}:
            return None
        return int(choice[1])

    def status(self):
        return {"weekly_plan": self.weekly_plan,
                "advisor": self.advisor.status() if self.advisor is not None else {"configured": False}}
