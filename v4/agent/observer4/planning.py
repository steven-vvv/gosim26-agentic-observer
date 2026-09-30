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
        self._rate_profile = None
        self._terrain_profile = None
        self._plan_seconds = None
        self._plan_anchors = 1.0
        self._exposure_seconds = 900.0

    def _paced_anchors(self, now: float, night_index: int, remaining_wallclock: float) -> int:
        """Anchor count that keeps the remaining plans inside the wall-clock budget.

        Planning time grows with the number of anchor fields; remaining plans are
        estimated from the observing time left and the recent mean exposure length.
        """
        settings = self.settings
        base = int(settings["anchors"])
        if remaining_wallclock < settings["budget_reserve_seconds"] + 15:
            return min(base, 2)
        if not settings.get("pace_budget", True) or self._plan_seconds is None:
            return base
        task = self.task
        left = max(0.0, task.nights[night_index].end - now) + sum(
            night.end - night.start for night in task.nights[night_index + 1:])
        plans = max(1.0, 1.15 * left / max(60.0, self._exposure_seconds))
        allowance = (remaining_wallclock - settings["budget_reserve_seconds"] - 20.0) / plans
        per_anchor = self._plan_seconds / max(1, self._plan_anchors)
        if per_anchor <= 0:
            return base
        return int(np.clip(math.floor(allowance / per_anchor), 2, base))

    def record_timing(self, seconds: float, anchors: int, duration: float | None) -> None:
        alpha = 0.05
        if self._plan_seconds is None:
            self._plan_seconds, self._plan_anchors = seconds, max(1, anchors)
        else:
            self._plan_seconds = (1 - alpha) * self._plan_seconds + alpha * seconds
            self._plan_anchors = (1 - alpha) * self._plan_anchors + alpha * max(1, anchors)
        if duration:
            self._exposure_seconds = (1 - alpha) * self._exposure_seconds + alpha * duration

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
        terrain_free = self.quality.terrain_free
        local_clean = self.quality.local_clean
        notice_clean = self.quality.notice_clean
        try:
            predicted = self.quality.level_prediction(now, night_index)
        except Exception:  # noqa: BLE001 - the frozen quality scale remains available
            self.quality.monitor, predicted = None, None
        zero_point = task.scoring["flux_zero_point"] * task.scoring["exposure_zero_point_seconds"]
        terrain = self.quality.terrain_model
        track = self._track(now, altitude, azimuth) if (predicted is not None or terrain is not None) else None
        self._terrain_profile = None
        if terrain is not None:
            try:
                self._terrain_profile = self._terrain_track(track, terrain)
            except Exception:  # noqa: BLE001 - fall back to the announced-sector envelope next plan
                self.quality.terrain_model = None
        if predicted is None:
            base_quality = lunar_factors(
                task.ra, task.dec, now + 450.0, task.latitude, task.longitude, task.scoring.get("lunar_model")
            ) / (task.scoring["q0"] * airmass(altitude) ** task.scoring["airmass_exponent"])
            scale = self.quality.scale(now)
            safety = settings["quality_safety"]
            profile = None
            probabilities = self.quality.program_probabilities(base_quality)
        else:
            scale, sigma = predicted
            safety = math.exp(-settings.get("level_safety_z", 1.5) * sigma)
            profile = self._k_profile(now, track)
            base_quality = profile[1][1]
            probabilities = self.quality.program_probabilities(
                base_quality, max(settings.get("program_uncertainty_floor", 0.05), sigma))
        rate = task.flux * base_quality * scale * directional * safety / zero_point
        if profile is not None:
            self._rate_profile = (profile[0], task.flux * profile[1] * scale * directional * safety / zero_point)
        else:
            self._rate_profile = None
        reach = np.minimum(1, rate * np.minimum(np.minimum(task.maximum_exposure, setting_seconds), left))
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
        priority = (science + required) * urgency * setting_boost * directional * terrain_free
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
        count = self._paced_anchors(now, night_index, remaining_wallclock)
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
            near = near[visible[near] & (directional[near] > 0) & (terrain_free[near] > 0.02)]
            if not len(near):
                continue
            ca, cz = task.grid.centers_for_target(float(altitude[anchor]), float(azimuth[anchor]))
            # Revision 2 scores each target on its own altitude; the centre only needs to be
            # a valid pointing. Revision 1 also required the centre above the limit.
            relaxed = getattr(task, "revision", 1) >= 2 and settings.get("free_center", True)
            floor = 0.0 if relaxed else task.minimum_altitude + 0.4
            keep = (ca >= floor) & (ca <= 89.85)
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
            factors = np.minimum(1, durations[:, None] * self._rates(durations, near, rate))
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
            gains = gains * self._terrain_open(durations, near, terrain_free)
            gains = np.where(durations[:, None] <= setting_seconds[near][None, :], gains, 0)
            if not np.any(gains > 0):
                continue
            if settings.get("score_marginal", False):
                total = fiber_totals(fibers, gains.reshape(-1, len(near)), geometry_weight, task.grid.n)
                total = total.reshape(len(ca), len(PROGRAMS), len(durations))
            else:
                total = fiber_totals(fibers, gains, geometry_weight, task.grid.n)
            center_limit = self._centers_up(ca, cz, lst) if not relaxed else np.full(len(ca), 1e9)
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
            item_rate = self._rates(np.array([item["duration"]]), selected, rate)[0]
            exposure_factors = np.minimum(1, item_rate * item["duration"])
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
                "signature": frozenset(kind for kind, direction in self.quality.notices if direction == "ALL"),
                "predicted_level": None if predicted is None else [math.log(max(scale, 1e-9)), predicted[1]],
                "pointing": dict(action["pointing"]),
                "targets": [
                    {
                        "index": i,
                        "fiber": fiber,
                        "base_quality": float(base_quality[i]),
                        "clean": bool(clean[i]),
                        "local_clean": bool(local_clean[i]),
                        "notice_clean": bool(notice_clean[i]),
                        "matched_probability": float(probabilities[program_i, i]),
                        "predicted_factor": float(min(1, rate_i * item["duration"])),
                    }
                    for (fiber, i), rate_i in zip(item["selected"], item_rate)
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

    PROFILE_OFFSETS = (0.0, 300.0, 600.0, 900.0, 1350.0, 1800.0)
    TRACK_TIMES = (0.0, 300.0, 600.0, 900.0, 1350.0, 1800.0, 2700.0, 3600.0)

    def _track(self, now: float, altitude0, azimuth0) -> dict:
        """Altitude and azimuth of every target at fixed offsets after now."""
        task = self.task
        alt, az = [np.asarray(altitude0)], [np.asarray(azimuth0)]
        for offset in self.TRACK_TIMES[1:]:
            a, z = altaz(horizontal_vectors(task.ra, task.dec, sidereal(now + offset, task.longitude), task.latitude))
            alt.append(a)
            az.append(z)
        return {"now": now, "alt": np.array(alt), "az": np.array(az)}

    def _k_profile(self, now: float, track: dict):
        """Lunar factor over airmass at exposure-midpoint offsets for every target."""
        task = self.task
        exponent = task.scoring["airmass_exponent"]
        model = task.scoring.get("lunar_model")
        rows = []
        for j, offset in enumerate(self.PROFILE_OFFSETS):
            lunar = lunar_factors(task.ra, task.dec, now + max(offset, 60.0), task.latitude, task.longitude, model)
            rows.append(lunar / (task.scoring["q0"] * airmass(np.maximum(track["alt"][j], 1.0)) ** exponent))
        # Midpoint sampling; time-resolved scoring differs only at second order in time.
        return np.array(self.PROFILE_OFFSETS), np.array(rows)

    def _terrain_track(self, track: dict, terrain) -> dict:
        """Unblocked probability along each envelope target's track."""
        envelope = np.zeros(track["alt"].shape[1], dtype=bool)
        for j in range(len(self.TRACK_TIMES)):
            envelope |= terrain.envelope(track["az"][j], track["alt"][j])
        candidates = np.flatnonzero(envelope)
        free = np.ones((len(self.TRACK_TIMES), len(candidates)))
        if len(candidates):
            for j in range(len(self.TRACK_TIMES)):
                free[j] = 1.0 - terrain.blocked_probability(track["az"][j, candidates], track["alt"][j, candidates])
        # Mean unblocked fraction over [0, t] at every track time (trapezoid rule).
        times = np.array(self.TRACK_TIMES)
        cumulative = np.zeros_like(free)
        for j in range(1, len(times)):
            cumulative[j] = cumulative[j - 1] + 0.5 * (free[j] + free[j - 1]) * (times[j] - times[j - 1])
        mean = free.copy()
        mean[1:] = cumulative[1:] / times[1:, None]
        position = np.full(track["alt"].shape[1], -1, dtype=np.int64)
        position[candidates] = np.arange(len(candidates))
        return {"times": times, "mean": mean, "position": position}

    def _terrain_open(self, durations, indices, fallback):
        """Expected unblocked fraction of each exposure length for the given targets."""
        profile = self._terrain_profile
        durations = np.asarray(durations, dtype=float)
        result = np.ones((len(durations), len(indices)))
        if profile is None:
            return result * fallback[indices][None, :] if fallback is not None else result
        position = profile["position"][indices]
        inside = position >= 0
        if not np.any(inside):
            return result
        times, mean = profile["times"], profile["mean"][:, position[inside]]
        span = np.clip(durations, times[1], times[-1])
        j = np.clip(np.searchsorted(times, span, side="right") - 1, 1, len(times) - 2)
        w = (span - times[j]) / (times[j + 1] - times[j])
        result[:, inside] = (1 - w)[:, None] * mean[j] + w[:, None] * mean[j + 1]
        return result

    def _rates(self, durations, indices, fallback):
        """Rate per duration for the given targets, using the midpoint profile if available."""
        durations = np.asarray(durations, dtype=float)
        if self._rate_profile is None:
            return np.broadcast_to(fallback[indices], (len(durations), len(indices)))
        offsets, rates = self._rate_profile
        mid = np.clip(durations / 2, offsets[0], offsets[-1])
        j = np.clip(np.searchsorted(offsets, mid, side="right") - 1, 0, len(offsets) - 2)
        w = (mid - offsets[j]) / (offsets[j + 1] - offsets[j])
        sub = rates[:, indices]
        return (1 - w)[:, None] * sub[j] + w[:, None] * sub[j + 1]

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
