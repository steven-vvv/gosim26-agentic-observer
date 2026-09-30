"""Joint field, fiber, exposure and program search under public online beliefs."""

from __future__ import annotations

import math

import numpy as np

from .geometry import (
    RAD,
    SIDEREAL_RATE,
    airmass,
    altaz,
    horizontal_vectors,
    lunar_factors,
    max_hour_angle,
    sidereal,
    to_equatorial,
    wrap180,
)

PROGRAMS = ("DARK", "BRIGHT", "BACKUP")


def strategy_settings(policy: dict, strategy: str, *, model_selected: bool = False) -> dict:
    settings = dict(policy)
    if strategy == "coverage_focus":
        settings["required_bonus"] = max(120.0, settings["required_bonus"])
        settings["required_safe_factor"] = min(0.55, settings["required_safe_factor"])
    elif strategy == "balanced":
        if model_selected:
            settings["required_bonus"] = min(120.0, settings["required_bonus"])
            settings["required_safe_factor"] = max(0.55, settings["required_safe_factor"])
    else:
        raise ValueError("unknown weekly strategy")
    return settings


def fiber_totals(fibers: np.ndarray, gains: np.ndarray, weight: np.ndarray, count: int) -> np.ndarray:
    """Sum the best target per fiber for each candidate center and duration."""
    centers, targets = fibers.shape
    labels = np.arange(centers)[:, None] * (count + 1) + np.where(fibers >= 0, fibers, count)
    order = np.argsort(labels.ravel(), kind="stable")
    sorted_labels = labels.ravel()[order]
    starts = np.r_[0, np.flatnonzero(np.diff(sorted_labels)) + 1]
    values = (gains.T[None, :, :] * weight[:, :, None]).reshape(-1, len(gains))[order]
    maxima = np.maximum.reduceat(values, starts, axis=0)
    sizes = np.diff(np.r_[starts, len(sorted_labels)])
    # The old search included zero for every target outside a fiber.
    maxima = np.where(sizes[:, None] < targets, np.maximum(maxima, 0), maxima)
    totals = np.zeros((centers * (count + 1), len(gains)))
    totals[sorted_labels[starts]] = maxima
    totals = totals.reshape(centers, count + 1, len(gains))[:, :count]
    return np.add.accumulate(totals, axis=1)[:, -1, :]


def expected_science_gains(factors: np.ndarray, weights: np.ndarray, best: np.ndarray,
                           probabilities: np.ndarray, multipliers: np.ndarray, mismatch: float) -> np.ndarray:
    """Expected improvement of the public best score for each declared program."""
    score = factors * weights[None, :]
    baseline = np.maximum(0, score * mismatch - best[None, :])
    matching = np.maximum(0, score[None, :, :] * multipliers[:, None, None] - best[None, None, :])
    return probabilities[:, None, :] * matching + (1 - probabilities[:, None, :]) * baseline[None, :, :]


def scarcity_multipliers(visibility: np.ndarray, required: np.ndarray, minimum: float, weight: float) -> np.ndarray:
    """Weight required targets with fewer remaining public observing hours."""
    future = np.cumsum(visibility[::-1], axis=0)[::-1]
    pressure = np.ones_like(future)
    for night, hours in enumerate(future):
        available = required & (hours >= minimum)
        if np.any(available):
            typical = float(np.median(hours[available]))
            pressure[night] += weight * (np.clip(typical / np.maximum(hours, minimum), 1.0, 3.0) - 1.0)
    return pressure


