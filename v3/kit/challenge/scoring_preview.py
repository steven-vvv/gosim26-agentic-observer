"""Participant-safe current-action estimates built from the public score contract."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence


PROGRAMS = ("DARK", "BRIGHT", "BACKUP")


@dataclass(frozen=True)
class CandidatePreview:
    """One legal current-snapshot action and its transparent ranking terms."""

    tile_id: str
    program: str
    request_id: str
    region_id: str
    scheduling_class: str
    nominal_exptime_seconds: int
    atmospheric_quality: float
    lunar_quality_factor: float
    combined_quality: float
    quality_band: str
    tile_science_value: float
    estimated_science_score: float
    terminal_penalty_avoidance: float
    request_policy_value: float
    estimated_total_gain: float
    estimated_gain_per_second: float
    estimate_semantics: str

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


def _number(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return moment


def _quality_band(quality: float, score_config: Mapping[str, object]) -> str:
    thresholds = score_config["quality_thresholds"]
    if quality >= _number(thresholds["dark"], "dark threshold"):
        return "DARK"
    if quality >= _number(thresholds["bright"], "bright threshold"):
        return "BRIGHT"
    return "BACKUP"


def _combined_quality(
    candidate: Mapping[str, object], weather_interface: Mapping[str, object]
) -> tuple[float, float, float]:
    weather = candidate["effective_weather"]
    geometry = candidate["geometry"]
    if not bool(weather["is_observable"]):
        return 0.0, _number(geometry["lunar_quality_factor"], "lunar factor"), 0.0
    airmass = _number(geometry["airmass"], "airmass")
    if airmass <= 0:
        raise ValueError("airmass must be positive")
    # Snapshots never carry instrument_efficiency: the preview baseline is the
    # efficiency-free public formula, so realized-score deviations isolate the
    # hidden instrument side (jitter x fault multiplier x tag multiplier).
    atmospheric = (
        _number(weather["transparency"], "transparency")
        * _number(weather["sky_quality"], "sky quality")
        / (
            _number(weather["seeing_arcsec"], "seeing")
            * airmass
            ** _number(weather_interface["airmass_exponent"], "airmass exponent")
        )
    )
    atmospheric = min(
        atmospheric,
        _number(weather_interface["maximum_weather_quality"], "maximum weather quality"),
    )
    lunar = _number(geometry["lunar_quality_factor"], "lunar factor")
    return atmospheric, lunar, atmospheric * lunar


def _request_options(
    snapshot: Mapping[str, object], tile_id: str
) -> list[tuple[str, float]]:
    options = []
    for request in snapshot.get("active_requests", []):
        if bool(request.get("is_complete", False)):
            continue
        matching = next(
            (
                item
                for item in request.get("tile_requirements", [])
                if str(item.get("tile_id")) == tile_id
                and int(item.get("remaining_visits", item.get("required_visits", 0))) > 0
            ),
            None,
        )
        if matching is None:
            continue
        remaining_tiles = max(
            1,
            int(request["required_tile_count"])
            - int(request.get("satisfied_tile_count", 0)),
        )
        eventual_delta = _number(request["completion_reward"], "request reward") + _number(
            request["miss_penalty"], "request miss penalty"
        )
        options.append((str(request["request_id"]), eventual_delta / remaining_tiles))
    return options


def _known_window_can_finish(
    snapshot: Mapping[str, object], candidate: Mapping[str, object]
) -> bool:
    start = _parse_utc(snapshot["cursor"]["timestamp_utc"])
    end = _parse_utc(candidate["window_end_utc"])
    exposure = int(candidate["nominal_exptime_seconds"])
    return start + timedelta(seconds=exposure) <= end


def preview_actions(
    snapshot: Mapping[str, object],
    scoring_contract: Mapping[str, object],
    tile_best_scores: Mapping[str, float] | None = None,
) -> list[CandidatePreview]:
    """Rank legal starts using public current state without reading future truth.

    Repeat observations of completed tiles are legal and bank the per-tile
    maximum. Their marginal science gain is `max(0, potential - banked best)`;
    callers that track realized bests (e.g. from snapshot feedback) pass them as
    `tile_best_scores`. Without that map the preview cannot know the banked
    best, so a repeat's estimated marginal gain is 0 — repeats still appear
    (observing one beats a penalised avoidable wait) but never outrank
    unfinished or request-valuable work.
    """
    if snapshot.get("schema_version") not in ("decision-snapshot-v2", "decision-snapshot-v3"):
        raise ValueError("unsupported decision snapshot schema_version")
    score_config = scoring_contract["score_config"]
    if score_config.get("schema_version") != "challenge-score-v3":
        raise ValueError("unsupported score config schema_version")
    weather_interface = scoring_contract["weather_score_interface"]
    penalties = score_config["penalties"]
    bonuses = score_config["program_bonus"]
    quota = int(score_config["flexible_quota_per_region"])
    best_scores = tile_best_scores or {}
    flexible_progress = snapshot.get("progress", {}).get(
        "flexible_completed_by_region", {}
    )
    result: list[CandidatePreview] = []
    for candidate in snapshot.get("candidate_tiles", []):
        weather = candidate["effective_weather"]
        if not bool(weather["is_observable"]) or not _known_window_can_finish(
            snapshot, candidate
        ):
            continue
        tile_id = str(candidate["tile_id"])
        already_completed = bool(candidate["already_completed"])
        request_options = _request_options(snapshot, tile_id)
        if already_completed and tile_best_scores is None and not request_options:
            # Pre-anomaly semantics: without a realized-best ledger a repeat is
            # never a candidate (and would be an invalid duplicate on the platform).
            continue
        action_options: Sequence[tuple[str, float]] = request_options or [("", 0.0)]
        atmospheric, lunar, combined = _combined_quality(
            candidate, weather_interface
        )
        band = _quality_band(combined, score_config)
        tile_value = _number(candidate["tile_science_value"], "tile science value")
        potential = tile_value * combined * (1.0 + _number(bonuses[band], "program bonus"))
        if already_completed:
            banked = best_scores.get(tile_id)
            science = 0.0 if banked is None else max(0.0, potential - _number(banked, "banked best score"))
        else:
            science = potential
        terminal_avoidance = 0.0
        if not already_completed and candidate["scheduling_class"] == "REQUIRED":
            terminal_avoidance = _number(
                penalties["required_miss"], "required miss penalty"
            )
        elif (
            not already_completed
            and candidate["scheduling_class"] == "FLEXIBLE"
            and int(flexible_progress.get(str(candidate["region_id"]), 0)) < quota
        ):
            terminal_avoidance = _number(
                penalties["flexible_shortfall_per_tile"],
                "flexible shortfall penalty",
            )
        exposure = int(candidate["nominal_exptime_seconds"])
        if exposure <= 0:
            raise ValueError("nominal exposure must be positive")
        for request_id, request_value in action_options:
            total = science + terminal_avoidance + request_value
            result.append(
                CandidatePreview(
                    tile_id=tile_id,
                    program=band,
                    request_id=request_id,
                    region_id=str(candidate["region_id"]),
                    scheduling_class=str(candidate["scheduling_class"]),
                    nominal_exptime_seconds=exposure,
                    atmospheric_quality=round(atmospheric, 6),
                    lunar_quality_factor=round(lunar, 6),
                    combined_quality=round(combined, 6),
                    quality_band=band,
                    tile_science_value=round(tile_value, 6),
                    estimated_science_score=round(science, 6),
                    terminal_penalty_avoidance=round(terminal_avoidance, 6),
                    request_policy_value=round(request_value, 6),
                    estimated_total_gain=round(total, 6),
                    estimated_gain_per_second=round(total / exposure, 9),
                    estimate_semantics=(
                        "official formula with current conditions held constant; "
                        "repeat observations show the marginal gain over the caller-supplied "
                        "banked best (0 while bests are untracked); "
                        "request value is apportioned over remaining required tiles; "
                        "authoritative replay may differ after future weather changes"
                    ),
                )
            )
    result.sort(
        key=lambda item: (
            -item.estimated_gain_per_second,
            -item.estimated_total_gain,
            item.tile_id,
            item.request_id,
            item.program,
        )
    )
    return result

