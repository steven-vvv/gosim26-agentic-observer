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
            else:
                result[key] = merge(result[key], value)
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
            "neighbor_radius_deg": (3.5, 10),
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
    for name, (low, high) in {"request_weight": (0, 20), "pace_floor": (0, 1)}.items():
        if name in config["policy"]:
            value = config["policy"][name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"invalid configuration field: policy.{name}")
    for name, (low, high) in {"rollout_top": (0, 2), "rollout_weight": (0, 2)}.items():
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
    for name in ("forecast_guard", "quake_cooldown"):
        if name in config["fault"] and type(config["fault"][name]) is not bool:
            raise ValueError(f"boolean configuration required for fault.{name}")
