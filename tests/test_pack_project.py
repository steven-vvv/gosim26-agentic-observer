from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agents" / "observer"
sys.path.insert(0, str(ROOT))

from tools.pack_project import build


def _build(tmp_path: Path, *, wheels: Path | None = None, image: str = "python:3.12-slim") -> tuple[Path, Path]:
    project = tmp_path / "project"
    archive = tmp_path / "observer-project.zip"
    build(AGENT, project, archive, wheels, image, telemetry_enabled=False)
    return project, archive


def test_complete_project_is_deterministic_and_platform_shaped(tmp_path):
    _, archive = _build(tmp_path)
    first = archive.read_bytes()
    first_digest = hashlib.sha256(first).hexdigest()

    _, rebuilt = _build(tmp_path)
    second = rebuilt.read_bytes()
    assert first == second
    assert first_digest == hashlib.sha256(second).hexdigest()

    with zipfile.ZipFile(rebuilt) as package:
        names = package.namelist()
        assert names == sorted(names)
        assert "observer.project.json" in names
        assert "observability.config.json" in names
        config = json.loads(package.read("observability.config.json"))
        assert config["local"]["enabled"] is False
        assert config["remote"]["enabled"] is False
        assert config["events"]["model_request"] is True
        assert not any(name.startswith((".env", ".git/", "__pycache__/")) for name in names)
        manifest = json.loads(package.read("observer.project.json"))
        assert manifest["schema_version"] == "observer-project-v1"
        assert manifest["environment"] == {"PYTHONDONTWRITEBYTECODE": "1"}
        assert manifest["build"] == []
        assert manifest["run"] == ["python3", "-u", "main.py"]


def test_wheels_are_included_and_declared_in_build(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "numpy-0.0-py3-none-any.whl").write_bytes(b"placeholder wheel")

    _, archive = _build(tmp_path, wheels=wheels)
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert "wheels/numpy-0.0-py3-none-any.whl" in names
        manifest = json.loads(package.read("observer.project.json"))
        assert len(manifest["build"]) == 1
        assert manifest["build"][0][0:2] == ["sh", "-c"]


def test_invalid_manifest_input_does_not_clobber_previous_outputs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    marker = project / "marker.txt"
    marker.write_text("previous", encoding="utf-8")
    archive = tmp_path / "observer-project.zip"
    archive.write_bytes(b"previous zip")

    with pytest.raises(SystemExit, match="image"):
        build(AGENT, project, archive, None, "bad image with spaces", telemetry_enabled=False)

    assert marker.read_text(encoding="utf-8") == "previous"
    assert archive.read_bytes() == b"previous zip"


def test_telemetry_url_cannot_package_query_credentials(tmp_path):
    with pytest.raises(SystemExit, match="查询参数"):
        build(AGENT, tmp_path / "project", tmp_path / "observer-project.zip", None,
              "python:3.12-slim", telemetry_enabled=False,
              telemetry_url="https://telemetry.example/v1?token=secret")
