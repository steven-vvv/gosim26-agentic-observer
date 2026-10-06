"""Fault reports require a sustained public drop and explicit guard settings."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from v4.agent.observer4.config import load
from v4.agent.observer4.quality import QualityModel

CONFIG = Path(__file__).resolve().parents[1] / "agent/config.json"


def test_persistent_fault_candidate_respects_guard_settings():
    config = load(CONFIG, {"fault": {"forecast_guard": True, "quake_cooldown": True}})
    task = SimpleNamespace(
        nights=[SimpleNamespace(date=f"2026-10-0{i}") for i in range(1, 4)],
        scoring={"program": {"bands": {"DARK": 0.65}}},
    )
    quality = QualityModel(task, config)
    for i in range(48):
        quality.clean.append((float(i), 0, 0.8))
    for i in range(48, 96):
        quality.clean.append((float(i), 1 if i < 72 else 2, 0.3))
    for i in range(90, 94):
        quality.band_checks.append((float(i), 2, "DARK", True, 1.2))
    quality.forecast_weather_nights.add(task.nights[2].date)
    quality.quake_until = 200.0

    assert quality.report_evidence(96.0) is None
    config["fault"]["quake_cooldown"] = False
    assert quality.report_evidence(96.0) is None
    config["fault"]["forecast_guard"] = False
    evidence = quality.report_evidence(96.0)
    assert evidence is not None and evidence["drop"] == pytest.approx(0.375)
    assert evidence["dark_matches"] == 4


def test_old_config_without_optional_fault_guards_stays_valid():
    legacy = CONFIG.parents[1] / "experiments/config-r10.json"
    assert load(legacy)["fault"]["minimum_samples"] == 24
    with pytest.raises(ValueError, match="fault.forecast_guard"):
        load(CONFIG, {"fault": {"forecast_guard": 0}})


def test_scale_falls_back_to_the_prior_once_samples_are_stale():
    config = load(CONFIG, {"quality": {"stale_hours": 36}})
    quality = QualityModel(SimpleNamespace(nights=[], scoring={}), config)
    for i in range(20):
        quality.samples.append((float(i), 0.09, True))
    hour = 3600.0
    assert quality.scale(20.0 + 24 * hour) == pytest.approx(0.09)
    assert quality.scale(20.0 + 40 * hour) == pytest.approx(config["quality"]["prior"])
    config["quality"]["stale_hours"] = 0
    assert quality.scale(20.0 + 40 * hour) == pytest.approx(0.09)