class Planner:
    def __init__(self, task, state, quality, pointing, config: dict):
        self.task, self.state, self.quality, self.pointing = task, state, quality, pointing
        self.config = config
        self.settings = config["policy"]
        task.prepare_neighbors(self.settings["neighbor_radius_deg"])
        weight = self.settings.get("scarcity_weight", 0)
        self.scarcity = (scarcity_multipliers(task.visibility, task.required, task.minimum_exposure, weight)
                         if weight else None)
        self.last_trace = {}
        self.pending = None
        self.alternatives = []

    def set_strategy(self, strategy: str, *, model_selected: bool = False) -> None:
        self.settings = strategy_settings(self.config["policy"], strategy, model_selected=model_selected)

    def plan(self, now: float, night_index: int, remaining_wallclock: float = 900.0) -> dict | None:
        self.alternatives = []
        task, state, settings = self.task, self.state, self.settings
        night = task.nights[night_index]
        left = min(night.end, task.end) - now
        if left < task.minimum_exposure:
            return None
        lst = sidereal(now, task.longitude)
        ha = wrap180(lst - task.ra)
        setting_seconds = np.where(task.hmax >= 180, 1e9, (task.hmax - ha) / SIDEREAL_RATE)
        visible = (ha >= -task.hmax) & (setting_seconds >= task.minimum_exposure) & (task.hmax > 0)
        vectors = horizontal_vectors(task.ra, task.dec, lst, task.latitude)
        altitude, azimuth = altaz(vectors)
        directional, clean = self.quality.direction_factors(altitude, azimuth)
        base_quality = lunar_factors(
            task.ra, task.dec, now + 450.0, task.latitude, task.longitude, task.scoring.get("lunar_model")
        ) / (task.scoring["q0"] * airmass(altitude) ** task.scoring["airmass_exponent"])
        scale = self.quality.scale(now)
        rate = task.flux * base_quality * scale * directional * settings["quality_safety"]
        rate /= task.scoring["flux_zero_point"] * task.scoring["exposure_zero_point_seconds"]
        reach = np.minimum(1, rate * np.minimum(np.minimum(task.maximum_exposure, setting_seconds), left))
        probabilities = self.quality.program_probabilities(base_quality)
        mult = np.array([task.scoring["program"]["multipliers"][name] for name in PROGRAMS])
        mismatch = task.scoring["program"]["mismatch_multiplier"]
        expected_mult = mismatch + (mult[:, None] - mismatch) * probabilities
        threshold = float(task.scoring["required"]["observed_factor_threshold"])
        incomplete = task.required & (state.factor_lower < threshold)
        urgency = 1.0 + settings["deadline_weight"] / np.maximum(1, task.last_night - night_index + 1)
        setting_boost = 1.0 + 0.35 * np.maximum(0, ha / np.maximum(1, task.hmax))
        if settings.get("score_marginal", False):
            science = np.max(expected_science_gains(reach[None, :], task.weight, state.best_score,
                                                    probabilities, mult, mismatch)[:, 0, :], axis=0)
        else:
            science = task.weight * np.maximum(
                0, reach ** settings["science_exponent"] - state.factor_estimate ** settings["science_exponent"]
            )
        required_bonus = settings["required_bonus"] if self.scarcity is None else (
            settings["required_bonus"] * self.scarcity[night_index]
        )
        required = incomplete * (reach >= settings["required_safe_factor"]) * required_bonus
        priority = (science + required) * urgency * setting_boost * directional
        priority *= np.maximum(0.25, 0.80 ** np.minimum(state.misses, 6))
        priority = np.where(visible & (task.last_night >= night_index), priority, 0)
        pool = np.flatnonzero(priority > 1e-8)
        if not len(pool):
            self.last_trace = {"reason": "no_positive_visible_target"}
            return None
        limit = min(len(pool), settings["anchor_pool"])
        pool = pool[np.argpartition(priority[pool], -limit)[-limit:]]
        pool = pool[np.lexsort((pool, -priority[pool]))]
        # Budget pacing uses measured time in the runtime; this is a predictable fallback.
        count = settings["anchors"]
        if remaining_wallclock < settings["budget_reserve_seconds"] + 15:
            count = min(count, 2)
        uniformity = state.uniformity_gain() * settings["uniformity_weight"]
        finalists = []
        used = []
        fields_checked = 0
        for anchor in pool:
            if len(used) >= count:
                break
            # Spread the search over distinct fields rather than repeatedly testing one cluster.
            if any(float(task.vectors[anchor] @ task.vectors[j]) > math.cos(0.6 * RAD) for j in used):
                continue
            used.append(int(anchor))
            near = task.neighbors(int(anchor))
            near = near[visible[near] & (directional[near] > 0)]
            if not len(near):
                continue
            ca, cz = task.grid.centers_for_target(float(altitude[anchor]), float(azimuth[anchor]))
            keep = (ca >= task.minimum_altitude + 0.4) & (ca <= 89.85)
            ca, cz = ca[keep], cz[keep]
            if not len(ca):
                continue
            if settings["refine_centers"]:
                ca = np.concatenate((ca, ca + 0.12, ca - 0.12))
                cz = np.tile(cz, 3)
            fibers, margin, _, _ = task.grid.classify(vectors[near], ca, cz)
            fields_checked += len(ca)
            geometry_weight = np.where(fibers >= 0, 1.0, 0.0)
            geometry_weight *= np.where(margin >= settings["glass_margin_deg"], 1.0, 0.45)
            durations = self._durations(rate[near], incomplete[near], left)
            if not len(durations):
                continue
            factors = np.minimum(1, durations[:, None] * rate[near])
            if settings.get("score_marginal", False):
                science_gain = expected_science_gains(factors, task.weight[near], state.best_score[near],
                                                       probabilities[:, near], mult, mismatch)
            else:
                science_gain = task.weight[near] * np.maximum(
                    0, factors ** settings["science_exponent"] - state.factor_estimate[near] ** settings["science_exponent"]
                )
            near_bonus = required_bonus if self.scarcity is None else required_bonus[near]
            req_gain = incomplete[near][None, :] * (factors >= settings["required_safe_factor"]) * near_bonus
            fairness = (
                (state.factor_lower[near][None, :] < threshold) & (factors >= settings["required_safe_factor"])
            ) * uniformity[near]
            gains = (science_gain + req_gain + fairness) * urgency[near] * setting_boost[near]
            gains = np.where(durations[:, None] <= setting_seconds[near][None, :], gains, 0)
            if not np.any(gains > 0):
                continue
            if settings.get("score_marginal", False):
                total = fiber_totals(fibers, gains.reshape(-1, len(near)), geometry_weight, task.grid.n)
                total = total.reshape(len(ca), len(PROGRAMS), len(durations))
            else:
                total = fiber_totals(fibers, gains, geometry_weight, task.grid.n)
            center_limit = self._centers_up(ca, cz, lst)
            if settings.get("score_marginal", False):
                total = np.where(durations[None, None, :] <= center_limit[:, None, None], total, 0)
                merit = total / durations[None, None, :]
            else:
                total = np.where(durations[None, :] <= center_limit[:, None], total, 0)
                merit = total / durations[None, :]
            flat = int(np.argmax(merit))
            if settings.get("score_marginal", False):
                center_i, program_i, duration_i = np.unravel_index(flat, merit.shape)
                value = float(merit[center_i, program_i, duration_i])
            else:
                center_i, duration_i = np.unravel_index(flat, merit.shape)
                program_i = None
                value = float(merit[center_i, duration_i])
            if value <= 0 or (len(finalists) >= 6 and value <= finalists[-1]["merit"]):
                continue
            selected = []
            gain = gains[program_i, duration_i] * geometry_weight[center_i] if program_i is not None else gains[duration_i] * geometry_weight[center_i]
            for fiber in range(task.grid.n):
                ids = np.flatnonzero((fibers[center_i] == fiber) & (gain > 0))
                if len(ids):
                    selected.append((fiber, int(near[ids[int(np.argmax(gain[ids]))]])))
            candidate = {
                "merit": value,
                "alt": float(ca[center_i]),
                "az": float(cz[center_i]),
                "duration": int(durations[duration_i]),
                "selected": selected,
                "program_i": program_i,
            }
            finalists.append(candidate)
            finalists.sort(key=lambda entry: entry["merit"], reverse=True)
            del finalists[6:]
        if not finalists:
            self.last_trace = {"reason": "no_profitable_field", "anchors": len(used), "fields": fields_checked}
            return None
        for item in finalists:
            selected = np.array([i for _, i in item["selected"]], dtype=np.int32)
            exposure_factors = np.minimum(1, rate[selected] * item["duration"])
            if item["program_i"] is None:
                votes = np.sum((task.weight[selected] * exposure_factors)[None, :] * expected_mult[:, selected], axis=1)
                program_i = int(np.argmax(votes))
            else:
                program_i = item["program_i"]
            program = PROGRAMS[program_i]
            command_alt = item["alt"] - self.pointing.alt_offset
            command_az = (item["az"] - self.pointing.az_offset) % 360.0
            if not 0 <= command_alt <= 90:
                continue
            action = {
                "action": "observe",
                "pointing": {"alt_deg": round(command_alt, 6), "az_deg": round(command_az, 6)},
                "assignments": {str(fiber): task.ids[i] for fiber, i in item["selected"]},
                "duration_seconds": item["duration"],
                "program": program,
            }
            if any(candidate["action"] == action for candidate in self.alternatives):
                continue
            pending = {
                "start": now,
                "duration": item["duration"],
                "program": program,
                "night": night_index,
                "pointing": dict(action["pointing"]),
                "targets": [
                    {
                        "index": i,
                        "fiber": fiber,
                        "base_quality": float(base_quality[i]),
                        "clean": bool(clean[i]),
                        "matched_probability": float(probabilities[program_i, i]),
                        "predicted_factor": float(min(1, rate[i] * item["duration"])),
                    }
                    for fiber, i in item["selected"]
                ],
            }
            self.alternatives.append({"action": action, "pending": pending, "merit": item["merit"]})
            if len(self.alternatives) == 2:
                break
        if not self.alternatives:
            return None
        action = self.alternatives[0]["action"]
        self.pending = self.alternatives[0]["pending"]
        selected = np.array([row["index"] for row in self.pending["targets"]], dtype=np.int32)
        self.last_trace = {
            "anchors": len(used),
            "fields": fields_checked,
            "assignments": len(selected),
            "merit_per_second": round(self.alternatives[0]["merit"], 6),
            "quality_scale": round(scale, 5),
            "required_assigned": int(np.count_nonzero(incomplete[selected])),
            "program": action["program"],
            "duration_seconds": action["duration_seconds"],
            "visible_targets": int(np.count_nonzero(visible)),
        }
        return action

    def _center_up(self, alt: float, az: float, lst: float) -> float:
        ra, dec = to_equatorial(alt, az, lst, self.task.latitude)
        hmax = float(max_hour_angle(dec, self.task.latitude, self.task.minimum_altitude + 0.1))
        return (hmax - float(wrap180(lst - ra))) / SIDEREAL_RATE if hmax < 180 else 1e9

    def _centers_up(self, alt: np.ndarray, az: np.ndarray, lst: float) -> np.ndarray:
        latitude = self.task.latitude
        a, z = np.asarray(alt) * RAD, np.asarray(az) * RAD
        lat = latitude * RAD
        sin_a, cos_a = np.sin(a), np.cos(a)
        sin_lat, cos_lat = math.sin(lat), math.cos(lat)
        dec = np.arcsin(np.clip(sin_a * sin_lat + cos_a * cos_lat * np.cos(z), -1, 1))
        cd = np.maximum(1e-12, np.cos(dec))
        sh = -np.sin(z) * cos_a / cd
        ch = (sin_a - np.sin(dec) * sin_lat) / (cd * max(1e-12, cos_lat))
        ra = (lst - np.arctan2(sh, ch) / RAD) % 360.0
        hmax = max_hour_angle(dec / RAD, latitude, self.task.minimum_altitude + 0.1)
        return np.where(hmax < 180, (hmax - wrap180(lst - ra)) / SIDEREAL_RATE, 1e9)

    def _durations(self, rate, required, left: float):
        low, high = self.task.minimum_exposure, min(self.task.maximum_exposure, int(left))
        values = list(self.settings["durations"])
        if self.settings["adaptive_durations"]:
            thresholds = np.where(required, self.settings["required_safe_factor"], 1.0)
            candidates = thresholds / np.maximum(rate, 1e-9)
            values.extend(np.ceil(candidates / 30).astype(np.int64) * 30)
        values.append(high)
        return np.array(sorted({int(x) for x in values if low <= x <= high}), dtype=np.int32)
