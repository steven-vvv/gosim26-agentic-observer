"""Acceptance run of a built project ZIP with the organizers' local runner and Python 3.12.

The ZIP is unpacked into a fresh directory, the manifest's build step installs NumPy from
the bundled wheels with the given interpreter, and ``examples/_local/runner/run_local.py``
(byte-identical to the platform engine) plays the project on each card with the card's
wall clock. The project runs without model credentials, so the deterministic path is
measured; stdout carries the runner's score summary per card.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .upstream import ROOT, resolve


def run(package: Path, cards: list[Path], python: str, out: Path) -> list[dict]:
    clone = resolve()
    runner = clone / "examples" / "_local" / "runner" / "run_local.py"
    results = []
    with tempfile.TemporaryDirectory(prefix="observer4-verify-") as temporary:
        project = Path(temporary) / "project"
        with zipfile.ZipFile(package) as archive:
            archive.extractall(project)
        # A clean interpreter with pip, as in the platform's python:3.12-slim image.
        subprocess.run([python, "-m", "venv", str(Path(temporary) / "venv")], check=True)
        python = str(Path(temporary) / "venv" / "bin" / "python3")
        manifest = json.loads((project / "observer.project.json").read_text(encoding="utf-8"))
        for step in manifest.get("build", []):
            command = [python if part == "python3" else part for part in step]
            if command[:2] == ["sh", "-c"]:
                command = ["sh", "-c", command[2].replace("python3 ", f"{python} ", 1)]
            subprocess.run(command, cwd=project, check=True, capture_output=True)
        agent = " ".join(python if part == "python3" else part for part in manifest["run"])
        for card in cards:
            target = out / card.name
            if target.exists():
                shutil.rmtree(target)
            completed = subprocess.run(
                [python, str(runner), "--card", str(card), "--agent", agent, "--agent-cwd", str(project),
                 "--out", str(target), "--quiet"],
                capture_output=True, text=True, timeout=1500, check=False)
            lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
            summary = json.loads(lines[-1]) if lines else {"error": completed.stderr[-500:]}
            results.append({"card": card.name, **summary})
            print(json.dumps(results[-1], sort_keys=True), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--cards", nargs="+", type=Path, required=True)
    parser.add_argument("--python", default="/usr/bin/python3.12")
    parser.add_argument("--out", type=Path, default=ROOT / "v4" / "results" / "package-verify")
    args = parser.parse_args()
    run(args.package, args.cards, args.python, args.out / args.package.stem)


if __name__ == "__main__":
    main()
