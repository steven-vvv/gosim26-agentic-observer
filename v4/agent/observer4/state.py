"""Public feedback ledger, including exact action-window invalidation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Contribution:
    action_index: int
    target_index: int
    score: float
    factor_lower: float
    factor_estimate: float
    valid: bool = True


class SurveyState:
    def __init__(self, task):
        self.task = task
        self.best_score = np.zeros(len(task.ids))
        self.factor_lower = np.zeros(len(task.ids))
        self.factor_estimate = np.zeros(len(task.ids))
        self.attempts = np.zeros(len(task.ids), dtype=np.int32)
        self.misses = np.zeros(len(task.ids), dtype=np.int32)
        self.history: list[Contribution] = []
        self.last_feedback = -1
        self.resyncs = 0

    def feedback(self, result: dict | None, pending: dict | None) -> list[dict]:
        if not result or result.get("action") != "observe" or not pending:
            return []
        index = int(result["observe_index"])
        if index <= self.last_feedback:
            return []
        self.last_feedback = index
        hits = {row["target_id"]: float(row["score"]) for row in result.get("hits", [])}
        multiplier = float(self.task.scoring["program"]["multipliers"][pending["program"]])
        mismatch = float(self.task.scoring["program"]["mismatch_multiplier"])
        samples = []
        for data in pending["targets"]:
            i = data["index"]
            self.attempts[i] += 1
            value = hits.get(self.task.ids[i])
            if value is None:
                self.misses[i] += 1
                continue
            if not np.isfinite(value) or value < 0:
                raise ValueError("invalid feedback score")
            normalized = value / self.task.weight[i]
            lower = float(np.clip((value - 1e-6) / (self.task.weight[i] * max(multiplier, mismatch)), 0, 1))
            matched = abs(normalized - multiplier) < 1e-4
            saturated_mismatch = abs(normalized - mismatch) < 1e-4
            assumed_multiplier = (
                multiplier if matched or (not saturated_mismatch and data["matched_probability"] >= 0.7) else mismatch
            )
            estimate = float(np.clip(normalized / assumed_multiplier, lower, 1))
            contribution = Contribution(index, i, value, lower, estimate)
            self.history.append(contribution)
            self._apply(contribution)
            samples.append(
                {
                    **data,
                    "score": value,
                    "normalized": normalized,
                    "factor_lower": lower,
                    "factor_estimate": estimate,
                    "matched": matched,
                    "saturated_mismatch": saturated_mismatch,
                }
            )
        return samples

    def _apply(self, contribution: Contribution) -> None:
        i = contribution.target_index
        self.best_score[i] = max(self.best_score[i], contribution.score)
        self.factor_lower[i] = max(self.factor_lower[i], contribution.factor_lower)
        self.factor_estimate[i] = max(self.factor_estimate[i], contribution.factor_estimate)

    def resync(self, message: dict) -> dict:
        window = message["invalidated_window"]
        first, end = int(window["action_index_start"]), int(window["action_index_end_exclusive"])
        if not 0 <= first <= end <= int(window["action_count_at_trigger"]):
            raise ValueError("invalid state_resync window")
        invalidated = 0
        self.best_score.fill(0)
        self.factor_lower.fill(0)
        self.factor_estimate.fill(0)
        for item in self.history:
            if first <= item.action_index < end and item.valid:
                item.valid = False
                invalidated += 1
            if item.valid:
                self._apply(item)
        public = {row["target_id"]: float(row["best_score"]) for row in message.get("best_scores", [])}
        disagreements = 0
        top_multiplier = max(float(x) for x in self.task.scoring["program"]["multipliers"].values())
        for i, target_id in enumerate(self.task.ids):
            score = public.get(target_id, 0.0)
            if abs(self.best_score[i] - score) > 2e-6:
                disagreements += 1
                self.best_score[i] = score
                self.factor_lower[i] = max(0, min(1, (score - 1e-6) / (self.task.weight[i] * top_multiplier)))
                self.factor_estimate[i] = self.factor_lower[i]
        self.resyncs += 1
        return {
            "invalidated_contributions": invalidated,
            "score_disagreements": disagreements,
            "valid_targets": int(np.count_nonzero(self.best_score)),
            "resync_count": self.resyncs,
        }

    def required_missing(self) -> int:
        threshold = float(self.task.scoring["required"]["observed_factor_threshold"])
        return int(np.count_nonzero(self.task.required & (self.factor_lower < threshold)))

    def uniformity_gain(self):
        threshold = float(self.task.scoring["uniformity"]["observed_factor_threshold"])
        counts = np.bincount(self.task.band_labels[self.factor_lower >= threshold], minlength=len(self.task.bands))
        ratios = counts / np.maximum(1, self.task.band_total)
        squares, total = float(ratios @ ratios), float(np.sum(ratios))
        if not len(ratios):
            return np.zeros(len(self.task.ids))
        old = 0 if squares == 0 else total * total / (len(ratios) * squares)
        delta = 1.0 / np.maximum(1, self.task.band_total)
        new = (total + delta) ** 2 / (len(ratios) * np.maximum(1e-12, squares + 2 * ratios * delta + delta * delta))
        return (float(self.task.scoring["uniformity"]["weight"]) * (new - old))[self.task.band_labels]
