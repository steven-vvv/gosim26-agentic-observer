"""Validated public configuration, shared by runtime and paired experiments."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

# Profiles change only public knobs that already have deterministic semantics.  The
# conservative release keeps the feedback/instrument models (the useful data source)
# enabled while shortening science exposures and disabling expensive refinement that
# makes the submitted score look like the tuned practice score.
PROFILE_PATCHES = {
    "full": {},
    "formal_conservative": {
        "policy": {
            "anchors": 16,
            "anchor_pool": 160,
            "cluster_anchors": 4,
            "refine_top": 0,
            "refine_centers": False,
            "adaptive_durations": False,
            "durations": [300, 450, 600, 900],
            "required_bonus": 120.0,
            "uniformity_weight": 0.5,
        },
        "model": {
            "max_total_calls": 12,
            "max_stage_calls": 8,
        },
        "scale": {
            "anchor_pool": 24,
            "anchors": 4,
            "cluster_anchors": 2,
            "refine_top": 0,
            "durations": [300, 450, 600, 900],
        },
    },
}


# Policy fields a card-structure group may override (structure.<group>_overrides).  The bounds are
# those of the same policy field (test_structure_override_bounds_match_policy_bounds); the knobs the
# policy section leaves unchecked get the bounds given here.  The integer knobs must be ints.
OVERRIDE_BOUNDS = {
    "setting_boost": (0, 2), "rising_weight": (0, 1), "upgrade_weight": (0, 2), "upgrade_factor": (0.5, 1),
    "season_weight": (0, 2), "window_weight": (0, 2), "science_exponent": (1, 4),
    "science_horizon_hours": (0, 2000), "level_safety_z": (0, 5), "deadline_weight": (0, 20),
    "uniformity_weight": (0, 10), "quality_safety": (0.3, 1.5), "required_bonus": (0, 500),
    "neighbor_radius_deg": (1.0, 10), "cluster_radius_deg": (0.1, 5), "glass_margin_deg": (0, 0.25),
    "program_uncertainty_floor": (0, 1), "request_weight": (0, 100), "science_cap_quality": (0, 2),
    "anchors": (1, 128), "anchor_pool": (1, 4096), "cluster_anchors": (0, 128), "refine_top": (0, 32),
    "refine_iterations": (1, 8), "pace_shrink": (0, 1), "revisit_increment": (0, 1),
    "overexposure_gamma": (0, 4), "choice_gamma": (0, 4),
}
OVERRIDE_INTEGERS = frozenset({"anchors", "anchor_pool", "cluster_anchors", "refine_top", "refine_iterations"})
STRUCTURE_GROUPS = ("late_overrides", "rich_overrides", "lean_overrides", "very_lean_overrides", "mid_overrides")
# Keys of the structure section that a configuration may omit; a patch may add them.
STRUCTURE_OPTIONAL = frozenset({"lean_fiber_hours", "lean_overrides", "very_lean_overrides", "mid_overrides",
                                "lean_split_fiber_hours"})


def profile_patch(name: str) -> dict:
    try:
        return copy.deepcopy(PROFILE_PATCHES[name])
    except KeyError as error:
        raise ValueError(f"unknown release profile: {name}") from error


def merge(base: dict, patch: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if key not in result:
            raise ValueError(f"unknown configuration field: {key}")
        if isinstance(result[key], dict) and isinstance(value, dict):
            if key == "events":
                result[key].update(value)
            elif key == "structure":
                result[key] = merge_structure(result[key], value)
            else:
                result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_structure(base: dict, patch: dict) -> dict:
    """Merge a structure patch: it may add the optional keys, and an override group takes new fields.

    The fields of a group are bounded by validate(), not by what the file it patches already holds.
    """
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if key not in result and key not in STRUCTURE_OPTIONAL:
            raise ValueError(f"unknown configuration field: {key}")
        if key in STRUCTURE_GROUPS and isinstance(value, dict) and isinstance(result.get(key, {}), dict):
            result[key] = {**result.get(key, {}), **copy.deepcopy(value)}
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve(config: dict, patch: dict | None = None) -> dict:
    """Apply a named profile, then a bounded override, and validate the result."""
    base = copy.deepcopy(config)
    requested = patch or {}
    release = requested.get("release") if isinstance(requested, dict) else None
    profile = release.get("profile") if isinstance(release, dict) else None
    profile = profile or base.get("release", {}).get("profile", "full")
    result = merge(base, profile_patch(profile))
    if patch:
        result = merge(result, patch)
    validate(result)
    return result


def load(path: Path, patch: dict | None = None, *, resolve_profile: bool = True) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate(config)
    if not resolve_profile:
        if patch:
            raise ValueError("patches require resolve_profile=True")
        return config
    return resolve(config, patch)


def validate(config: dict) -> None:
    if config.get("schema") != "gosim26.v4.config.v1":
        raise ValueError("unsupported v4 configuration schema")
    release = config.get("release", {})
    if not isinstance(release, dict):
        raise TypeError("release configuration must be an object")
    profile = release.get("profile", "full")
    if not isinstance(profile, str) or profile not in PROFILE_PATCHES:
        raise ValueError("invalid release profile")
    remote = release.get("remote_config", {})
    if not isinstance(remote, dict):
        raise TypeError("release.remote_config must be an object")
    for name in ("enabled", "required", "locked"):
        if name in remote and type(remote[name]) is not bool:
            raise ValueError(f"boolean configuration required for release.remote_config.{name}")
    if "auth" in remote and remote["auth"] not in {"token", "public"}:
        raise ValueError("release.remote_config.auth must be 'token' or 'public'")
    if "url" in remote and (not isinstance(remote["url"], str) or len(remote["url"]) > 512):
        raise ValueError("invalid release.remote_config.url")
    if "config_id" in remote and (not isinstance(remote["config_id"], str) or not remote["config_id"].strip()):
        raise ValueError("invalid release.remote_config.config_id")
    if "timeout_seconds" in remote:
        value = remote["timeout_seconds"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.1 <= value <= 10:
            raise ValueError("invalid release.remote_config.timeout_seconds")
    ranges = {
        "policy": {
            "anchors": (1, 128),
            "anchor_pool": (1, 4096),
            "neighbor_radius_deg": (1.0, 10),
            "science_exponent": (1, 4),
            "required_bonus": (0, 500),
            "required_safe_factor": (0.5, 1),
            "quality_safety": (0.3, 1.5),
            "deadline_weight": (0, 20),
            "density_weight": (0, 10),
            "uniformity_weight": (0, 10),
            "altitude_margin_deg": (0.001, 5),
            "glass_margin_deg": (0, 0.25),
            "budget_reserve_seconds": (0, 60),
        },
        "quality": {
            "prior": (0.05, 2),
            "memory_hours": (0.05, 48),
            "program_uncertainty": (0.01, 1),
            "instrument_prior": (0.1, 1),
        },
        "fault": {
            "drop_threshold": (0.1, 0.99),
            "minimum_samples": (4, 1000),
            "confirm_nights": (1, 10),
            "maximum_reports": (0, 20),
            "cooldown_hours": (1, 240),
        },
        "model": {
            "timeout_seconds": (0.2, 30),
            "max_total_calls": (0, 64),
            "max_stage_calls": (0, 32),
            "max_tokens": (16, 8192),
            "minimum_budget_seconds": (0, 900),
            "max_consecutive_failures": (1, 20),
            "min_alternative_probability": (0, 1),
            "action_merit_tolerance": (0, 0.25),
        },
        "observability": {"decision_every": (1, 10000), "checkpoint_every": (1, 10000)},
    }
    for section, fields in ranges.items():
        for name, (low, high) in fields.items():
            value = config[section][name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError(f"invalid configuration field: {section}.{name}")
    for section, fields in {
        "policy": ("anchors", "anchor_pool"),
        "fault": ("minimum_samples", "confirm_nights", "maximum_reports"),
        "model": ("max_total_calls", "max_stage_calls", "max_tokens", "max_consecutive_failures"),
        "observability": ("decision_every", "checkpoint_every"),
    }.items():
        if any(type(config[section][name]) is not int for name in fields):
            raise ValueError(f"integer configuration required in {section}")
    durations = config["policy"]["durations"]
    if not isinstance(durations, list) or not durations or any(type(x) is not int or not 60 <= x <= 3600 for x in durations):
        raise ValueError("policy.durations must contain integer seconds within 60..3600")
    config["policy"]["durations"] = sorted(set(durations))
    for section, fields in {
        "policy": ("refine_centers", "adaptive_durations"),
        "quality": ("directional_notices", "weather_feedback"),
        "fault": ("enabled",),
        "model": ("enabled",),
        "observability": ("local_enabled",),
    }.items():
        if any(type(config[section][name]) is not bool for name in fields):
            raise ValueError(f"boolean configuration required in {section}")
    if "score_marginal" in config["policy"] and type(config["policy"]["score_marginal"]) is not bool:
        raise ValueError("boolean configuration required for policy.score_marginal")
    if "scarcity_weight" in config["policy"]:
        value = config["policy"]["scarcity_weight"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError("invalid configuration field: policy.scarcity_weight")
    for name, (low, high) in {"request_weight": (0, 100), "pace_floor": (0, 1)}.items():
        if name in config["policy"]:
            value = config["policy"][name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid configuration field: policy.{name}")
    for name, (low, high) in {"rollout_top": (0, 2), "rollout_weight": (0, 2), "setting_boost": (0, 2),
                              "rising_weight": (0, 1), "upgrade_weight": (0, 2), "upgrade_factor": (0.5, 1),
                              "season_weight": (0, 2), "window_weight": (0, 2),
                              "revisit_increment": (0, 1)}.items():
        if name in config["policy"]:
            value = config["policy"][name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid configuration field: policy.{name}")
    if "rollout_top" in config["policy"] and type(config["policy"]["rollout_top"]) is not int:
        raise ValueError("integer configuration required in policy.rollout_top")
    # The scale profile was added after the first public configs. Keep those
    # configs loadable while validating every field when the profile exists.
    if "scale" in config:
        scale = config["scale"]
        if not isinstance(scale, dict):
            raise ValueError("scale configuration must be an object")
        scale_ranges = {
            "cost_threshold": (1, 100_000_000),
            "anchor_pool": (1, 4096),
            "anchors": (1, 128),
            "cluster_anchors": (0, 128),
            "refine_top": (0, 64),
        }
        for name, (low, high) in scale_ranges.items():
            if name not in scale:
                raise ValueError(f"missing configuration field: scale.{name}")
            value = scale[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError(f"invalid configuration field: scale.{name}")
        if any(type(scale[name]) is not int for name in scale_ranges):
            raise ValueError("integer configuration required in scale")
        if type(scale.get("enabled")) is not bool:
            raise ValueError("boolean configuration required in scale")
        scale_durations = scale.get("durations")
        if (
            not isinstance(scale_durations, list)
            or not scale_durations
            or any(type(value) is not int or not 60 <= value <= 3600 for value in scale_durations)
        ):
            raise ValueError("scale.durations must contain integer seconds within 60..3600")
        scale["durations"] = sorted(set(scale_durations))
    if "structure" in config:
        structure = config["structure"]
        if not isinstance(structure, dict) or type(structure.get("enabled")) is not bool:
            raise ValueError("structure configuration must be an object with a boolean enabled")
        for name, (low, high) in {"late_fraction": (0, 1), "late_share_threshold": (0, 1),
                                  "rich_fiber_hours": (0, 1e9), "lean_fiber_hours": (0, 1e9),
                                  "lean_split_fiber_hours": (0, math.inf)}.items():
            if name in STRUCTURE_OPTIONAL and name not in structure:
                continue
            value = structure.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid configuration field: structure.{name}")
        for name in STRUCTURE_GROUPS:
            if name in STRUCTURE_OPTIONAL and name not in structure:
                continue
            overrides = structure.get(name)
            if not isinstance(overrides, dict):
                raise ValueError(f"invalid configuration field: structure.{name}")  # noqa: TRY004
            for field, value in overrides.items():
                if field not in OVERRIDE_BOUNDS:
                    raise ValueError(f"invalid configuration field: structure.{name}.{field}")
                low, high = OVERRIDE_BOUNDS[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                        or not low <= value <= high:
                    raise ValueError(f"invalid configuration field: structure.{name}.{field}")
                if field in OVERRIDE_INTEGERS and type(value) is not int:
                    raise ValueError(f"integer configuration required in structure.{name}.{field}")
    for name in ("forecast_guard", "quake_cooldown"):
        if name in config["fault"] and type(config["fault"][name]) is not bool:
            raise ValueError(f"boolean configuration required for fault.{name}")
    if "stale_hours" in config["quality"]:
        value = config["quality"]["stale_hours"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 2000:
            raise ValueError("invalid configuration field: quality.stale_hours")
    for name, (low, high) in {"stale_levels": (1, 50), "stale_floor": (0, 10),
                              "stale_max_hours": (0, 2000)}.items():
        if name in config["fault"]:
            value = config["fault"][name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid configuration field: fault.{name}")
    if "stale_levels" in config["fault"] and type(config["fault"]["stale_levels"]) is not int:
        raise ValueError("integer configuration required for fault.stale_levels")
    if "logbook" in config:
        logbook = config["logbook"]
        if not isinstance(logbook, dict) or any(type(logbook.get(name)) is not bool
                                                for name in ("enabled", "reports", "terrain", "rules")):
            raise ValueError("logbook configuration must be an object with boolean switches")
        for name, (low, high) in {"min_chars": (1, 10000), "max_chars": (100, 60000), "max_calls": (0, 1000),
                                  "per_decision": (1, 20), "timeout_seconds": (0.2, 30), "max_tokens": (64, 4096),
                                  "max_consecutive_failures": (1, 50), "merge_hours": (0, 48),
                                  "terrain_margin_deg": (0, 10)}.items():
            value = logbook.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid configuration field: logbook.{name}")
        if any(type(logbook[name]) is not int for name in ("min_chars", "max_chars", "max_calls", "per_decision",
                                                           "max_tokens", "max_consecutive_failures")):
            raise ValueError("integer configuration required in logbook")
