#!/usr/bin/env python3
"""Run a paired strategy experiment on the existing 92-scenario corpus.

    uv run python tools/robust_batch.py horizon21 --param plan_horizon_nights=21
    uv run python tools/robust_batch.py horizon21 --param plan_horizon_nights=21 --all

The first command uses a fixed 23-scenario screening set. Results live under
/tmp/robust/<name>/ and can be compared with runs/robust-0925/compare2.py.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = Path("/tmp/robust/cold")
OUTPUT = Path("/tmp/robust")
AGENT = ROOT / "agents" / "observer"
RUNNER = ROOT / "tools" / "fast_runner.py"


def signal_group(process: subprocess.Popen, sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def scenarios(screen: bool) -> list[Path]:
    names = sorted(path.name for path in CORPUS.iterdir() if (path / "score_report.json").exists())
    if screen:
        local = ["comp-a-s101", "comp-a-s104", "comp-b-s201"]
        old = [name for name in names if name.startswith("mc-") and int(name[-4:]) % 100 <= 16]
        new = [name for name in names if name.startswith("mc-") and int(name[-4:]) % 100 >= 17]
        names = local + [name for name in old if name.startswith("mc-a-")][:4]
        names += [name for name in old if name.startswith("mc-b-")][:4]
        names += [name for name in new if name.startswith("mc-a-")][:6]
        names += [name for name in new if name.startswith("mc-b-")][:6]
    return [ROOT / "scenarios" / name for name in names]


def run_one(name: str, agent: Path, scenario: Path, force: bool, direct: bool, stop: threading.Event) -> tuple[str, float, str]:
    if stop.is_set():
        raise CancelledError()
    out = OUTPUT / name / scenario.name
    report_path = out / "score_report.json"
    if report_path.exists() and not force:
        cached = json.loads(report_path.read_text())
        if cached.get("termination_reason") == "survey_complete":
            return scenario.name, cached["score"]["total"], "cached"
    out.mkdir(parents=True, exist_ok=True)
    if force:
        report_path.unlink(missing_ok=True)
    command = [sys.executable, str(RUNNER), "--scenario", str(scenario), "--agent", str(agent),
               "--out", str(out), "--no-replay", "--quiet", "--python", sys.executable]
    if direct:
        command.append("--direct")
    started = time.monotonic()
    with (out / "runner.stdout").open("w") as stdout, (out / "runner.stderr").open("w") as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, start_new_session=True)
        while process.poll() is None:
            if stop.is_set():
                signal_group(process, signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    signal_group(process, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        signal_group(process, signal.SIGKILL)
                        process.wait()
                raise CancelledError()
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
    if process.returncode or not report_path.exists():
        raise RuntimeError(f"{scenario.name}: runner exited {process.returncode}; see {out / 'runner.stderr'}")
    report = json.loads(report_path.read_text())
    if report.get("termination_reason") != "survey_complete":
        raise RuntimeError(f"{scenario.name}: {report.get('termination_reason')}; see {out / 'agent.log'}")
    for filename in ("workflow_result.json", "decisions.csv"):
        (out / filename).unlink(missing_ok=True)
    return scenario.name, report["score"]["total"], f"{time.monotonic() - started:.0f}s"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="experiment name under /tmp/robust")
    parser.add_argument("--param", action="append", default=[], metavar="KEY=JSON_VALUE")
    parser.add_argument("--all", action="store_true", help="run all 92 scenarios instead of the 23-scenario screen")
    parser.add_argument("--only", action="append", default=[], metavar="SCENARIO", help="run only the named scenario (repeatable)")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--direct", action="store_true", help="use the local same-process observer runner")
    args = parser.parse_args()
    if not args.name.isidentifier() or args.name.startswith("."):
        parser.error("name must be a simple identifier")
    params = {}
    for assignment in args.param:
        key, separator, value = assignment.partition("=")
        if not separator:
            parser.error(f"invalid --param {assignment!r}")
        params[key] = json.loads(value)
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    experiment_dir = OUTPUT / args.name
    work_agent = OUTPUT / args.name / ".agent"
    if experiment_dir.exists() and not work_agent.exists() and any(experiment_dir.glob("*/score_report.json")):
        parser.error(f"{experiment_dir} contains existing reports but no experiment agent snapshot")
    if work_agent.exists():
        existing = json.loads((work_agent / "params.json").read_text())
        if existing != params:
            parser.error(f"{work_agent} already contains different parameters")
    else:
        shutil.copytree(AGENT, work_agent, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (work_agent / "params.json").write_text(json.dumps(params), encoding="utf-8")
    jobs = scenarios(not args.all)
    if args.only:
        missing = set(args.only) - {scenario.name for scenario in jobs}
        if missing:
            parser.error(f"scenario not found in selected corpus: {', '.join(sorted(missing))}")
        jobs = [scenario for scenario in jobs if scenario.name in args.only]
    print(f"{args.name}: {len(jobs)} scenarios, {args.jobs} workers, direct={args.direct}, params={params}", flush=True)
    stop = threading.Event()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, args.name, work_agent / "main.py", scenario, args.force, args.direct, stop)
                   for scenario in jobs]
        try:
            for future in as_completed(futures):
                scenario, score, elapsed = future.result()
                print(f"{scenario}: {score:.3f} ({elapsed})", flush=True)
        except KeyboardInterrupt:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            stop.set()
            for future in futures:
                future.cancel()
            print(f"{args.name}: interrupted; stopping active runners", file=sys.stderr, flush=True)
            raise SystemExit(130) from None
        except Exception:
            stop.set()
            for future in futures:
                future.cancel()
            raise


if __name__ == "__main__":
    main()
