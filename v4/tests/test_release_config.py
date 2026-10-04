"""Release profiles and bounded startup configuration delivery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from v4.agent.observer4.config import load
from v4.agent.observer4.remote_config import resolve_startup

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "v4" / "agent" / "config.json"


def test_formal_profile_reduces_exposure_and_model_budgets_without_disabling_models():
    full = load(CONFIG)
    conservative = load(CONFIG, {"release": {"profile": "formal_conservative"}})

    assert full["release"]["profile"] == "full"
    assert conservative["release"]["profile"] == "formal_conservative"
    assert conservative["policy"]["durations"] == [300, 450, 600, 900]
    assert conservative["policy"]["adaptive_durations"] is False
    assert conservative["policy"]["refine_centers"] is False
    assert conservative["model"]["enabled"] is True
    assert conservative["model"]["max_total_calls"] == 12
    assert conservative["scale"]["durations"] == [300, 450, 600, 900]
    assert conservative["scale"]["refine_top"] == 0

    explicit = load(CONFIG, {
        "release": {"profile": "formal_conservative"},
        "policy": {"required_bonus": 80.0},
    })
    assert explicit["policy"]["required_bonus"] == 80.0


def test_startup_remote_profile_is_validated_and_audited(monkeypatch):
    local = load(CONFIG, {"observability": {"remote": {"enabled": False}}})
    response = {
        "schema": "gosim26.v4.config-response.v1",
        "config_id": "observer4",
        "revision": 7,
        "patch": {"release": {"profile": "formal_conservative"}},
        "expires_at_ms": None,
        "updated_at_ms": 1,
    }

    class FakeResponse:
        headers: ClassVar[dict] = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(response).encode()

    calls = []
    monkeypatch.setenv("OBSERVER_CONFIG_TOKEN", "config-read-token")
    monkeypatch.setattr("v4.agent.observer4.remote_config.urlopen", lambda request, timeout: (calls.append((request, timeout)) or FakeResponse()))
    resolved, status = resolve_startup(local)

    assert resolved["release"]["profile"] == "formal_conservative"
    assert status["source"] == "remote"
    assert status["revision"] == 7
    assert len(calls) == 1
    assert json.loads(calls[0][0].data)["schema"] == "gosim26.v4.config-request.v1"
    assert calls[0][0].get_header("Authorization") == "Bearer config-read-token"


def test_startup_invalid_remote_patch_falls_back_to_local(monkeypatch):
    local = load(CONFIG, {"observability": {"remote": {"enabled": False}}})
    response = {
        "schema": "gosim26.v4.config-response.v1",
        "config_id": "observer4",
        "revision": 8,
        "patch": {"unknown_section": {"enabled": True}},
    }

    class FakeResponse:
        headers: ClassVar[dict] = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(response).encode()

    monkeypatch.setenv("OBSERVER_CONFIG_TOKEN", "config-read-token")
    monkeypatch.setattr("v4.agent.observer4.remote_config.urlopen", lambda *_args, **_kwargs: FakeResponse())
    resolved, status = resolve_startup(local)

    assert resolved == local
    assert status["source"] == "local"
    assert status["remote_attempted"] is True
    assert status["reason"] == "ValueError"
