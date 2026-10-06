"""kind_specifications must not change existing datasets' spec hashes when adding flats."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from v4.evaluation.kinds import kind_specifications

ROOT = Path(__file__).resolve().parents[2]

# Frozen before the "flats" field existed, for kind-big index 0 (generation_revision 3).
FROZEN_SPEC = {
    "name": "kind-big-r3-000",
    "card_id": "kind-big-r3-000",
    "scenario_slug": "kind-big-r3-000",
    "phase": "research",
    "kind": "a",
    "seed": 22500017,
}
FROZEN_SHA256 = "1a2a34cf662fc84123014f01faac4732c19346311e30a05f43ab665090efb148"


def test_kind_big_card_0_spec_unchanged():
    data = json.loads((ROOT / "v4" / "experiments" / "datasets.json").read_text(encoding="utf-8"))
    selected = data["kind-big"]
    assert "flats" not in selected
    specs = kind_specifications("kind-big", selected, data["generation_revision"])
    item = specs[0]
    assert item == FROZEN_SPEC
    assert "flats" not in item
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(encoded.encode()).hexdigest() == FROZEN_SHA256


def test_kind_v6_spec_includes_flats():
    data = json.loads((ROOT / "v4" / "experiments" / "datasets.json").read_text(encoding="utf-8"))
    selected = data["kind-v6"]
    specs = kind_specifications("kind-v6", selected, data["generation_revision"])
    assert all("flats" in item for item in specs)
    assert specs[0]["flats"] == {"per_night": 3}
