"""Validated public configuration, shared by runtime and paired experiments."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path


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


def load(path: Path, patch: dict | None = None) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if patch:
        config = merge(config, patch)
    validate(config)
    return config


def validate(config: dict) -> None:
    if config.get("schema") != "gosim26.v4.config.v1":
        raise ValueError("unsupported v4 configuration schema")
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
            "max_total_calls": (0, 20),
            "max_stage_calls": (0, 10),
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
