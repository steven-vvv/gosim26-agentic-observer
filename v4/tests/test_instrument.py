"""Exposure levels, fault detection and terrain posterior on synthetic public evidence."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

from v4.agent.observer4.instrument import InstrumentMonitor, exposure_level
from v4.agent.observer4.planning import fiber_totals
from v4.agent.observer4.terrain import TerrainModel

MULTIPLIERS = {"DARK": 1.20, "BRIGHT": 1.12, "BACKUP": 1.06}
ZERO_POINT = 450.0


def _scores(level: float, fluxes, ks, duration, multiplier, weights):
    factor = np.minimum(1.0, np.asarray(fluxes) * duration * math.exp(level) * np.asarray(ks) / ZERO_POINT)
    return np.asarray(weights) * factor * multiplier


def test_exposure_level_recovers_log_efficiency_times_sky():
    fluxes, ks, weights = [0.3, 0.4, 0.5, 4.0], [1.1, 1.2, 1.0, 1.1], [1.0, 1.0, 1.7, 0.3]
    scores = _scores(math.log(0.55), fluxes, ks, 600, MULTIPLIERS["DARK"], weights)
    level, samples, zero, matched = exposure_level(scores, weights, fluxes, ks, 600, "DARK", MULTIPLIERS, 1.0,
                                                   ZERO_POINT)
    assert abs(level - math.log(0.55)) < 1e-9
    assert samples == 3 and not zero and matched is True  # the star saturates at the declared multiplier


def test_exposure_level_splits_matched_and_unmatched_groups():
    fluxes, ks, weights = np.array([0.3, 0.4, 0.35, 0.45]), np.ones(4), np.ones(4)
    scores = np.concatenate((_scores(math.log(0.6), fluxes[:2], ks[:2], 300, 1.2, weights[:2]),
                             _scores(math.log(0.6), fluxes[2:], ks[2:], 300, 1.0, weights[2:])))
    level, samples, _, _ = exposure_level(scores, weights, fluxes, ks, 300, "DARK", MULTIPLIERS, 1.0, ZERO_POINT)
    assert samples == 4 and abs(level - math.log(0.6)) < 1e-9


def test_exposure_level_all_zero_is_flagged():
    level, samples, zero, _ = exposure_level(np.zeros(3), np.ones(3), np.ones(3), np.ones(3), 300, "DARK",
                                             MULTIPLIERS, 1.0, ZERO_POINT)
    assert level is None and samples == 0 and zero


def _task(nights=40, slot=900.0):
    rows = [SimpleNamespace(start=86400.0 * k, end=86400.0 * k + 36 * slot) for k in range(nights)]
    task = SimpleNamespace(nights=rows, slot_seconds=slot, scoring={
        "program": {"bands": {"DARK": 0.65, "BRIGHT": 0.40}, "multipliers": MULTIPLIERS, "mismatch_multiplier": 1.0},
        "reporting": {"false_report_free_allowance": 2}})
    task.current_night = lambda t: next((i for i, n in enumerate(rows) if n.start <= t < n.end), None)
    return task


def _record(start, duration, night, level, signature=frozenset()):
    return {"start": start, "end": start + duration, "night": night, "level": level, "zero": False,
            "signature": signature, "samples": 8, "matched": None, "mixed": False}


def test_monitor_reports_persistent_step_inside_the_fault_window():
    task = _task()
    monitor = InstrumentMonitor(task, {"enabled": True}, revision=2)
    night = 20  # slot 720, inside the window around the survey midpoint
    start = task.nights[night].start
    for i in range(6):
        monitor.add(_record(start + 600 * i, 600, night, math.log(0.6)))
    assert monitor.decide(start + 3600, night) is None
    for i in range(6, 10):
        monitor.add(_record(start + 600 * i, 600, night, math.log(0.6 * 0.45)))
    evidence = monitor.decide(start + 6000, night)
    assert evidence is not None and evidence["kind"] == "step"
    assert abs(evidence["drop"] - math.log(0.45)) < 0.05


def test_monitor_ignores_a_dome_closure_dilution():
    task = _task()
    monitor = InstrumentMonitor(task, {"enabled": True}, revision=2)
    night = 20
    start = task.nights[night].start
    for i in range(6):
        monitor.add(_record(start + 450 * i, 450, night, math.log(0.6)))
    # One closed slot [2700, 3600) halves two 900 s exposures that overlap it by 450 s.
    monitor.add(_record(start + 2250, 900, night, math.log(0.6 * 0.5)))
    monitor.add(_record(start + 3150, 900, night, math.log(0.6 * 0.5)))
    assert monitor._step() is None


def test_monitor_does_not_report_before_the_window_without_strong_evidence():
    task = _task()
    monitor = InstrumentMonitor(task, {"enabled": True}, revision=1)
    night = 2
    start = task.nights[night].start
    for i in range(10):
        monitor.add(_record(start + 600 * i, 600, night, math.log(0.6 if i < 6 else 0.3)))
    assert monitor.decide(start + 6000, night) is None


def test_fiber_totals_takes_best_target_per_fiber():
    fibers = np.array([[0, 0, 1, -1]])
    gains = np.array([[1.0, 3.0, 2.0, 9.0]])
    weight = np.ones((1, 4))
    assert fiber_totals(fibers, gains, weight, 16).tolist() == [[5.0]]


def test_terrain_posterior_concentrates_on_the_true_sector():
    model = TerrainModel(["E"], {})
    rng = np.random.default_rng(0)
    tracks, observations = [], []
    for _ in range(200):
        az, alt = rng.uniform(40, 150), rng.uniform(30, 50)
        blocked = abs(az - 100) <= 20 and alt <= 42
        tracks.append((np.array([az, az + 0.5]), np.array([alt, alt + 0.3])))
        observations.append(("fraction", 0.0 if blocked else 1.0))
    model.update(tracks, observations)
    inside = model.blocked_probability(np.array([100.0, 95.0]), np.array([35.0, 40.0]))
    outside = model.blocked_probability(np.array([60.0, 140.0, 100.0]), np.array([35.0, 35.0, 47.0]))
    assert np.all(inside > 0.9) and np.all(outside < 0.1)
