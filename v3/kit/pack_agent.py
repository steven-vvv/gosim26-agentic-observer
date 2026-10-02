#!/usr/bin/env python3
"""Zip an agent folder into the package the platform runs, after checking that the platform can start it.

    python3 pack_agent.py [--agent agent] [--out my-agent.zip] [--no-env]

Package rules (mirroring the platform's runner):
  * the entry script (minimal_agent.py, agent.py or main.py, first found) sits at the zip root;
  * every other file in the folder is importable next to it;
  * requirements.txt, if present, is `pip install`ed into a fresh virtualenv before the run, so it may only list
    installable distribution names with optional version specifiers (no local paths, URLs, -e, or pip options);
  * .env, if present, is loaded into the agent's environment only (API keys for an LLM provider). It is included
    in the zip unless --no-env is given;
  * __pycache__, .venv, *.pyc, scratch/, run_output/ and editor/OS clutter are never packaged.

Standard library only.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
import zipfile
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
ENTRY_CANDIDATES = ("minimal_agent.py", "agent.py", "main.py")
EXCLUDED_DIRS = {"__pycache__", ".venv", "venv", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "scratch", "run_output", ".idea", ".vscode"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip"}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}
REQUIREMENT_LINE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"  # distribution name (PEP 508)
    r"(?:\[[A-Za-z0-9._,\s-]+\])?"                    # optional extras
    r"\s*(?:(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?"
    r"\s*(?:;.*)?$"                                    # optional environment marker
)
MAX_PACKAGE_BYTES = 20 << 20


def find_entry(agent_dir: Path) -> Path:
    for name in ENTRY_CANDIDATES:
        if (agent_dir / name).is_file():
            return agent_dir / name
    raise SystemExit(f"{agent_dir} must contain one of {', '.join(ENTRY_CANDIDATES)} at its top level")


def check_entry(entry: Path) -> None:
    try:
        ast.parse(entry.read_text(encoding="utf-8"), filename=str(entry))
    except SyntaxError as exc:
        raise SystemExit(f"{entry.name} has a syntax error: {exc}")
    text = entry.read_text(encoding="utf-8")
    if "__main__" not in text:
        print(f"warning: {entry.name} has no `if __name__ == \"__main__\":` block; the platform runs it as a script", file=sys.stderr)


def check_requirements(path: Path) -> list[str]:
    problems = []
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        lowered = line.lower()
        if line.startswith("-") or "://" in line or lowered.startswith(("file:", "git+", "./", "../", "/")) or " @ " in line or line.endswith((".whl", ".tar.gz", ".zip")):
            problems.append(f"line {number}: {line!r} is not an installable distribution name")
        elif not REQUIREMENT_LINE.match(line):
            problems.append(f"line {number}: {line!r} is not a valid requirement specifier")
    return problems


def collect(agent_dir: Path, include_env: bool) -> list[Path]:
    files = []
    for path in sorted(agent_dir.rglob("*")):
        rel = path.relative_to(agent_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if not path.is_file() or path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES:
            continue
        if path.name == ".env" and not include_env:
            continue
        if path.name.startswith(".env.") and path.name != ".env.example":
            continue
        files.append(path)
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", type=Path, default=KIT_ROOT / "agent", help="agent folder (default: agent/)")
    parser.add_argument("--out", type=Path, default=KIT_ROOT / "my-agent.zip", help="zip path (default: my-agent.zip)")
    parser.add_argument("--no-env", action="store_true", help="leave .env out of the package (the platform then runs without your keys)")
    parser.add_argument("--allow-large", action="store_true", help=f"skip the {MAX_PACKAGE_BYTES >> 20} MB size guard")
    args = parser.parse_args(argv)

    agent_dir = args.agent.resolve()
    if not agent_dir.is_dir():
        raise SystemExit(f"agent folder not found: {agent_dir}")
    entry = find_entry(agent_dir)
    check_entry(entry)
    requirements = agent_dir / "requirements.txt"
    if requirements.is_file():
        problems = check_requirements(requirements)
        if problems:
            raise SystemExit("requirements.txt cannot be installed on the platform:\n  " + "\n  ".join(problems))
    files = collect(agent_dir, include_env=not args.no_env)
    out = args.out.resolve()
    if out.parent == agent_dir or agent_dir in out.parents:
        raise SystemExit("write the zip outside the agent folder, otherwise it would package itself")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.relative_to(agent_dir).as_posix())
    size = out.stat().st_size
    if size > MAX_PACKAGE_BYTES and not args.allow_large:
        out.unlink()
        raise SystemExit(f"package is {size / (1 << 20):.1f} MB; move data out of the agent folder or pass --allow-large")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"packed {len(files)} files from {agent_dir} -> {out} ({size} bytes, sha256 {digest[:16]}...)")
    print(f"  entry: {entry.name}")
    for path in files:
        print(f"  {path.relative_to(agent_dir).as_posix()}")
    if (agent_dir / ".env").is_file():
        print("  note: .env is included; its keys are visible only to your agent process on the platform" if not args.no_env
              else "  note: .env left out (--no-env); the platform run will use deterministic mode")
    if requirements.is_file():
        print("  note: the platform installs requirements.txt into a fresh virtualenv before the run (network is allowed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
