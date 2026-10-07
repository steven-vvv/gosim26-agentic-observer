"""Joint field, fiber, exposure and program search under public online beliefs."""

from __future__ import annotations

import copy
import math

import numpy as np

from .durations import Durations
from .fibers import _segments, fiber_totals
from .geometry import (
    RAD,
    SIDEREAL_RATE,
    airmass,
    altaz,
    horizontal_vectors,
    lunar_factors,
    max_hour_angle,
    parse_utc,
    sidereal,
    to_equatorial,
    wrap180,
)
from .opportunity import OpportunityModel
from .rates import RateProfile
from .reach import Reach, spread_anchors
from .sky import program_odds, visible_sky
from .structure import card_structure
from .valuation import science_gains

PROGRAMS = ("DARK", "BRIGHT", "BACKUP")
# Priority factor after k recorded misses (k capped at 6): max(0.25, 0.80 ** k), elementwise.
MISS_FACTORS = np.maximum(0.25, 0.80 ** np.arange(7))
WEEKLY_STRATEGIES = ("steady", "required_push", "science_push")


def strategy_settings(policy: dict, strategy: str) -> dict:
    """Planner settings of a weekly strategy; ``steady`` is the frozen policy itself.

    ``required_push`` raises the required-target completion bonus by 30 % and plans those
    targets to a safer completion factor; ``science_push`` lowers the bonus by 25 %.
    """
    settings = dict(policy)
    if strategy == "required_push":
        settings["required_bonus"] = 1.3 * settings["required_bonus"]
        settings["required_safe_factor"] = max(0.55, settings["required_safe_factor"])
    elif strategy == "science_push":
        settings["required_bonus"] = 0.75 * settings["required_bonus"]
    elif strategy != "steady":
        raise ValueError("unknown weekly strategy")
    return settings


def _fiber_choices(fibers: np.ndarray, gain: np.ndarray, near: np.ndarray) -> list:
    """(fibre, target) with the largest positive gain in each fibre, in fibre order.

    Ties keep the lowest index, as np.argmax over each fibre's targets did.
    """
    ids = np.flatnonzero((fibers >= 0) & (gain > 0))
    if not len(ids):
        return []
    ids = ids[np.lexsort((-gain[ids], fibers[ids]))]
    fiber = fibers[ids]
    starts, _ = _segments(fiber)
    return list(zip(fiber[starts].tolist(), near[ids[starts]].tolist()))


def expected_science_gains(factors: np.ndarray, weights: np.ndarray, best: np.ndarray,
                           probabilities: np.ndarray, multipliers: np.ndarray, mismatch: float) -> np.ndarray:
    """Expected improvement of the public best score for each declared program."""
    score = factors * weights[None, :]
    baseline = np.maximum(0, score * mismatch - best[None, :])
    matching = np.maximum(0, score[None, :, :] * multipliers[:, None, None] - best[None, None, :])
    return probabilities[:, None, :] * matching + (1 - probabilities[:, None, :]) * baseline[None, :, :]


