"""Optional model decisions over already feasible weekly plans and observations."""

from __future__ import annotations

import math
import os

import numpy as np

from .planning import strategy_settings
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
        self.weekly_plan = {"strategy": "balanced", "source": "deterministic"}
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

    def deterministic_strategy(self, night: int) -> str:
        """Choose the public-state fallback at the start of each observing night."""
        if self.policy["required_bonus"] >= 120.0 and self.policy["required_safe_factor"] <= 0.55:
            return "coverage_focus"
        remaining_fraction = (len(self.task.nights) - night) / len(self.task.nights)
        missing_fraction = self.state.required_missing() / max(1, int(np.count_nonzero(self.task.required)))
        return "coverage_focus" if missing_fraction > min(1.0, 1.2 * remaining_fraction) else "balanced"

    def weekly(self, snapshot: dict, night: int, remaining: float, sequence: int) -> str | None:
        forecast = snapshot.get("latest_forecast") or {}
        issued = forecast.get("issued_at_utc")
        if not issued or issued == self.last_forecast:
            return None
        self.last_forecast = issued
        remaining_fraction = (len(self.task.nights) - night) / len(self.task.nights)
        missing_fraction = self.state.required_missing() / max(1, int(np.count_nonzero(self.task.required)))
        deterministic = self.deterministic_strategy(night)
        choice = None
        if self.available(remaining):
            options = []
            for strategy in ("balanced", "coverage_focus"):
                settings = strategy_settings(self.policy, strategy, model_selected=True)
                options.append({"id": strategy, "required_bonus": settings["required_bonus"],
                                "required_safe_factor": settings["required_safe_factor"]})
            if (options[0]["required_bonus"], options[0]["required_safe_factor"]) != (
                options[1]["required_bonus"], options[1]["required_safe_factor"]
            ):
                context = self.context(snapshot, night, remaining, sequence)
                context.update(
                    {
                        "deterministic_choice": deterministic,
                        "eligible_choices": ["balanced", "coverage_focus"],
                        "planning_rule": "coverage_focus for an already strong base policy; otherwise when required deficit exceeds 1.2 times remaining night fraction",
                        "missing_required_fraction": round(missing_fraction, 6),
                        "remaining_night_fraction": round(remaining_fraction, 6),
                        "choice_prior_basis": "heuristic preference, not calibrated win probabilities",
                    }
                )
                options[0]["prior_probability"] = 0.98 if deterministic == "balanced" else 0.02
                options[1]["prior_probability"] = 1 - options[0]["prior_probability"]
                choice = self.advisor.choose("weekly_planning", options, context)
        selected = choice or deterministic
        self.weekly_plan = {"strategy": selected, "source": "model" if choice else "deterministic",
                            "forecast_issued_at_utc": issued, "night_index": night}
        self.emit("weekly_plan", self.weekly_plan)
        return selected

    def action(self, snapshot: dict, night: int, remaining: float, sequence: int, alternatives: list[dict]) -> int | None:
        if night in self.action_nights or len(alternatives) < 2 or not self.available(remaining):
            return None
        best, second = alternatives[:2]
        gap = max(0.0, (best["merit"] - second["merit"]) / max(best["merit"], 1e-9))
        eligible = gap <= self.settings["action_merit_tolerance"]
        if not eligible:
            return None
        self.action_nights.add(night)
        preferred = min(0.995, 0.95 + 0.045 * min(1.0, gap / max(1e-9, self.settings["action_merit_tolerance"])))
        options = []
        for index, candidate in enumerate((best, second)):
            action = candidate["action"]
            targets = [
                {"target_id": self.task.ids[row["index"]], "required": bool(self.task.required[row["index"]]),
                 "science_weight": float(self.task.weight[row["index"]]),
                 "prior_best_score": float(self.state.best_score[row["index"]]),
                 "predicted_factor": row["predicted_factor"]}
                for row in candidate["pending"]["targets"]
            ]
            options.append(
                {"id": f"C{index}", "action": action, "predicted_targets": targets,
                 "estimated_merit_per_second": round(candidate["merit"], 8),
                 "prior_probability": round(preferred if index == 0 else 1 - preferred, 4),
                 "eligible": index == 0 or eligible}
            )
        context = self.context(snapshot, night, remaining, sequence)
        context.update(
            {"deterministic_choice": "C0", "eligible_choices": ["C0", "C1"] if eligible else ["C0"],
             "relative_merit_gap": round(gap, 8),
             "choice_prior_basis": "relative deterministic merit, not calibrated win probabilities"}
        )
        choice = self.advisor.choose("action_decision", options, context)
        if choice not in {"C0", "C1"}:
            return None
        index = int(choice[1])
        if index == 1 and (not eligible or not math.isfinite(gap)):
            return None
        return index

    def status(self):
        return {"weekly_plan": self.weekly_plan,
                "advisor": self.advisor.status() if self.advisor is not None else {"configured": False}}
