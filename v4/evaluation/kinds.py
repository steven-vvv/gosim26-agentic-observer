"""Research cards of the four formal card kinds, rebuilt from the organizer's trial specs.

The formal cards A-D follow the four trial practice specs the organizer published on
2026-10-01 (hackathon-survey26 ``bdb1031``, ``cards/v4-practice-{a,b,c,d}/spec``) and
withdrew with PR #153: the run telemetry of A-D shows the same fibre layouts (16, 25, 9
and 100 fibres), target counts and seasons. The specs are copied unchanged into
``kind_specs/<kind>/``; a research card only replaces the catalogue and weather seeds, so
site, season, footprint, fibre layout, events and scoring stay those of the kind.

* a: Paranal, 123 nights, 30,000 targets, 16 fibres of 0.4 deg2;
* b: Cape Town, 183 nights, 50,000 targets, 25 fibres of 0.064 deg2, frequent rain and cloud;
* c: Mauna Kea, 121 nights, 30,000 targets, 9 fibres of 0.7 deg2;
* d: Nemo Observatory, 365 nights, 30,000 targets, 100 fibres of 0.06 deg2, stress
  (data-loss windows) and three instrument faults.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

SPECS = Path(__file__).with_name("kind_specs")
KINDS = ("a", "b", "c", "d")


def kind_specifications(dataset: str, selected: dict, revision: int) -> list[dict]:
    """One card per seed, the kinds in turn, so every prefix of the dataset is balanced."""
    kinds = list(selected.get("kinds", KINDS))
    result = []
    for i in range(selected["count"]):
        seed = selected.get("seed_overrides", {}).get(str(i), selected["seed_base"] + i * 997 + 17)
        name = f"{dataset}-r{revision}-{i:03d}"
        item = {"name": name, "card_id": name, "scenario_slug": name, "phase": "research",
                "kind": kinds[i % len(kinds)], "seed": int(seed)}
        if selected.get("weather_patch"):
            item["weather_patch"] = selected["weather_patch"]
        result.append(item)
    return result


def build_kind_card(root: Path, spec: dict) -> Path:
    """Organizer spec builder with the kind's configs and a new catalogue and weather seed."""
    from challenge.v4_bundle import build_spec_bundle

    source = SPECS / spec["kind"]
    with tempfile.TemporaryDirectory(prefix="kind-spec-") as scratch:
        folder = Path(scratch) / "spec"
        shutil.copytree(source, folder)
        card = json.loads((folder / "card.json").read_text(encoding="utf-8"))
        card.update(name=spec["name"], card_id=spec["card_id"], scenario_slug=spec["scenario_slug"],
                    phase=spec["phase"])
        (folder / "card.json").write_text(json.dumps(card), encoding="utf-8")
        for name in ("v4_catalog_config.json", "v4_weather_config.json"):
            config = json.loads((folder / name).read_text(encoding="utf-8"))
            config["seed"] = int(spec["seed"])
            if name == "v4_weather_config.json":
                # Stress sections replaced whole (A1-D1 style: several faults, terrain, quakes).
                config.update(spec.get("weather_patch", {}))
            (folder / name).write_text(json.dumps(config), encoding="utf-8")
        return build_spec_bundle(root, folder)