def science_value(factor: np.ndarray, exponent: float, cap: np.ndarray | None) -> np.ndarray:
    """Credited science per unit weight for a completion factor.

    Without a cap this is factor ** exponent. With a per-target cap (the factor a typical
    sky reaches in the longest exposure) a factor below the cap earns cap * (f / cap) **
    exponent and one above it earns f: an exponent above one then discounts partial
    exposures that a later, longer exposure would supersede, without penalising faint
    targets that can never reach a factor of one.
    """
    if cap is None:
        if np.ndim(exponent) == 0:
            return factor ** exponent
        # pow(1, p) is exactly 1, so saturated factors (most long exposures) skip the power.
        result = np.ones(np.broadcast_shapes(np.shape(factor), np.shape(exponent)))
        return np.power(factor, exponent, out=result, where=np.not_equal(factor, 1.0))
    ratio = np.minimum(factor / cap, 1.0)
    return np.where(factor >= cap, factor, cap * ratio ** exponent)


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
        self.structure = card_structure(task, config.get("structure") or {})
        self.settings = self._scaled_settings(config["policy"])
        self.scale_profile = self._scale_active()
        task.prepare_neighbors(self.settings["neighbor_radius_deg"])
        self.reach = Reach(task)
        weight = self.settings.get("scarcity_weight", 0)
        self.scarcity = (scarcity_multipliers(task.visibility, task.required, task.minimum_exposure, weight)
                         if weight else None)
        self.instrument = None
        self.opportunity = None
        if self.settings.get("opportunity_weight", 0.0) > 0:
            try:
                self.opportunity = OpportunityModel(task, self.settings)
            except Exception:  # noqa: BLE001 - the deadline urgency remains available
                self.opportunity = None
        self.last_trace = {}
        self.pending = None
        self.alternatives = []
        self._rate_profile = None
        self._terrain_profile = None
        self._pace_origin = None
        # Diagnostics only: plans whose search the budget cut, and the smallest ratio of
        # remaining budget to projected need.
        self.paced_plans = 0
        self.pace_headroom = None
        self.pace_headroom_at = None
        self.paced_nights = []
        # Night from which the search runs shrunk by pace_shrink (None: never).
        self.shrunk_at = None
        self._future_hours = None
        self.horizon_limit = None
        self.active_requests = []

    def _scale_active(self) -> bool:
        profile = self.config.get("scale") or {}
        cost = len(getattr(self.task, "ids", ())) * int(getattr(getattr(self.task, "grid", None), "n", 0))
        return bool(profile.get("enabled", False)) and cost >= int(profile.get("cost_threshold", 0))

    def _scaled_settings(self, settings: dict) -> dict:
        """Reduce geometric search only when the card's estimated work is large.

        The dominant matrix is candidate targets by fibre slots.  A fixed search budget
        makes high-fibre cards spend the 900-second run in planning, while applying the
        same reduction to small cards costs score.  The threshold is public configuration
        so it can be audited and tuned without changing the decision logic.
        """
        profile = self.config.get("scale") or {}
        overrides = (getattr(self, "structure", None) or {}).get("overrides") or {}
        if not self._scale_active():
            return {**settings, **overrides} if overrides else settings
        scaled = dict(settings)
        for name in ("anchor_pool", "anchors", "cluster_anchors", "refine_top", "setting_boost"):
            if name in profile:
                scaled[name] = profile[name]
        if profile.get("durations"):
            scaled["durations"] = list(profile["durations"])
        scaled.update(overrides)
        return scaled

    def _science_exponents(self, night_index: int):
        """Science exponent, per target when it depends on the remaining public visibility.

        A partial exposure only keeps its value if no later exposure supersedes it. Targets
        with many visible hours left get the configured (convex) exponent; the exponent
        falls linearly to one as the remaining hours after tonight shrink to zero.
        """
        base = float(self.settings["science_exponent"])
        horizon = float(self.settings.get("science_horizon_hours", 0.0))
        if horizon <= 0 or base == 1.0:
            return base
        if self._future_hours is None:
            future = np.cumsum(self.task.visibility[::-1], axis=0)[::-1] / 3600.0
            self._future_hours = np.vstack((future[1:], np.zeros((1, future.shape[1]))))
        share = np.clip(self._future_hours[night_index] / horizon, 0.0, 1.0)
        return 1.0 + (base - 1.0) * share

    def _remaining_hours(self, night_index: int) -> np.ndarray:
        """Public visible hours of every target from tonight to the end of the season."""
        if self._future_hours is None:
            future = np.cumsum(self.task.visibility[::-1], axis=0)[::-1] / 3600.0
            self._future_hours = np.vstack((future[1:], np.zeros((1, future.shape[1]))))
        tonight = self.task.visibility[night_index] / 3600.0
        return self._future_hours[night_index] + tonight

    def set_requests(self, active_requests: list[dict]) -> None:
        self.active_requests = active_requests

    def _request_terms(self, now: float) -> list[dict]:
        weight = self.settings.get("request_weight", 1.0)
        if weight <= 0:
            return []
        terms = []
        for request in self.active_requests:
            remaining = int(request["remaining_count"])
            deadline = parse_utc(request["deadline_utc"]).timestamp()
            if remaining <= 0 or deadline - now < self.task.minimum_exposure:
                continue
            completed = set(request["completed_target_ids"])
            indices = np.array(
                [self.task.index[target_id] for target_id in request["target_ids"]
                 if target_id not in completed and target_id in self.task.index], dtype=np.int32
            )
            if len(indices) < remaining:
                continue
            terms.append({
                "indices": indices,
                "deadline": deadline,
                "threshold": min(1.0, float(request["completion_factor_threshold"]) + 0.02),
                "bonus": float(request["completion_reward"]) / remaining * weight,
            })
        return terms

    @staticmethod
    def _request_gains(terms: list[dict], near: np.ndarray, durations: np.ndarray,
                       factors: np.ndarray, now: float) -> np.ndarray:
        gains = np.zeros_like(factors)
        for term in terms:
            selected = np.isin(near, term["indices"])
            if np.any(selected):
                valid = durations[:, None] <= term["deadline"] - now
                gains[:, selected] += term["bonus"] * valid * (factors[:, selected] >= term["threshold"])
        return gains

    def _refine(self, item, vectors, glass_margin, lst, relaxed, floor) -> int:
        """Try small centre shifts around a finalist; keep the best field (local search).

        Without gaps a shift of a fraction of a cell moves targets between cells, which
        resolves collisions of two valuable targets in one cell.
        """
        search = item.get("_search")
        if search is None:
            return 0
        near, gains, durations = search
        task, settings = self.task, self.settings
        steps = np.asarray(settings.get("refine_shifts", [-0.24, -0.12, 0.12, 0.24]), dtype=float)
        da, dz = np.meshgrid(np.r_[0.0, steps], np.r_[0.0, steps], indexing="ij")
        da, dz = da.ravel()[1:], dz.ravel()[1:]
        ca = item["alt"] + da
        cz = (item["az"] + dz / max(0.05, math.cos(item["alt"] * RAD))) % 360.0
        keep = (ca >= floor) & (ca <= 89.85)
        ca, cz = ca[keep], cz[keep]
        if not len(ca):
            return 0
        fibers, margin, _, _ = task.grid.classify(vectors[near], ca, cz)
        geometry_weight = np.where(fibers >= 0, 1.0, 0.0)
        geometry_weight *= np.where(margin >= glass_margin, 1.0, 0.45)
        total = fiber_totals(fibers, gains, geometry_weight, task.grid.n)
        center_limit = self._centers_up(ca, cz, lst) if not relaxed else np.full(len(ca), 1e9)
        total = np.where(durations[None, :] <= center_limit[:, None], total, 0)
        merit = total / (durations[None, :] + float(settings.get("duration_overhead", 0.0)))
        flat = int(np.argmax(merit))
        center_i, duration_i = np.unravel_index(flat, merit.shape)
        value = float(merit[center_i, duration_i])
        if value <= item["merit"] * (1.0 + 1e-9):
            return len(ca)
        selected = _fiber_choices(fibers[center_i], gains[duration_i] * geometry_weight[center_i], near)
        item.update({"merit": value, "alt": float(ca[center_i]), "az": float(cz[center_i]),
                     "duration": int(durations[duration_i]), "selected": selected, "refined": True})
        return len(ca)

    def _arrival_cap(self, now, left, incomplete, visible, reach, ha, scale, safety, zero_point, open_now):
        """Latest end for this exposure so that a burst of required targets finds the telescope free.

        Required targets rising later in the night (or reachable only once higher) are
        scanned every 5 minutes with the current sky level; when enough of them become
        reachable within the look-ahead, exposures are cut to end by then. Without
        readout overheads, a shorter exposure loses little; a missed window is lost.
        """
        task, settings = self.task, self.settings
        safe = settings["required_safe_factor"]
        now_ok = incomplete & visible & (reach * open_now >= safe)
        candidates = np.flatnonzero(incomplete & ~now_ok & (task.hmax > 0))
        minimum = int(settings.get("arrival_min", 8))
        # Only a telescope without required work now needs to keep itself free for later.
        if len(candidates) < minimum or np.count_nonzero(now_ok) >= float(settings.get("arrival_busy", 0.5)) * minimum:
            return None
        horizon = min(float(settings.get("arrival_horizon", 3600.0)), left - task.minimum_exposure)
        if horizon < 300.0:
            return None
        ra, dec, flux = task.ra[candidates], task.dec[candidates], task.flux[candidates]
        model = task.scoring.get("lunar_model")
        exponent = task.scoring["airmass_exponent"]
        limit = task.minimum_altitude + float(settings.get("altitude_margin_deg", 0.2))
        terrain = self.quality.terrain_model
        arrived = np.zeros(len(candidates), dtype=bool)
        for offset in np.arange(300.0, horizon + 1.0, 300.0):
            moment = now + offset
            altitude, azimuth = altaz(horizontal_vectors(ra, dec, sidereal(moment, task.longitude), task.latitude))
            up = altitude >= limit
            if not np.any(up):
                continue
            k = lunar_factors(ra, dec, moment, task.latitude, task.longitude, model)
            k = k / (task.scoring["q0"] * airmass(np.maximum(altitude, 1.0)) ** exponent)
            ha_future = wrap180(sidereal(moment, task.longitude) - ra)
            setting = np.where(task.hmax[candidates] >= 180, 1e9, (task.hmax[candidates] - ha_future) / SIDEREAL_RATE)
            span = np.minimum(np.minimum(task.maximum_exposure, setting), left - offset)
            factor = flux * k * scale * safety / zero_point * span
            if terrain is not None:
                factor = factor * (1.0 - terrain.blocked_probability(azimuth, altitude))
            factor = factor * self.quality.notice_factor(altitude, azimuth)
            arrived |= up & (factor >= safe)
            if np.count_nonzero(arrived) >= minimum:
                return float(offset)
        return None

    def _cluster_order(self, pool, priority, count: int, extra: int):
        """Pool order: the first anchors by own priority, then by nearby priority sum."""
        task = self.task
        radius = math.cos(float(self.settings.get("cluster_radius_deg", 1.3)) * RAD)
        values = np.zeros(len(pool))
        # Only the anchors after the head are ordered by their surroundings.
        for k in range(count, len(pool)):
            values[k] = float(np.sum(priority[task.close_neighbors(int(pool[k]), radius)]))
        head = pool[:count]
        rest = pool[count:]
        rest = rest[np.lexsort((rest, -values[count:]))]
        return np.concatenate((head, rest))

    def _paced_anchors(self, now: float, night_index: int, remaining_wallclock: float, extra: int = 0) -> int:
        """Anchor count that keeps the remaining plans inside the wall-clock budget.

        Planning time grows with the number of anchor fields and is front-loaded: early
        exposures are short, so the first nights hold several times their share of plans,
        and any extrapolation of the early rate overstates the need. The search therefore
        shrinks only when the usable wall clock falls below a fixed share
        (``pace_floor``) of the starting budget in proportion to the observing time left,
        and then steeply. A normal machine never reaches the floor, so the actions do not
        depend on timing noise.
        """
        settings = self.settings
        base = int(settings["anchors"]) + extra
        if remaining_wallclock < settings["budget_reserve_seconds"] + 15:
            self.paced_plans += 1
            self._shrink(night_index)
            return min(base, 2)
        if not settings.get("pace_budget", True):
            return base
        task = self.task
        left = max(0.0, task.nights[night_index].end - now) + sum(
            night.end - night.start for night in task.nights[night_index + 1:])
        reserve = settings["budget_reserve_seconds"] + 20.0
        if self._pace_origin is None:
            self._pace_origin = (max(left, 1.0), max(remaining_wallclock - reserve, 1.0))
        share = self._pace_origin[1] * min(1.0, left / self._pace_origin[0])
        if share <= 0:
            return base
        ratio = (remaining_wallclock - reserve) / share
        if self.pace_headroom is None or ratio < self.pace_headroom:
            self.pace_headroom = ratio
            self.pace_headroom_at = {"night": int(night_index), "remaining": round(remaining_wallclock, 1)}
        floor = settings.get("pace_floor", 0.8)
        if ratio < floor:
            self._shrink(night_index)
        base = self._shrunk(base, 2)
        if ratio >= floor:
            return base
        # Anchors are about a quarter of the plan time, so the cut is steep: none at the
        # floor, the minimum at half of it.
        count = int(np.clip(math.floor(base * (2.0 * ratio / floor - 1.0)), 2, base))
        self.paced_plans += count < base
        if count < base and len(self.paced_nights) < 64:
            self.paced_nights.append(int(night_index))
        return count

    def _shrink(self, night_index: int) -> None:
        # A card whose fixed cost per decision leaves little for the search cannot be held by
        # cutting anchors alone: the first time pacing is needed, the whole search (anchors,
        # pool, refinement) shrinks by pace_shrink for the rest of the run.
        if self.settings.get("pace_shrink", 0) > 0 and self.shrunk_at is None:
            self.shrunk_at = int(night_index)

    def _shrunk(self, value: int, low: int = 1) -> int:
        if getattr(self, "shrunk_at", None) is None:
            return value
        return max(low, round(value * float(self.settings.get("pace_shrink", 1.0))))

    def pacing_status(self) -> dict:
        return {"paced_plans": int(self.paced_plans), "shrunk_at": self.shrunk_at,
                "headroom_min": None if self.pace_headroom is None else round(float(self.pace_headroom), 3),
                "headroom_at": self.pace_headroom_at, "paced_nights": self.paced_nights}

    def set_strategy(self, strategy: str) -> None:
        self.settings = self._scaled_settings(strategy_settings(self.config["policy"], strategy))
        self.scale_profile = self._scale_active()

    def _rollout_requests(self, pending: dict) -> list[dict]:
        """Apply the candidate's likely request completions to a private forecast copy."""
        additions = {}
        for row in pending.get("targets", []):
            factor = float(row.get("predicted_factor", 0.0))
            additions[self.task.ids[int(row["index"])]] = factor
        result = []
        for request in self.active_requests:
            completed = set(request.get("completed_target_ids", []))
            threshold = float(request.get("completion_factor_threshold", 1.0))
            newly_completed = {target_id for target_id, factor in additions.items()
                               if factor >= threshold and target_id in request.get("target_ids", [])}
            completed.update(newly_completed)
            updated = dict(request)
            updated["completed_target_ids"] = sorted(completed)
            if "remaining_count" in updated:
                updated["remaining_count"] = max(0, int(updated["remaining_count"]) - len(newly_completed))
            result.append(updated)
        return result

    def _rollout_candidate(self, candidate: dict, now: float, night_index: int,
                           remaining_wallclock: float) -> float:
        """Estimate the best next merit after one candidate without changing live state."""
        pending = candidate["pending"]
        state = copy.deepcopy(self.state)
        state.task = self.task
        program = self.task.scoring["program"]
        multiplier = float(program["multipliers"][pending["program"]])
        mismatch = float(program["mismatch_multiplier"])
        for row in pending.get("targets", []):
            index = int(row["index"])
            factor = float(np.clip(row.get("predicted_factor", 0.0), 0.0, 1.0))
            probability = float(np.clip(row.get("matched_probability", 0.0), 0.0, 1.0))
            expected_multiplier = mismatch + probability * (multiplier - mismatch)
            state.best_score[index] = max(state.best_score[index], self.task.weight[index] * factor * expected_multiplier)
            state.factor_lower[index] = max(
                state.factor_lower[index], factor * expected_multiplier / max(multiplier, mismatch, 1e-9)
            )
            state.factor_estimate[index] = max(state.factor_estimate[index], factor)
            state.attempts[index] += 1

        quality = copy.deepcopy(self.quality)
        quality.task = self.task
        pointing = copy.deepcopy(self.pointing)
        pointing.task = self.task
        config = copy.deepcopy(self.config)
        # Keep the currently selected weekly settings, but never recurse into rollout.
        config["policy"] = dict(self.settings, rollout_top=0, rollout_weight=0.0)
        planner = Planner(self.task, state, quality, pointing, config)
        if self.instrument is not None:
            planner.instrument = copy.deepcopy(self.instrument)
            planner.instrument.task = self.task
        planner.set_requests(self._rollout_requests(pending))
        next_budget = max(0.0, float(remaining_wallclock) - float(pending["duration"]))
        next_action = planner.plan(now + float(pending["duration"]), night_index, next_budget)
        if next_action is None:
            return 0.0
        return float(planner.last_trace.get("merit_per_second", 0.0))

    def _apply_rollout(self, now: float, night_index: int, remaining_wallclock: float) -> dict:
        """Rerank the two deterministic finalists using an optional one-step forecast."""
        top = int(self.settings.get("rollout_top", 0))
        weight = float(self.settings.get("rollout_weight", 0.0))
        status = {"rollout_top": top, "rollout_weight": round(weight, 4), "rollout_candidates": 0,
                  "rollout_failures": 0}
        if top < 2 or weight <= 0 or len(self.alternatives) < 2:
            return status
        for candidate in self.alternatives[:min(top, len(self.alternatives))]:
            try:
                future = self._rollout_candidate(candidate, now, night_index, remaining_wallclock)
            except Exception:  # noqa: BLE001 - research-only look-ahead must never stop a run
                future = 0.0
                status["rollout_failures"] += 1
            candidate["rollout_future_merit"] = round(future, 6)
            candidate["rollout_score"] = candidate["merit"] + weight * future
            status["rollout_candidates"] += 1
        self.alternatives.sort(
            key=lambda item: (float(item.get("rollout_score", item["merit"])), float(item["merit"])),
            reverse=True,
        )
        return status

    def plan(self, now: float, night_index: int, remaining_wallclock: float = 900.0) -> dict | None:
        self.alternatives = []
        task, state, settings = self.task, self.state, self.settings
        night = task.nights[night_index]
        left = min(night.end, task.end) - now
        if self.horizon_limit is not None:
            # Research hook (truth-informed replays): end exposures before a known closure.
            left = min(left, self.horizon_limit - now)
        if left < task.minimum_exposure:
            return None
        lst = sidereal(now, task.longitude)
        ha = wrap180(lst - task.ra)
        setting_seconds = np.where(task.hmax >= 180, 1e9, (task.hmax - ha) / SIDEREAL_RATE)
        visible = (ha >= -task.hmax) & (setting_seconds >= task.minimum_exposure) & (task.hmax > 0)
        # Only visible targets can enter a plan, so sky, tracks and profiles cover just them.
        subset = np.flatnonzero(visible)
        part = task.horizon.take(subset)
        vectors, altitude, azimuth, directional, clean = visible_sky(task, self.quality, lst, ha, subset, part)
        terrain_free = self.quality.terrain_free
        local_clean = self.quality.local_clean
        notice_clean = self.quality.notice_clean
        try:
            predicted = self.quality.level_prediction(now, night_index)
        except Exception:  # noqa: BLE001 - the frozen quality scale remains available
            self.quality.monitor, predicted = None, None
        zero_point = task.scoring["flux_zero_point"] * task.scoring["exposure_zero_point_seconds"]
        typical = float(settings.get("science_cap_quality", 0.0))
        science_cap = (np.clip(task.flux * typical * task.maximum_exposure / zero_point, 0.05, 1.0)
                       if typical > 0 else None)
        exponent = self._science_exponents(night_index)
        overhead = float(settings.get("duration_overhead", 0.0))
        terrain = self.quality.terrain_model
        horizon = part if (predicted is not None or terrain is not None) else None
        track = self._track(now, altitude, azimuth, subset, horizon) if terrain is not None else None
        self._terrain_profile = None
        if terrain is not None:
            try:
                self._terrain_profile = self._terrain_track(track, terrain)
            except Exception:  # noqa: BLE001 - fall back to the announced-sector envelope next plan
                self.quality.terrain_model = None
        if predicted is None:
            base_quality = lunar_factors(
                task.ra, task.dec, now + 450.0, task.latitude, task.longitude, task.scoring.get("lunar_model"),
                vectors=task.vectors,
            ) / (task.scoring["q0"] * airmass(altitude) ** task.scoring["airmass_exponent"])
            scale = self.quality.scale(now)
            safety = settings["quality_safety"]
            profile = None
            probabilities = program_odds(self.quality, base_quality, subset)
        else:
            scale, sigma = predicted
            safety = math.exp(-settings.get("level_safety_z", 1.5) * sigma)
            profile = RateProfile(task, now, altitude, subset, horizon, scale, safety, directional, zero_point)
            base_quality = profile.k
            try:
                correction = self.quality.band_correction(now)
            except Exception:  # noqa: BLE001 - the level-based bands remain available
                correction = 0.0
            uncertainty = max(settings.get("program_uncertainty_floor", 0.05), sigma)
            probabilities = program_odds(self.quality, base_quality, subset, *((uncertainty, correction) if correction else (uncertainty,)))
        rate = task.flux * base_quality * scale * directional * safety / zero_point
        if profile is not None:
            self._rate_profile = (profile.offsets, profile)
        else:
            self._rate_profile = None
        # Targets that have already set get a negative span; they are masked out below, and a
        # factor below zero would only produce NaN in the convex science value.
        reach = np.clip(rate * np.minimum(np.minimum(task.maximum_exposure, setting_seconds), left), 0, 1)
        self._last_reach, self._last_directional = reach, directional * terrain_free
        mult = np.array([task.scoring["program"]["multipliers"][name] for name in PROGRAMS])
        mismatch = task.scoring["program"]["mismatch_multiplier"]
        expected_mult = mismatch + (mult[:, None] - mismatch) * probabilities
        threshold = float(task.scoring["required"]["observed_factor_threshold"])
        incomplete = task.required & (state.factor_lower < threshold)
        urgency = 1.0 + settings["deadline_weight"] / np.maximum(1, task.last_night - night_index + 1)
        season = float(settings.get("season_weight", 0.0))
        if season > 0:
            # Long seasons: a target whose remaining public visible hours are few, compared with
            # the typical target, has fewer later chances; this ranks it ahead while it lasts.
            future = self._remaining_hours(night_index)
            alive = future > 0
            if np.any(alive):
                typical = float(np.median(future[alive]))
                scarce = np.clip(typical / np.maximum(future, 1.0), 1.0, 4.0) - 1.0
                urgency = urgency * (1.0 + season * np.where(alive, scarce, 0.0))
        window = float(settings.get("window_weight", 0.0))
        if window > 0:
            # Remaining chances in nights: visible hours left this season over tonight's. A
            # target that will be gone after a few such nights ranks ahead of one with many
            # left; a region that mostly rises later in the season is not urgent before then.
            tonight = task.visibility[night_index] / 3600.0
            nights_left = self._remaining_hours(night_index) / np.maximum(tonight, 0.25)
            up = tonight > 0
            if np.any(up):
                typical = float(np.median(nights_left[up]))
                scarce = np.clip(typical / np.maximum(nights_left, 1.0), 1.0, 4.0) - 1.0
                urgency = urgency * (1.0 + window * np.where(up, scarce, 0.0))
        if self.opportunity is not None and self.instrument is not None:
            try:
                chances = self.opportunity.urgency(night_index, self.instrument.sky_levels(), threshold,
                                                   float(settings["opportunity_weight"]))
                urgency = np.where(task.required, chances, urgency)
            except Exception:  # noqa: BLE001 - keep the deadline urgency
                self.opportunity = None
        setting_boost = 1.0 + float(settings.get("setting_boost", 0.35)) * np.maximum(0, ha / np.maximum(1, task.hmax))
        rising = float(settings.get("rising_weight", 0.0))
        if rising > 0:
            # A rising target that transits before the night ends is cheaper to expose later
            # tonight (lower airmass); discount it by how far it still is from the meridian.
            transit = now - ha / SIDEREAL_RATE
            later = (ha < 0) & (transit <= min(night.end, task.end))
            setting_boost = setting_boost * np.where(
                later, 1.0 - rising * np.clip(-ha / np.maximum(1, task.hmax), 0.0, 1.0), 1.0)
        tonight = float(settings.get("tonight_weight", 0.0))
        if tonight > 0:
            # Required targets whose window tonight closes soon lose their chance for the
            # night; targets that stay up can be done later. Nearer closing, higher urgency.
            remaining = np.minimum(setting_seconds, left)
            horizon = float(settings.get("tonight_horizon", 7200.0))
            closing = np.clip(1.0 - remaining / horizon, 0.0, 1.0)
            urgency = urgency * np.where(incomplete, 1.0 + tonight * closing, 1.0)
        inc = float(settings.get("revisit_increment", 0))
        if settings.get("score_marginal", False):
            science = np.max(expected_science_gains(reach[None, :], task.weight, state.best_score,
                                                    probabilities, mult, mismatch)[:, 0, :], axis=0)
        else:
            estimate_value = science_value(state.factor_estimate, exponent, science_cap)
            science = task.weight * science_gains(science_value(reach, exponent, science_cap), reach,
                                                  state.factor_estimate, estimate_value, inc)
        upgrade = float(settings.get("upgrade_weight", 0.0))
        if upgrade > 0:
            # A complete target scored under a mismatched or weaker program can still gain the
            # multiplier difference; valued linearly in score and scaled down, so it only wins
            # when new completions are scarce (long seasons).
            top_multiplier = float(np.max(mult))
            expected_top = np.max(expected_mult, axis=0)
            complete = state.factor_estimate >= float(settings.get("upgrade_factor", 0.95))
            linear = np.maximum(0, task.weight * expected_top * reach - state.best_score) / top_multiplier
            science = np.where(complete, np.maximum(science, upgrade * linear), science)
        required_bonus = settings["required_bonus"] if self.scarcity is None else (
            settings["required_bonus"] * self.scarcity[night_index]
        )
        required = incomplete * (reach >= settings["required_safe_factor"]) * required_bonus
        request_terms = self._request_terms(now)
        request_priority = np.zeros(len(task.ids))
        request_thresholds = np.zeros(len(task.ids))
        for term in request_terms:
            indices = term["indices"]
            span = np.minimum(np.minimum(task.maximum_exposure, setting_seconds[indices]),
                              min(left, term["deadline"] - now))
            reachable = np.minimum(1, rate[indices] * span) >= term["threshold"]
            request_priority[indices] += term["bonus"] * reachable
            request_thresholds[indices] = np.maximum(request_thresholds[indices], term["threshold"])
        priority = (science + required + request_priority) * urgency * setting_boost * directional * terrain_free
        priority *= MISS_FACTORS[np.minimum(state.misses, 6)]
        priority = np.where(visible & (task.last_night >= night_index), priority, 0)
        self._last_priority = priority
        cap = None
        probe = float(settings.get("stale_probe_seconds", 0))
        if probe > 0 and predicted is not None and predicted[1] > float(settings.get("probe_sigma", 0.06)):
            # A stale sky estimate (new night or segment) is first measured with a short
            # exposure; the following exposures then need a smaller safety margin.
            cap = probe
        if settings.get("arrival_cap", False):
            try:
                arrival = self._arrival_cap(now, left, incomplete, visible, reach, ha, scale, safety, zero_point,
                                            terrain_free)
            except Exception:  # noqa: BLE001 - planning continues without the look-ahead
                arrival = None
            if arrival is not None:
                cap = arrival if cap is None else min(cap, arrival)
        pool = np.flatnonzero(priority > 1e-8)
        if not len(pool):
            self.last_trace = {"reason": "no_positive_visible_target"}
            return None
        limit = min(len(pool), self._shrunk(settings["anchor_pool"]))
        pool = pool[np.argpartition(priority[pool], -limit)[-limit:]]
        pool = pool[np.lexsort((pool, -priority[pool]))]
        # Budget pacing uses measured time in the runtime; this is a predictable fallback.
        extra = int(settings.get("cluster_anchors", 0))
        total = self._paced_anchors(now, night_index, remaining_wallclock, extra)
        if extra > 0:
            # Sparse valuable targets (required ones) are completed a few per field; anchors
            # whose surroundings hold the most priority find the densest fields. Pacing
            # shrinks both kinds in proportion.
            base = int(settings["anchors"])
            extra = min(extra, round(total * extra / (base + extra)))
            pool = self._cluster_order(pool, priority, total - extra, extra)
        count = total
        uniformity = state.uniformity_gain() * settings["uniformity_weight"]
        glass_margin = settings["glass_margin_deg"]
        if settings.get("adaptive_margin", False):
            # A target only leaves its cell through pointing error; once hits pin the offset
            # down, the edge penalty shrinks to the remaining uncertainty.
            glass_margin = min(glass_margin, self.pointing.uncertainty + settings.get("margin_floor_deg", 0.012))
        finalists = []
        used = spread_anchors(task, pool, count)
        fields = [(anchor, *self.reach.field(settings, anchor, visible, directional, terrain_free)) for anchor in used]
        if profile is not None and fields:
            profile.prepare(np.concatenate([cropped for _, _, cropped in fields]))
        fields_checked = 0
        open_factor = (1.0 - state.factor_estimate) * (state.factor_estimate > 0) if inc > 0 else None
        candidates = Durations(task, settings, left if cap is None else min(left, cap), rate, incomplete,
                               request_thresholds, subset, open_factor)
        for anchor, near, cropped in fields:
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
                cz = np.concatenate((cz, cz, cz))
            fields_checked += len(ca)
            durations = candidates(near)
            if not len(durations):
                continue
            # The durations came from every neighbour; geometry and gains need only those in reach.
            near = cropped
            if not len(near):
                continue
            factors = durations[:, None] * self._rates(durations, near, rate)
            np.minimum(1, factors, out=factors)
            if settings.get("score_marginal", False):
                science_gain = expected_science_gains(factors, task.weight[near], state.best_score[near],
                                                       probabilities[:, near], mult, mismatch)
            else:
                near_cap = science_cap[near] if science_cap is not None else None
                power = exponent if np.ndim(exponent) == 0 else exponent[near]
                science_gain = science_gains(science_value(factors, power, near_cap), factors,
                                             state.factor_estimate[near], estimate_value[near], inc)
                science_gain *= task.weight[near]
                if upgrade > 0:
                    linear = np.maximum(
                        0, task.weight[near] * expected_top[near] * factors - state.best_score[near]) / top_multiplier
                    science_gain = np.where(complete[near][None, :], np.maximum(science_gain, upgrade * linear),
                                            science_gain)
            near_bonus = required_bonus if self.scarcity is None else required_bonus[near]
            safe = factors >= settings["required_safe_factor"]
            req_gain = incomplete[near][None, :] * safe * near_bonus
            request_gain = self._request_gains(request_terms, near, durations, factors, now)
            fairness = ((state.factor_lower[near][None, :] < threshold) & safe) * uniformity[near]
            # (science + required + request + fairness) * urgency * setting boost * terrain,
            # accumulated in place in that order.
            gains = science_gain
            gains += req_gain
            gains += request_gain
            gains += fairness
            gains *= urgency[near]
            gains *= setting_boost[near]
            gains *= self._terrain_open(durations, near, terrain_free)
            np.copyto(gains, 0, where=~(durations[:, None] <= setting_seconds[near][None, :]))
            # Targets without a positive gain never fill a fibre; dropping them before the
            # geometry leaves every fibre maximum and choice unchanged.
            useful = np.any(gains > 0, axis=tuple(range(gains.ndim - 1)))
            if not np.any(useful):
                continue
            near, gains = near[useful], gains[..., useful]
            fibers, margin, _, _ = task.grid.classify(vectors[near], ca, cz)
            geometry_weight = np.where(fibers >= 0, 1.0, 0.0)
            geometry_weight *= np.where(margin >= glass_margin, 1.0, 0.45)
            if settings.get("score_marginal", False):
                total = fiber_totals(fibers, gains.reshape(-1, len(near)), geometry_weight, task.grid.n)
                total = total.reshape(len(ca), len(PROGRAMS), len(durations))
            else:
                total = fiber_totals(fibers, gains, geometry_weight, task.grid.n)
            center_limit = self._centers_up(ca, cz, lst) if not relaxed else np.full(len(ca), 1e9)
            if settings.get("score_marginal", False):
                total = np.where(durations[None, None, :] <= center_limit[:, None, None], total, 0)
                merit = total / (durations[None, None, :] + overhead)
            else:
                total = np.where(durations[None, :] <= center_limit[:, None], total, 0)
                merit = total / (durations[None, :] + overhead)
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
            gain = gains[program_i, duration_i] * geometry_weight[center_i] if program_i is not None else gains[duration_i] * geometry_weight[center_i]
            selected = _fiber_choices(fibers[center_i], gain, near)
            candidate = {
                "merit": value,
                "alt": float(ca[center_i]),
                "az": float(cz[center_i]),
                "duration": int(durations[duration_i]),
                "selected": selected,
                "program_i": program_i,
                "_search": (near, gains, durations) if program_i is None else None,
            }
            finalists.append(candidate)
            finalists.sort(key=lambda entry: entry["merit"], reverse=True)
            del finalists[6:]
        refine = self._shrunk(int(settings.get("refine_top", 0)), 0)
        if refine > 0 and finalists:
            relaxed = getattr(task, "revision", 1) >= 2 and settings.get("free_center", True)
            floor = 0.0 if relaxed else task.minimum_altitude + 0.4
            for item in finalists[:refine]:
                for _ in range(max(1, int(settings.get("refine_iterations", 1)))):
                    before = item["merit"]
                    fields_checked += self._refine(item, vectors, glass_margin, lst, relaxed, floor)
                    if item["merit"] <= before:
                        break
            finalists.sort(key=lambda entry: entry["merit"], reverse=True)
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
                "sectors": tuple(sorted(pair for pair in self.quality.notices if pair[1] != "ALL")),
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
        rollout_status = self._apply_rollout(now, night_index, remaining_wallclock)
        action = self.alternatives[0]["action"]
        self.pending = self.alternatives[0]["pending"]
        selected = np.array([row["index"] for row in self.pending["targets"]], dtype=np.int32)
        self.last_trace = {
            "anchors": len(used),
            "fields": fields_checked,
            "scale_profile": self.scale_profile,
            "assignments": len(selected),
            "merit_per_second": round(self.alternatives[0]["merit"], 6),
            "quality_scale": round(scale, 5),
            "required_assigned": int(np.count_nonzero(incomplete[selected])),
            "request_assigned": int(np.count_nonzero(request_priority[selected])),
            "program": action["program"],
            "duration_seconds": action["duration_seconds"],
            "visible_targets": int(np.count_nonzero(visible)),
            **rollout_status,
        }
        return action

    TRACK_TIMES = (0.0, 300.0, 600.0, 900.0, 1350.0, 1800.0, 2700.0, 3600.0)

    def _track(self, now: float, altitude0, azimuth0, subset, horizon) -> dict:
        """Altitude and azimuth of the subset at fixed offsets after now; others keep now.

        ``horizon`` is task.horizon.take(subset).
        """
        task = self.task
        alt = np.repeat(np.asarray(altitude0)[None, :], len(self.TRACK_TIMES), axis=0)
        az = np.repeat(np.asarray(azimuth0)[None, :], len(self.TRACK_TIMES), axis=0)
        for j, offset in enumerate(self.TRACK_TIMES[1:], start=1):
            alt[j, subset], az[j, subset] = altaz(horizon.vectors(sidereal(now + offset, task.longitude)))
        return {"now": now, "alt": alt, "az": az}

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
        if profile is None:
            # 1 * f is f: the fallback row serves every duration.
            return (np.broadcast_to(fallback[indices], (len(durations), len(indices))) if fallback is not None
                    else np.ones((len(durations), len(indices))))
        result = np.ones((len(durations), len(indices)))
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
        # (1 - w) * low + w * high, in place.
        low, high = sub[j], sub[j + 1]
        low *= (1 - w)[:, None]
        high *= w[:, None]
        low += high
        return low

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
