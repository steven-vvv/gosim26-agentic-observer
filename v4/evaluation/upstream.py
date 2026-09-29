"""Read-only adapter to the exact organizer commit used by this research."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = Path(__file__).with_name("upstream.lock.json")


def resolve() -> Path:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    clone = Path(os.environ.get("GOSIM_V4_UPSTREAM", str(ROOT / lock["relative_clone"]))).resolve()
    if not (clone / "challenge" / "v4_workflow.py").is_file():
        raise RuntimeError("pinned organizer clone is missing; see v4/evaluation/upstream.lock.json")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=clone, text=True).strip()
    if commit != lock["commit"]:
        raise RuntimeError("organizer commit differs from the v4 lock; review the engine before updating the lock")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=clone, text=True)
    if dirty:
        raise RuntimeError("organizer checkout has tracked changes; evaluation requires an unmodified engine")
    loaded = sys.modules.get("challenge")
    if loaded is not None and Path(loaded.__file__).resolve().parent != clone / "challenge":
        raise RuntimeError("another challenge package is already imported; run v4 evaluation in a fresh process")
    sys.path.insert(0, str(clone))
    return clone


def module(name: str):
    resolve()
    return importlib.import_module(f"challenge.{name}")
