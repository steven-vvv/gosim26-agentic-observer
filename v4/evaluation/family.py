"""Randomized cards drawn from the organizer card family inferred from eight published cards.

The registered practice cards alpha-delta and the local cards L1-L4 share one recipe that
differs from the reference generator configs used by ``generate.build_card_bundle``:

* three footprint components placed by season: centres at the mid-season midnight LST
  plus about -45, +5 and +50 deg (declinations near -22, -52 and -15), area weights
  0.40/0.35/0.30, 5 targets per square degree, 5 % required (6 % on the extreme card);
* cluster centres scaled with the area (150 per 6000 deg2), sigma 0.5 deg;
* about a third of the reference weather-event counts (cloudy 3-4, rainy 2-3, smoggy 1-2,
  cold wave 1-2, tornado 0-1, rocket 1-2, earthquake 1-2), event shapes unchanged;
* one instrument fault with efficiency multiplier within [0.4, 0.75];
* stress cards with data-loss windows of 4.5-7 % of the actions.

Each card draws its parameters from its own seed, so a dataset is a sample of the family
rather than a fixed grid. The fourth card of every published set is an extreme card
(fewer observable hours, more disruptions); ``extreme_fraction`` of the cards follow it.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path


def _rng(seed: int, stream: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{stream}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _sidereal_deg(moment_days_since_j2000: float, longitude: float) -> float:
    return (280.46061837 + 360.98564736629 * moment_days_since_j2000 + longitude) % 360.0


def mid_season_lst(start: date, nights: int, longitude: float = -70.3976) -> float:
    """LST at the local apparent solar midnight of the middle night."""
    middle = start + timedelta(days=nights // 2 + 1)
    b = math.radians(360.0 * (middle.timetuple().tm_yday - 81) / 365.0)
    equation_of_time = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)  # minutes
    utc_hours = -longitude / 15.0 - equation_of_time / 60.0  # midnight in UTC hours on that date
    days = (middle - date(2000, 1, 1)).days - 0.5 + utc_hours / 24.0
    return _sidereal_deg(days, longitude)


def family_spec(name: str, seed: int, settings: dict) -> dict:
    """Draw one card specification of the family; ``settings`` may narrow the ranges."""
    rng = _rng(seed, "family-spec")
    first, last = (date.fromisoformat(value) for value in settings.get("start_range", ["2026-10-01", "2026-11-25"]))
    start = first + timedelta(days=rng.randint(0, (last - first).days))
    nights = rng.randint(*settings.get("nights_range", [32, 38]))
    extreme = rng.random() < float(settings.get("extreme_fraction", 0.25))
    stress = extreme or rng.random() < float(settings.get("stress_fraction", 1.0 / 3.0))
    area = 20.0 * round(rng.uniform(*settings.get("area_range", [1500.0, 2000.0])) / 20.0)
    lst = mid_season_lst(start, nights)
    offsets = [(-45.0, -22.0, 6.0), (5.0, -52.0, 5.0), (50.0, -15.0, 4.0)]
    centers = [[round((lst + ra + rng.uniform(-10.0, 10.0)) % 360.0, 2),
                round(dec + rng.uniform(-spread, spread), 2)] for ra, dec, spread in offsets]
    weights = [0.40, 0.35, 0.30]
    rng.shuffle(weights)
    counts = {
        "cloudy": rng.randint(3, 4),
        "rainy": rng.randint(2, 3) + (1 if extreme else 0),
        "smoggy": rng.randint(1, 2),
        "cold_wave": rng.randint(1, 2),
        "tornado": 1 if extreme else rng.randint(0, 1),
        "rocket_launch": 2 if extreme else rng.randint(1, 2),
        "earthquake": 2 if extreme else rng.randint(1, 2),
        "instrument_fault": 1,
    }
    return {
        "name": name,
        "card_id": name,
        "scenario_slug": name,
        "phase": "research",
        "seed": seed,
        "start_date": str(start),
        "end_date": str(start + timedelta(days=nights)),
        "targets": round(5.0 * area),
        "area_deg2": area,
        "stress": stress,
        "wallclock_seconds": 900,
        "family": {
            "extreme": extreme,
            "required_fraction": 0.06 if extreme else 0.05,
            "component_centers": centers,
            "component_area_weights": weights,
            "n_cluster_centers": max(10, round(150.0 * area / 6000.0)),
            "event_counts": counts,
            "fault_range": [0.40, 0.75],
            "closure_scale": 2.0 if extreme else 1.0,
            "data_loss_max_fraction": round(rng.uniform(0.045, 0.075), 3),
        },
    }


def build_family_card(root: Path, spec: dict) -> Path:
    """Organizer bundle builder with the family's catalog and weather parameters."""
    from challenge import v4_bundle
    from challenge.v4_config_check import cross_validate_generator_configs

    family = spec["family"]
    seed = int(spec["seed"])
    stress = bool(spec["stress"])
    catalog = v4_bundle._load("v4_catalog_config.json")
    catalog.update(seed=seed, seed_derivation="sha256-v1")
    catalog["footprint"].update(
        total_area_deg2=float(spec["area_deg2"]), n_components=3,
        component_area_weights=list(family["component_area_weights"]),
        component_centers=[list(center) for center in family["component_centers"]],
    )
    catalog["targets"].update(total_count=int(spec["targets"]), required_fraction=float(family["required_fraction"]),
                              n_cluster_centers=int(family["n_cluster_centers"]))
    catalog["observability"].update(start_date=spec["start_date"], end_date=spec["end_date"])
    weather = v4_bundle._load("v4_weather_stress_config.json" if stress else "v4_weather_config.json")
    weather.update(seed=seed, seed_derivation="sha256-v1")
    weather["survey"].update(start_date=spec["start_date"], end_date=spec["end_date"])
    weather["stress_tests"]["enabled"] = stress
    weather["stress_tests"]["data_loss"]["window_max_fraction"] = float(family["data_loss_max_fraction"])
    for key, count in family["event_counts"].items():
        if key in weather["weather_events"]["conditions"]:
            weather["weather_events"]["conditions"][key]["count"] = int(count)
        else:
            weather[key]["count"] = int(count)
    weather["instrument_fault"]["instrument_efficiency_multiplier_range"] = list(family["fault_range"])
    closure = weather["background_closure"]
    closure["start_probability_per_open_slot"] *= float(family["closure_scale"])
    scenario = v4_bundle._scenario(spec, stress, site=None)
    fiber = v4_bundle._load("v4_fiber_config.json")
    fiber.pop("demo", None)
    cross_validate_generator_configs(catalog, weather, scenario, fiber, require_hashed_seeds=True)
    return v4_bundle._generate(root, seed, catalog, weather, scenario, fiber,
                               v4_bundle.REFERENCE / "v4_score_config.json", None, stress)


def family_specifications(dataset: str, selected: dict, revision: int) -> list[dict]:
    result = []
    for i in range(selected["count"]):
        seed = selected.get("seed_overrides", {}).get(str(i), selected["seed_base"] + i * 997 + 17)
        result.append(family_spec(f"{dataset}-r{revision}-{i:03d}", seed, selected.get("family", {})))
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(family_spec("probe", int(sys.argv[1]) if len(sys.argv) > 1 else 1, {}), indent=1))
