"""Filesystem defaults for the vendored challenge environment (example3 contract).

`EXAMPLE3_ROOT` points at the bundled public reference scenario; platform code always passes an explicit
scenario root instead of relying on these defaults.
"""

from pathlib import Path


EXAMPLE3_ROOT = Path(__file__).resolve().parent / "reference"
CONFIG_DIR = EXAMPLE3_ROOT / "config"
REFERENCE_OUTPUT_DIR = EXAMPLE3_ROOT / "outputs" / "reference"
