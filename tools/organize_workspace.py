#!/usr/bin/env python3
"""Plan and apply conservative workspace/archive migrations.

The command is deliberately dry-run by default. ``--apply`` copies (or moves
on the same filesystem) one item at a time, verifies every file with SHA-256,
and only then removes its original. A JSON manifest is written after all
operations succeed so a later audit can compare the migration byte-for-byte.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULT_ROOT = ROOT / "result_temp"
RESULTS_ROOT = ROOT / "results"
TMP_ARCHIVE_ROOT = Path.home() / ".local" / "share" / "gosim26" / "archive" / "tmp"
DEFAULT_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def files_under(path: Path) -> Iterable[Path]:
    if path.is_symlink():
        return
    if path.is_file():
        yield path
    elif path.is_dir():
        # Symlinks are copied as links and verified through their target path;
        # they are not counted as regular payload files in the manifest.
        yield from (item for item in sorted(path.rglob("*")) if item.is_file() and not item.is_symlink())


def file_record(path: Path, source_root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.relative_to(source_root)),
        "bytes": stat.st_size,
        "sha256": digest(path),
        "mtime_ns": stat.st_mtime_ns,
    }


def copy_verified(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
        source.unlink()
        return
    if source.is_dir():
        shutil.copytree(source, destination, copy_function=shutil.copy2, symlinks=True)
        source_files = list(files_under(source))
        destination_files = list(files_under(destination))
        if len(source_files) != len(destination_files):
            raise RuntimeError(f"file count mismatch after copying {source}")
        source_hashes = sorted(digest(item) for item in source_files)
        destination_hashes = sorted(digest(item) for item in destination_files)
        if source_hashes != destination_hashes:
            raise RuntimeError(f"SHA-256 mismatch after copying {source}")
        shutil.rmtree(source)
        return
    shutil.copy2(source, destination)
    if digest(source) != digest(destination):
        raise RuntimeError(f"SHA-256 mismatch after copying {source}")
    source.unlink()


def operation(source: Path, destination: Path, source_root: Path, kind: str) -> dict[str, Any]:
    files = [file_record(path, source_root) for path in files_under(source)]
    return {
        "kind": kind,
        "source": str(source),
        "destination": str(destination),
        "files": files,
        "bytes": sum(item["bytes"] for item in files),
    }


def result_operations(result_root: Path, date: str) -> list[dict[str, Any]]:
    if not result_root.exists():
        return []
    operations: list[dict[str, Any]] = []
    submissions = result_root / "submissions" / date
    logs = result_root / "logs" / date
    for item in sorted(result_root.iterdir()):
        if item.name in {"submissions", "logs", "README.md", "manifest.json", "migration-plan.json"}:
            continue
        if item.is_file():
            target = submissions / "attempt-01" / item.name if item.suffix == ".zip" else logs / item.name
            operations.append(operation(item, target, result_root, "result_temp"))
            continue
        match = re.fullmatch(r"\d+", item.name)
        if match and item.is_dir():
            target = submissions / f"attempt-{int(item.name):02d}"
            operations.append(operation(item, target, result_root, "result_temp"))
    return operations


def evaluation_operations(results_root: Path, result_root: Path, date: str) -> list[dict[str, Any]]:
    """Move reproducible evaluation JSON while preserving its experiment layout."""
    if not results_root.exists():
        return []
    destination_root = result_root / "evaluations" / date
    return [
        operation(item, destination_root / item.name, results_root, "results")
        for item in sorted(results_root.iterdir())
    ]


TMP_DIRS = (
    "robust", "fable-0925", "fable-0925c", "observer-package-smoke-20260927",
    "gosim26-package.TkzC1P", "gosim26-pack-smoke.ZPazqw", "gosim26-v2.ttK139",
    "py312np", "gosim-official", "hackathon-survey26.WXrNYd", "gosim-assets",
    "gosim-site", "gosim-site-live", "gosim_probe", "plat4", "pkgtest", "pkgtest_dev-fortnight",
    "pkgtest_dev-reference", "pr", "site",
)
# These names are transient experiment outputs discovered during the second
# /tmp audit. Keep the list explicit: generic temporary directories must not be
# moved merely because a name happens to contain "gosim".
TMP_ADDITIONAL_DIRS = (
    "direct-check-comp-a-s101", "direct-check-ref", "direct-comp-s101-once",
    "direct-env-dev", "direct-env-once", "gosim-bench-cpy", "gosim-bench-parser",
    "gosim-bench-vector", "gosim-closing-cache-out", "gosim-cpy-profile-10-run",
    "gosim-cpy-profile-run", "gosim-fast-profile-out", "gosim-fast-profile2-out",
    "gosim-insert-weather-out", "gosim-numpy-geometry-dev", "gosim-recent-weather-dev",
    "gosim-recent-weather-dev-official", "gosim-site-open-cache-out",
    "gosim26-packcheck-dev-fortnight", "gosim26-packcheck-dev-reference",
    "gosim26-platform-source-1117973", "gosim26-platform-src.K3iJAt",
    "gosim26-platform.bYMz4i", "gosim26-result2.Ehb3tF", "proto-check-ref",
)
TMP_FILES = (
    "observer-package-smoke-20260927.zip", "score_dev-reference.json", "score_dev-fortnight.json",
    "gosim26-init-workflow.json", "gosim26-latest-workflow.json", "gosim-fast-profile.prof",
    "gosim-fast-profile2.prof", "lb_practice.json", "lb_fort.json", "highs_opts.txt",
    "outputs_k1_before_refactor.txt", "pkgtest_dev-fortnight.json", "pkgtest_dev-reference.json",
)


def project_tmp_files(tmp_root: Path) -> list[Path]:
    explicit = {tmp_root / name for name in TMP_FILES}
    for item in tmp_root.iterdir():
        if not item.is_file():
            continue
        name = item.name
        if name.startswith(("gosim-", "gosim.", "gosim_", "gosim26-", "score_", "lb_", "observer-", "patch_")):
            explicit.add(item)
    return sorted(path for path in explicit if path.exists())


def tmp_operations(tmp_root: Path, archive_root: Path) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for name in (*TMP_DIRS, *TMP_ADDITIONAL_DIRS):
        source = tmp_root / name
        if source.exists() and source not in seen:
            operations.append(operation(source, archive_root / name, tmp_root, "tmp"))
            seen.add(source)
    for source in project_tmp_files(tmp_root):
        if source not in seen:
            operations.append(operation(source, archive_root / source.name, tmp_root, "tmp"))
            seen.add(source)
    return operations


def print_plan(operations: list[dict[str, Any]]) -> None:
    for item in operations:
        print(f"{item['kind']}: {item['source']} -> {item['destination']} ({item['bytes']:,} bytes, {len(item['files'])} files)")
    print(f"total: {sum(item['bytes'] for item in operations):,} bytes in {len(operations)} operations")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def archive_manifest(archive: Path, date: str) -> None:
    """Rebuild a manifest from the archive, including prior incremental runs."""
    if not archive.exists():
        return
    excluded = {"manifest.json", "migration-plan.json", "README.json"}
    files = [
        file_record(path, archive)
        for path in files_under(archive)
        if not (path.parent == archive and path.name in excluded)
    ]
    links = [
        {
            "path": str(path.relative_to(archive)),
            "target": os.readlink(path),
        }
        for path in sorted(archive.rglob("*"))
        if path.is_symlink() and not (path.parent == archive and path.name in excluded)
    ]
    write_json(archive / "manifest.json", {
        "schema_version": "gosim26.workspace-archive.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "date_partition": date,
        "source": "/tmp",
        "file_count": len(files),
        "link_count": len(links),
        "total_bytes": sum(item["bytes"] for item in files),
        "files": files,
        "links": links,
    })


def apply_operations(operations: list[dict[str, Any]], manifest_path: Path, date: str) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    prior_operations: list[dict[str, Any]] = []
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if isinstance(previous, dict) and isinstance(previous.get("operations"), list):
            prior_operations = previous["operations"]
    for item in operations:
        source = Path(item["source"])
        destination = Path(item["destination"])
        if not source.exists():
            raise RuntimeError(f"source disappeared before migration: {source}")
        if destination.exists():
            raise RuntimeError(f"destination already exists: {destination}")
        copy_verified(source, destination)
    manifest = {
        "schema_version": "gosim26.workspace-migration.v1",
        "migrated_at_utc": datetime.now(timezone.utc).isoformat(),
        "date_partition": date,
        "operations": [*prior_operations, *operations],
    }
    write_json(manifest_path, manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the verified plan; default is dry-run")
    parser.add_argument("--result-only", action="store_true")
    parser.add_argument("--tmp-only", action="store_true")
    parser.add_argument("--date", default=os.environ.get("GOSIM26_ARCHIVE_DATE", DEFAULT_DATE))
    args = parser.parse_args(argv)
    if args.result_only and args.tmp_only:
        parser.error("--result-only and --tmp-only are mutually exclusive")

    operations: list[dict[str, Any]] = []
    if not args.tmp_only:
        operations.extend(result_operations(RESULT_ROOT, args.date))
        operations.extend(evaluation_operations(RESULTS_ROOT, RESULT_ROOT, args.date))
    archive = TMP_ARCHIVE_ROOT / args.date
    if not args.result_only:
        operations.extend(tmp_operations(Path("/tmp"), archive))
    print_plan(operations)
    if not args.apply:
        print("dry-run: no files changed; rerun with --apply after reviewing the plan")
        return 0

    if not operations:
        if not args.result_only:
            archive_manifest(archive, args.date)
        print("nothing to migrate")
        return 0
    result_ops = [item for item in operations if item["kind"] in {"result_temp", "results"}]
    tmp_ops = [item for item in operations if item["kind"] == "tmp"]
    if result_ops:
        write_json(RESULT_ROOT / "migration-plan.json", {
            "schema_version": "gosim26.workspace-migration.plan.v1",
            "date_partition": args.date,
            "operations": result_ops,
        })
    if tmp_ops:
        write_json(archive / "migration-plan.json", {
            "schema_version": "gosim26.workspace-migration.plan.v1",
            "date_partition": args.date,
            "operations": tmp_ops,
        })
    if result_ops:
        apply_operations(result_ops, RESULT_ROOT / "manifest.json", args.date)
        if RESULTS_ROOT.exists() and not any(RESULTS_ROOT.iterdir()):
            RESULTS_ROOT.rmdir()
    if tmp_ops:
        apply_operations(tmp_ops, archive / "manifest.json", args.date)
        write_json(archive / "README.json", {
            "purpose": "Archived project-specific /tmp work products",
            "source": "/tmp",
            "manifest": "manifest.json",
            "note": "System/runtime directories and unrelated project workspaces were intentionally excluded.",
        })
        archive_manifest(archive, args.date)
    print("migration complete; every source was verified before removal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
