"""Run official-engine parity checks outside the v3 challenge import namespace."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_geometry_and_ledger_against_pinned_upstream():
    root = Path(__file__).resolve().parents[2]
    checks = Path(__file__).with_name("geometry_checks.py")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(checks)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
