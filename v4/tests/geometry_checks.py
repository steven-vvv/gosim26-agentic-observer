"""Parity checks against the pinned organizer geometry and ledger in a clean process."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from v4.agent.observer4.geometry import FiberGrid, horizontal_vectors, sidereal
from v4.evaluation.acceleration import fast_runner
from v4.evaluation.upstream import resolve


@pytest.fixture(scope="module")
def upstream():
    resolve()
    from challenge import v4_fiber_map, v4_scorer

    return v4_fiber_map, v4_scorer


def test_fiber_classification_matches_upstream(upstream):
    official, _ = upstream
    rng = random.Random(83)
    instrument = {"grid_side": 4, "n_fibers": 16, "fiber_area_deg2": 0.75, "gap_deg": 0.1}
    ours = FiberGrid(instrument)
    theirs = official.FiberGrid(instrument["fiber_area_deg2"], instrument["gap_deg"], 16)
    for _ in range(120):
        alt, az = rng.uniform(25, 80), rng.uniform(0, 360)
        centers_alt, centers_az = ours.centers_for_target(alt, az)
        target = np.array(
            [
                [
                    np.cos(np.deg2rad(alt)) * np.cos(np.deg2rad(az)),
                    np.cos(np.deg2rad(alt)) * np.sin(np.deg2rad(az)),
                    np.sin(np.deg2rad(alt)),
                ]
            ]
        )
        fiber, margin, _, _ = ours.classify(target, centers_alt, centers_az)
        assert len(fiber) == 16
        assert fiber[:, 0].tolist() == list(range(16))
        assert np.allclose(margin[:, 0], ours.glass / 2, atol=1e-10)
        for center_alt, center_az in zip(centers_alt, centers_az):
            north, east = theirs.tangent_offsets(alt, az, center_alt, center_az)
            assert theirs.classify_offset(north, east)[1] == official.REGION_GLASS


def test_horizontal_vectors_match_upstream(upstream):
    official, _ = upstream
    rng = random.Random(217)
    moment = datetime(2026, 10, 14, 12, 34, tzinfo=timezone.utc)
    lat, lon = 31.0, -110.0
    for _ in range(200):
        ra, dec = rng.uniform(0, 360), rng.uniform(-70, 80)
        ours = horizontal_vectors(ra, dec, sidereal(moment.timestamp(), lon), lat)
        alt, az = official.radec_to_altaz(ra, dec, moment, lat, lon)
        expected = np.array(
            [
                np.cos(np.deg2rad(alt)) * np.cos(np.deg2rad(az)),
                np.cos(np.deg2rad(alt)) * np.sin(np.deg2rad(az)),
                np.sin(np.deg2rad(alt)),
            ]
        )
        assert np.allclose(ours, expected, atol=1e-10)


def test_accelerated_altitude_minimum_matches_sampled_engine(upstream):
    official, _ = upstream
    rng = random.Random(178)
    accelerated = fast_runner()
    config = {"site": {"latitude_deg": 31.0, "longitude_deg": -110.0}}
    origin = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for _ in range(500):
        ra, dec = rng.uniform(0, 360), rng.uniform(-80, 80)
        start = origin + timedelta(seconds=rng.randrange(70 * 86400))
        end = start + timedelta(seconds=rng.randint(1, 3900))
        for step in (60, 120):
            expected = official.min_altitude_during(ra, dec, start, end, config, step)
            actual = accelerated.min_altitude_during(ra, dec, start, end, config, step)
            assert actual == pytest.approx(expected, abs=1e-10)


def test_incremental_ledger_matches_official_after_invalidation(upstream):
    _, official = upstream
    rng = random.Random(914)
    baseline, accelerated = official.BestLedger(), fast_runner().BestLedger()
    for action in range(200):
        for _ in range(rng.randrange(5)):
            target = f"T{rng.randrange(60):03d}"
            factor, score = rng.random(), rng.random() * 25
            baseline.record(action, target, factor, score)
            accelerated.record(action, target, factor, score)
        if action % 23 == 0:
            first = max(0, action - 11)
            assert baseline.invalidate_window(first, action + 1, "fault") == accelerated.invalidate_window(
                first, action + 1, "fault"
            )
        assert baseline.best() == accelerated.best()
        assert baseline.max_factors() == accelerated.max_factors()


def test_incremental_ledger_preserves_exposure_windows_for_requests(upstream):
    _, official = upstream
    baseline, accelerated = official.BestLedger(), fast_runner().BestLedger()
    issued = datetime(2026, 10, 5, tzinfo=timezone.utc)
    deadline = issued + timedelta(minutes=10)
    for ledger in (baseline, accelerated):
        ledger.record(0, "T0", 0.7, 1.0, issued - timedelta(seconds=1), issued + timedelta(minutes=2))
        ledger.record(1, "T0", 0.6, 0.9, issued, issued + timedelta(minutes=5))
        ledger.record(2, "T1", 0.8, 1.0, issued + timedelta(minutes=8), deadline + timedelta(seconds=1))
        ledger.record(3, "T2", 0.8, 1.0, issued + timedelta(minutes=1), deadline)
    assert accelerated.request_factors(issued, deadline) == baseline.request_factors(issued, deadline) == {
        "T0": 0.6, "T2": 0.8,
    }
    for ledger in (baseline, accelerated):
        ledger.invalidate_window(1, 2, "loss")
    assert accelerated.request_factors(issued, deadline) == baseline.request_factors(issued, deadline) == {
        "T2": 0.8,
    }


def test_accelerated_runner_catches_workflow_termination(upstream):
    resolve()
    from challenge import v4_runner

    assert fast_runner().AgentTermination is v4_runner.AgentTermination
