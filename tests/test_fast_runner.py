"""The local vector kernel must preserve the official geometry semantics."""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "kit"))

from challenge import tile_geometry_simulator as official

from tools.vector_geometry import FIELDS, BatchGeometry


def test_batch_geometry_matches_official_samples() -> None:
    scenario = ROOT / "kit" / "scenarios" / "dev-reference"
    reference = scenario / "outputs" / "reference"
    config = scenario / "config"
    simulator = official.TileGeometrySimulator.from_files(
        reference / "tiles.csv", config / "tile_config.json", config / "calendar_config.json",
        reference / "night_calendar.csv", reference / "slots.csv",
    )
    vector = BatchGeometry(simulator)
    tiles = list(simulator.tiles.values())
    slots = [slot for night in simulator.slots_by_night.values() for slot in night]
    minimum = float(simulator.tile_config["geometry"]["minimum_altitude_deg"])
    lunar = simulator.tile_config["lunar_model"]
    site = simulator.calendar_config["site"]

    for slot in (slots[0], slots[len(slots) // 2], slots[-1]):
        moment = slot.timestamp_utc + (slot.end_utc - slot.timestamp_utc) / 2
        sidereal = official._local_sidereal_deg(moment, float(site["longitude_deg"]))
        sun_ra, sun_dec = official._sun_equatorial_deg(moment)
        moon_ra, moon_dec = official._moon_equatorial_deg(moment)
        sun_moon = official._angular_separation_deg(sun_ra, sun_dec, moon_ra, moon_dec)
        illumination = (1.0 - math.cos(math.radians(sun_moon))) / 2.0
        moon_tile = replace(tiles[0], ra_deg=moon_ra, dec_deg=moon_dec)
        moon_altitude = official.geometry_sample_without_lunar(moon_tile, moment, simulator.calendar_config)["altitude_deg"]
        altitude_weight = math.sin(math.radians(max(0.0, moon_altitude))) ** float(lunar["altitude_exponent"])
        terms = (sidereal, moon_ra, moon_dec,
                 float(lunar["maximum_penalty"]) * illumination * altitude_weight,
                 float(lunar["angular_decay_scale_deg"]))
        batch = vector.at(terms)

        for tile in tiles:
            expected = official.geometry_sample(tile, moment, simulator.tile_config, simulator.calendar_config)
            actual = vector.sample(batch, vector.index[id(tile)])
            assert set(actual) == set(FIELDS)
            assert (actual["altitude_deg"] >= minimum) == (expected["altitude_deg"] >= minimum)
            for field in FIELDS:
                if math.isinf(expected[field]):
                    assert math.isinf(actual[field])
                else:
                    assert math.isclose(actual[field], expected[field], rel_tol=1e-12, abs_tol=1e-10), (
                        tile.tile_id, slot.slot_id, field, actual[field], expected[field]
                    )
