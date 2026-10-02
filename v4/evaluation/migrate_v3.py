"""One-off repository reorganisation: move the v3-specific tree under v3/ (dry run by default).

Moves agents, scenarios, runs, kit, data, tools, tests and result_temp with ``git mv``
(ignored contents move with their directories), plus the ignored root outputs results/
and dist/. Root-anchored ignore patterns, pytest/ruff paths, the telemetry client test and
the absolute paths of two archived run folders are rewritten, and repository paths in our
own Markdown (README, workspace docs, notes) gain the v3/ prefix. Archived platform pages
(docs/en, docs/zh, docs/snapshots) and v4/README.md quote other layouts and stay as they are.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKED = ("agents", "scenarios", "runs", "kit", "data", "tools", "tests", "result_temp")
IGNORED = ("results", "dist")
MARKDOWN_SKIP = ("docs/en/", "docs/zh/", "docs/snapshots/", "docs/announcements/", "v4/")
PATH = re.compile(r"(?<![\w./\-])(" + "|".join(TRACKED) + r")/")


def markdown_files() -> list[Path]:
    names = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    return [ROOT / name for name in names
            if not name.startswith(TRACKED) and not name.startswith(MARKDOWN_SKIP) and not name.startswith("v3/")]


def rewrite_gitignore(text: str) -> str:
    lines = []
    for line in text.splitlines():
        body = line.removeprefix("!")
        bang = "!" if line.startswith("!") else ""
        if body.split("/", 1)[0] in TRACKED and "/" in body:
            line = f"{bang}v3/{body}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def plan() -> list[tuple[str, Path, str]]:
    """(kind, path, new text) edits after the move."""
    edits = []
    gitignore = ROOT / ".gitignore"
    edits.append(("ignore", gitignore, rewrite_gitignore(gitignore.read_text(encoding="utf-8"))))
    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text = text.replace('extend-exclude = ["agents/*/protocol.py"]', 'extend-exclude = ["v3/agents/*/protocol.py"]')
    text = text.replace('testpaths = ["tests", "telemetry-worker/client", "v4/tests"]',
                        'testpaths = ["v3/tests", "telemetry-worker/client", "v4/tests"]')
    text = text.replace("# agents/ 要在平台的 Python 3.12 上运行", "# v3/agents/ 要在平台的 Python 3.12 上运行")
    edits.append(("config", pyproject, text))
    client = ROOT / "telemetry-worker" / "client" / "test_telemetry_client.py"
    text = client.read_text(encoding="utf-8")
    edits.append(("code", client, text.replace('parents[2] / "agents" / "observer"', 'parents[2] / "v3" / "agents" / "observer"')))
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        new = PATH.sub(r"v3/\1/", text)
        if new != text:
            edits.append(("markdown", path, new))
    return edits


def archived_runs() -> list[tuple[Path, str]]:
    """Absolute paths in archived run scripts (already under v3/ after the move)."""
    result = []
    old = str(ROOT)
    for path in sorted((ROOT / "v3" / "runs").glob("fable-0925*/*.py")):
        text = path.read_text(encoding="utf-8")
        new = text.replace(f"{old}/agents/", f"{old}/v3/agents/").replace(f"{old}/tools", f"{old}/v3/tools")
        new = new.replace(f'ROOT = "{old}"', f'ROOT = "{old}/v3"').replace(f"ROOT = '{old}'", f"ROOT = '{old}/v3'")
        new = new.replace(f'"{old}/runs', f'"{old}/v3/runs').replace(f"'{old}/runs", f"'{old}/v3/runs")
        if new != text:
            result.append((path, new))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    edits = plan()
    for kind, path, _ in edits:
        print(f"edit {kind:8s} {path.relative_to(ROOT)}")
    if not args.apply:
        print("dry run; --apply moves", ", ".join(TRACKED + IGNORED))
        return
    (ROOT / "v3").mkdir(exist_ok=True)
    subprocess.run(["git", "mv", *TRACKED, "v3/"], cwd=ROOT, check=True)
    for name in IGNORED:
        if (ROOT / name).exists():
            shutil.move(str(ROOT / name), str(ROOT / "v3" / name))
    for _, path, text in edits:
        path.write_text(text, encoding="utf-8")
    for path, text in archived_runs():
        print(f"edit archived {path.relative_to(ROOT)}")
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
