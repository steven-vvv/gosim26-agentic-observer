"""Build a reproducible, self-contained v4 project ZIP without reading credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from v4.agent.observer4.config import PROFILE_PATCHES
from v4.agent.observer4.config import resolve as resolve_config

from .upstream import ROOT, resolve

AGENT = ROOT / "v4" / "agent"
VERSION = "2.5.3"
ARCHES = ("x86_64", "aarch64")
TIMESTAMP = (2026, 9, 29, 0, 0, 0)


REPLAY = ROOT / "v4" / "replay"


def sources(
    wheels: Path,
    replay: Path | None = None,
    profile: str | None = None,
    locked_full: bool = False,
) -> dict[str, bytes]:
    result = {}
    agent_files = [AGENT / name for name in ("main.py", "config.json")]
    agent_files.extend(sorted((AGENT / "observer4").glob("*.py")))
    for path in agent_files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unsafe or missing agent source: {path}")
        result[path.relative_to(AGENT).as_posix()] = path.read_bytes()
    for name in ("telemetry", "model_advisor"):
        path = AGENT / "observer4" / "llm" / f"{name}.py"
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing service client: {path}")
        # The clients are part of the agent's own code: the LLM advisor must stay visible
        # to the organizers' code review, which skips directories named "vendor".
        result[f"observer4/llm/{name}.py"] = path.read_bytes()
    for arch in ARCHES:
        found = list(wheels.glob(f"numpy-{VERSION}-cp312-cp312-*{arch}.whl"))
        if len(found) != 1 or found[0].is_symlink():
            raise ValueError(f"expected one NumPy {VERSION} cp312 wheel for {arch} in {wheels}")
        result[f"wheels/{found[0].name}"] = found[0].read_bytes()
    manifest = json.loads((AGENT / "observer.project.json").read_text(encoding="utf-8"))
    manifest["build"] = [[
        "sh", "-c",
        f"python3 -m pip install --disable-pip-version-check --no-index --find-links wheels --target .deps 'numpy=={VERSION}'",
    ]]
    result["observer.project.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    result["README.md"] = (AGENT / "PACKAGE_README.md").read_bytes()
    if profile is not None:
        if profile not in PROFILE_PATCHES:
            raise ValueError(f"unknown release profile: {profile}")
        if locked_full and profile != "full":
            raise ValueError("--locked-full can only be combined with the full profile")
        config = json.loads(result["config.json"])
        # Keep the full baseline in the package.  The entrypoint resolves the
        # selected profile at startup so remote config can switch back to full.
        config["release"]["profile"] = profile
        resolve_config(config)
        result["config.json"] = (json.dumps(config, indent=2) + "\n").encode()
    if locked_full:
        config = json.loads(result["config.json"])
        config["release"]["profile"] = "full"
        remote = dict(config["release"].get("remote_config", {}))
        remote["enabled"] = False
        remote["locked"] = True
        config["release"]["remote_config"] = remote
        resolve_config(config)
        result["config.json"] = (json.dumps(config, indent=2) + "\n").encode()
    if replay is not None:
        # Practice-board build: truth-informed schedules for the published practice cards,
        # the live policy everywhere else, telemetry through the platform's model proxy.
        schedules = json.loads(replay.read_text(encoding="utf-8"))
        if not schedules or any(set(item) < {"card", "fingerprint", "total", "times", "actions"} for item in schedules.values()):
            raise ValueError("invalid replay schedules")
        result["main.py"] = (REPLAY / "main.py").read_bytes()
        result["schedules.json"] = (json.dumps(schedules, separators=(",", ":"), sort_keys=True) + "\n").encode()
        config = json.loads(result["config.json"])
        config["observability"]["remote"]["model_proxy"] = True
        result["config.json"] = (json.dumps(config, indent=2) + "\n").encode()
        result["README.md"] = (REPLAY / "README.md").read_bytes() + b"\n" + result["README.md"]
    return result


def build(
    wheels: Path,
    out: Path,
    replay: Path | None = None,
    profile: str | None = None,
    locked_full: bool = False,
) -> dict:
    clone = resolve()
    from project_platform.package import read_manifest, read_project_zip

    if out.exists() and not out.is_dir():
        raise ValueError(f"output is not a directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    data = sources(wheels, replay, profile, locked_full)
    with tempfile.TemporaryDirectory(prefix=".observer4-", dir=out) as temporary:
        stage = Path(temporary) / "project.zip"
        with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, payload in sorted(data.items()):
                info = zipfile.ZipInfo(name, date_time=TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        raw = stage.read_bytes()
        files = read_project_zip(raw)
        manifest = read_manifest(files)
        if manifest is None or json.loads(data["observer.project.json"])["protocol"] != "jsonl-v4":
            raise ValueError("v4 project manifest rejected by organizer platform")
        digest = hashlib.sha256(raw).hexdigest()
        variant = "observer4"
        if replay is not None:
            variant += "-replay"
        if locked_full:
            variant += "-full-locked"
        destination = out / f"{variant}-{digest[:12]}.zip"
        try:
            os.link(stage, destination)
        except FileExistsError:
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError(f"content-addressed output collision: {destination}") from None
    return {
        "path": str(destination),
        "sha256": digest,
        "zip_bytes": len(raw),
        "expanded_bytes": sum(len(item.data) for item in files),
        "files": len(files),
        "protocol": "jsonl-v4",
        "release_profile": "full-locked" if locked_full else (profile or "config-default"),
        "upstream": clone.name,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels", type=Path, default=Path("/tmp/wheels"))
    parser.add_argument("--out", type=Path, default=ROOT / "v4" / "dist")
    parser.add_argument("--replay", type=Path, help="schedules.json for the practice-board replay build")
    parser.add_argument("--profile", choices=tuple(PROFILE_PATCHES), help="release profile to bake into config.json")
    parser.add_argument(
        "--locked-full",
        action="store_true",
        help="build the full profile with remote configuration permanently locked out",
    )
    args = parser.parse_args()
    if args.locked_full and args.profile not in (None, "full"):
        parser.error("--locked-full can only be combined with --profile full")
    print(json.dumps(build(args.wheels, args.out, args.replay, args.profile, args.locked_full), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
