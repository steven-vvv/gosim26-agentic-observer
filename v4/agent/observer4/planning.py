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


class Planner:
    def __init__(self, task, state, quality, pointing, config: dict):
        self.task, self.state, self.quality, self.pointing = task, state, quality, pointing
        self.config = config
        self.settings = config["policy"]
        task.prepare_neighbors(self.settings["neighbor_radius_deg"])
        self.last_trace = {}
        self.pending = None
        self.alternatives = []

    def set_strategy(self, strategy: str) -> None:
        self.settings = dict(self.config["policy"])
        if strategy == "coverage_focus":
            self.settings["required_bonus"] = max(120.0, self.settings["required_bonus"])
            self.settings["required_safe_factor"] = min(0.55, self.settings["required_safe_factor"])
        elif strategy != "balanced":
            raise ValueError("unknown weekly strategy")

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
        threshold = float(task.scoring["required"]["observed_factor_threshold"])
        incomplete = task.required & (state.factor_lower < threshold)
        urgency = 1.0 + settings["deadline_weight"] / np.maximum(1, task.last_night - night_index + 1)
        setting_boost = 1.0 + 0.35 * np.maximum(0, ha / np.maximum(1, task.hmax))
        science = task.weight * np.maximum(
            0, reach ** settings["science_exponent"] - state.factor_estimate ** settings["science_exponent"]
        )
        required = incomplete * (reach >= settings["required_safe_factor"]) * settings["required_bonus"]
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
        probabilities = self.quality.program_probabilities(base_quality)
        mult = np.array([task.scoring["program"]["multipliers"][name] for name in PROGRAMS])
        mismatch = task.scoring["program"]["mismatch_multiplier"]
        expected_mult = mismatch + (mult[:, None] - mismatch) * probabilities
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
            science_gain = task.weight[near] * np.maximum(
                0, factors ** settings["science_exponent"] - state.factor_estimate[near] ** settings["science_exponent"]
            )
            req_gain = incomplete[near][None, :] * (factors >= settings["required_safe_factor"]) * settings["required_bonus"]
            fairness = (
                (state.factor_lower[near][None, :] < threshold) & (factors >= settings["required_safe_factor"])
            ) * uniformity[near]
            gains = (science_gain + req_gain + fairness) * urgency[near] * setting_boost[near]
            gains = np.where(durations[:, None] <= setting_seconds[near][None, :], gains, 0)
            if not np.any(gains > 0):
                continue
            # Choose a winner independently for every fiber at every duration, then sum.
            total = np.zeros((len(ca), len(durations)))
            for fiber in range(task.grid.n):
                values = np.where(fibers[:, None, :] == fiber, gains[None, :, :] * geometry_weight[:, None, :], 0)
                total += np.max(values, axis=2)
            center_limit = np.array([self._center_up(float(a), float(z), lst) for a, z in zip(ca, cz)])
            total = np.where(durations[None, :] <= center_limit[:, None], total, 0)
            merit = total / durations[None, :]
            flat = int(np.argmax(merit))
            center_i, duration_i = np.unravel_index(flat, merit.shape)
            value = float(merit[center_i, duration_i])
            if value <= 0 or (len(finalists) >= 6 and value <= finalists[-1]["merit"]):
                continue
            selected = []
            gain = gains[duration_i] * geometry_weight[center_i]
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
            votes = np.sum((task.weight[selected] * exposure_factors)[None, :] * expected_mult[:, selected], axis=1)
            program_i = int(np.argmax(votes))
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

    def _durations(self, rate, required, left: float):
        low, high = self.task.minimum_exposure, min(self.task.maximum_exposure, int(left))
        values = list(self.settings["durations"])
        if self.settings["adaptive_durations"]:
            thresholds = np.where(required, self.settings["required_safe_factor"], 1.0)
            candidates = thresholds / np.maximum(rate, 1e-9)
            values.extend(np.ceil(candidates / 30).astype(np.int64) * 30)
        values.append(high)
        return np.array(sorted({int(x) for x in values if low <= x <= high}), dtype=np.int32)
