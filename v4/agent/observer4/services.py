"""Load version-independent service clients without importing v3 policy code."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_client(name: str):
    if name not in {"telemetry", "model_advisor"}:
        raise ValueError("unknown shared service client")
    qualified = f"observer4_shared_{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    here = Path(__file__).resolve().parent
    candidates = [here / "llm" / f"{name}.py", here / "vendor" / f"{name}.py",
                  here.parents[2] / "agents" / "observer" / f"{name}.py"]
    path = next((path for path in candidates if path.is_file()), None)
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location(qualified, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - optional service client may be unavailable in a bundle
        sys.modules.pop(qualified, None)
        return None
    return module
