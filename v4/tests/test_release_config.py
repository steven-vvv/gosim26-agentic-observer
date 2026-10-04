"""Release profiles and bounded startup configuration delivery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from v4.agent.observer4.config import load
from v4.agent.observer4.remote_config import resolve_startup
from v4.evaluation.package import ARCHES, VERSION, sources

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
    assert calls[0][0].get_header("User-agent") == "gosim26-observer4-config/1"


def test_startup_reuses_same_worker_openai_credentials(monkeypatch):
    local = json.loads(CONFIG.read_text(encoding="utf-8"))
    local["release"]["remote_config"]["url"] = "https://config.example.test/v1/config/resolve"
    response = {
        "schema": "gosim26.v4.config-response.v1",
        "config_id": "observer4",
        "revision": 10,
        "patch": {"release": {"profile": "full"}},
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
    monkeypatch.delenv("OBSERVER_CONFIG_TOKEN", raising=False)
    monkeypatch.delenv("TELEMETRY_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://config.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "same-worker-key")
    monkeypatch.setattr(
        "v4.agent.observer4.remote_config.urlopen",
        lambda request, timeout: (calls.append((request, timeout)) or FakeResponse()),
    )

    resolved, status = resolve_startup(local)

    assert resolved["release"]["profile"] == "full"
    assert status["source"] == "remote"
    assert status["revision"] == 10
    assert calls[0][0].get_header("Authorization") == "Bearer same-worker-key"


def test_startup_does_not_send_openai_key_to_another_host(monkeypatch):
    local = json.loads(CONFIG.read_text(encoding="utf-8"))
    local["release"]["remote_config"]["url"] = "https://config.example.test/v1/config/resolve"
    calls = []
    monkeypatch.delenv("OBSERVER_CONFIG_TOKEN", raising=False)
    monkeypatch.delenv("TELEMETRY_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key")
    monkeypatch.setattr(
        "v4.agent.observer4.remote_config.urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    resolved, status = resolve_startup(local)

    assert resolved["release"]["profile"] == "full"
    assert status["source"] == "local"
    assert status["reason"] == "missing_token"
    assert calls == []


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
    assert status["profile"] == "full"
    assert status["reason"] == "ValueError"


def test_startup_remote_full_restores_a_conservative_raw_profile(monkeypatch):
    local = json.loads(CONFIG.read_text(encoding="utf-8"))
    local["release"]["profile"] = "formal_conservative"
    response = {
        "schema": "gosim26.v4.config-response.v1",
        "config_id": "observer4",
        "revision": 9,
        "patch": {"release": {"profile": "full"}},
        "expires_at_ms": None,
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

    assert status["source"] == "remote"
    assert status["profile"] == "full"
    assert resolved["release"]["profile"] == "full"
    assert resolved["policy"]["anchors"] == 24
    assert resolved["policy"]["durations"][-1] == 3600
    assert resolved["model"]["max_total_calls"] == 24


def test_formal_package_keeps_full_baseline_for_startup_switching(tmp_path):
    for arch in ARCHES:
        (tmp_path / f"numpy-{VERSION}-cp312-cp312-test_{arch}.whl").write_bytes(b"wheel placeholder")
    packaged = json.loads(sources(tmp_path, profile="formal_conservative")["config.json"])

    assert packaged["release"]["profile"] == "formal_conservative"
    assert packaged["policy"]["anchors"] == 24
    assert packaged["policy"]["durations"][-1] == 3600
    assert packaged["model"]["max_total_calls"] == 24
