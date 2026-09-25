#!/usr/bin/env python3
"""在本地场景上批量评测 agent，输出得分对比表。

    uv run python tools/evaluate.py --agent base=kit/agent/minimal_agent.py --agent obs=agents/observer/main.py \
        --scenario scenarios/comp-a-s101 --scenario scenarios/comp-b-s201 --jobs 3

每个 (agent, 场景) 运行一次 runner，从 score_report.json 提取精简结果写入 results/<agent>/<场景>.json；
随后删除运行目录中的大文件（workflow_result.json、score_report.json、decisions.csv），只留 agent.log。
结果文件已存在时直接复用（--force 强制重跑），基线只需跑一次。

默认用 tools/fast_runner.py（官方 runner 加纯函数缓存）+ PyPy（PATH 里的 pypy3.11）运行 runner 与 agent，
决赛规模每场 obs10 约 115 秒（官方 runner + CPython 约 640 秒），输出与官方逐字节相同。
--official 改用官方 kit/local_runner.py；--verify 对每个作业再用官方 runner + 当前 CPython 跑一遍，
逐字节比对 decisions.csv 与 score_report.json（kit 更新、改补丁或换解释器之后用它复核）。
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "kit" / "local_runner.py"
FAST_RUNNER = ROOT / "tools" / "fast_runner.py"
HEAVY = ("workflow_result.json", "score_report.json", "decisions.csv", "decision_replay.html")


def summarize_report(report: dict, summary: dict, scenario: Path) -> dict:
    score = report["score"]
    completed = report["completion"]["completed_tiles"]
    with (scenario / "outputs" / "reference" / "tiles.csv").open(encoding="utf-8-sig") as handle:
        tile_region = {row["tile_id"]: row["region_id"] for row in csv.DictReader(handle)}
    reports = report.get("reports") or {}
    tags = Counter(f'{item["tag"]}:{item["settled"]}' for item in reports.get("tag_settlements", []))
    return {
        **fault_timing(report, scenario),
        "total": score["total"],
        "base_science": score["base_science"],
        "program_bonus": score["program_bonus"],
        "coverage_bonus": score.get("coverage_bonus", 0.0),
        "coverage_evenness": score.get("coverage_evenness"),
        "request_reward": score.get("request_reward", 0.0),
        "report_reward": score.get("report_reward", 0.0),
        "penalties": {key: value for key, value in score["penalties"].items() if value},
        "completed_tiles": len(completed),
        "region_counts": dict(sorted(Counter(tile_region[t] for t in completed).items())),
        "outcomes": dict(Counter(action["outcome"] for action in report["actions"])),
        "fault_correct_reports": reports.get("fault_correct_reports"),
        "fault_misreports": reports.get("fault_misreports"),
        "tag_results": dict(tags),
        "termination_reason": summary.get("termination_reason"),
        "wall_seconds": summary.get("wall_seconds"),
    }


def fault_timing(report: dict, scenario: Path) -> dict:
    """本地场景带天气真值：算出故障开始到首次正确上报相隔多少小时。"""
    events = scenario / "outputs" / "reference" / "weather_events.csv"
    if not events.exists():
        return {}
    with events.open(encoding="utf-8-sig") as handle:
        faults = [row for row in csv.DictReader(handle) if row["condition"] == "instrument_fault"]
    if not faults:
        return {}
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    start = parse(faults[0]["actual_start_utc"])
    correct = [parse(a["start_utc"]) for a in report["actions"] if a["outcome"] == "report_correct" and a.get("start_utc")]
    return {"fault_regions": json.loads(faults[0]["spatial_scope_payload"]).get("region_ids"),
            "fault_multiplier": float(faults[0]["instrument_efficiency_multiplier"]),
            "fault_delay_hours": round((min(correct) - start).total_seconds() / 3600, 2) if correct else None}


def launch(runner: Path, python: str, agent_name: str, agent_path: Path, scenario: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [python, str(runner), "--scenario", str(scenario), "--agent", str(agent_path), "--out", str(out_dir),
               "--no-replay", "--quiet", "--python", python]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 2) or not (out_dir / "score_report.json").exists():
        raise RuntimeError(f"{agent_name} on {scenario.name} failed (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout[proc.stdout.index("{"):])  # --quiet 时 stdout 只有一个（多行的）汇总 JSON


def run_one(agent_name: str, agent_path: Path, scenario: Path, python: str, force: bool, keep: bool = False,
            runner: Path = FAST_RUNNER, verify: bool = False) -> dict:
    result_path = ROOT / "results" / agent_name / f"{scenario.name}.json"
    if result_path.exists() and not force and not verify:
        return json.loads(result_path.read_text(encoding="utf-8"))
    out_dir = ROOT / "runs" / "eval" / agent_name / scenario.name
    started = time.monotonic()
    summary = launch(runner, python, agent_name, agent_path, scenario, out_dir)
    elapsed = round(time.monotonic() - started, 1)
    report = json.loads((out_dir / "score_report.json").read_text(encoding="utf-8"))
    result = {"agent": agent_name, "scenario": scenario.name, "elapsed": elapsed, "runner": runner.name,
              "python": Path(python).name, **summarize_report(report, summary, scenario)}
    if verify:  # 官方 runner + 当前 CPython 再跑一遍，逐字节比对
        check_dir = out_dir.parent / f"{scenario.name}.official"
        launch(RUNNER, sys.executable, agent_name, agent_path, scenario, check_dir)
        same = [name for name in ("decisions.csv", "score_report.json")
                if (out_dir / name).read_bytes() == (check_dir / name).read_bytes()]
        result["verified"] = len(same) == 2
        print(f"[verify] {agent_name} on {scenario.name}: {'逐字节相同' if result['verified'] else '不一致！'}"
              f"（{runner.name} + {Path(python).name} 对官方 local_runner.py + {Path(sys.executable).name}）", flush=True)
        shutil.rmtree(check_dir)
    for name in HEAVY if not keep else ():
        (out_dir / name).unlink(missing_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def print_table(results: list[dict], agents: list[str], scenarios: list[str]) -> None:
    by = {(r["agent"], r["scenario"]): r for r in results}
    base = agents[0]
    header = f"{'scenario':<14}" + "".join(f"{name:>14}" for name in agents) + "".join(f"{'Δ' + name:>12}" for name in agents[1:])
    print(header)
    for scenario in scenarios:
        row = f"{scenario:<14}" + "".join(f"{by[(a, scenario)]['total']:>14.1f}" for a in agents)
        row += "".join(f"{(by[(a, scenario)]['total'] / by[(base, scenario)]['total'] - 1) * 100:>11.2f}%" for a in agents[1:])
        print(row)
    means = {a: sum(by[(a, s)]["total"] for s in scenarios) / len(scenarios) for a in agents}
    print(f"{'mean':<14}" + "".join(f"{means[a]:>14.1f}" for a in agents)
          + "".join(f"{(means[a] / means[base] - 1) * 100:>11.2f}%" for a in agents[1:]))
    print()
    keys = ("base_science", "program_bonus", "coverage_bonus", "coverage_evenness", "request_reward", "report_reward", "completed_tiles")
    for a in agents:
        agg = {k: sum(float(by[(a, s)][k] or 0) for s in scenarios) / len(scenarios) for k in keys}
        pen = Counter()
        outc = Counter()
        for s in scenarios:
            pen.update(by[(a, s)]["penalties"])
            outc.update(by[(a, s)]["outcomes"])
        print(f"[{a}] " + "  ".join(f"{k}={v:.3f}" if k == "coverage_evenness" else f"{k}={v:.0f}" for k, v in agg.items()))
        print(f"      penalties(sum)={dict(pen)}  outcomes(sum)={dict(outc)}")
        faults = [(by[(a, s)].get("fault_delay_hours"), by[(a, s)].get("fault_misreports")) for s in scenarios]
        tags = Counter()
        for s in scenarios:
            tags.update(by[(a, s)].get("tag_results") or {})
        print(f"      fault(delay_h, misreports)={faults}  tags(sum)={dict(tags)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", action="append", required=True, help="NAME=PATH，第一个作为对比基准")
    parser.add_argument("--scenario", action="append", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=3, help="并行数（本机 4 核、无 swap，默认 3）")
    parser.add_argument("--python", default=shutil.which("pypy3.11") or sys.executable,
                        help="运行 runner 与 agent 的解释器（默认 PATH 里的 pypy3.11，没有就用当前解释器）")
    parser.add_argument("--official", action="store_true", help="用官方 kit/local_runner.py 而不是 tools/fast_runner.py")
    parser.add_argument("--verify", action="store_true", help="再用官方 runner + 当前 CPython 跑一遍并逐字节比对（隐含 --force）")
    parser.add_argument("--force", action="store_true", help="忽略已有结果重新运行")
    parser.add_argument("--keep", action="store_true", help="保留运行目录里的大文件（调试用）")
    args = parser.parse_args(argv)
    runner = RUNNER if args.official else FAST_RUNNER
    agents = [tuple(item.split("=", 1)) for item in args.agent]
    scenarios = [path.resolve() for path in args.scenario]
    jobs = [(name, Path(path).resolve(), scenario) for name, path in agents for scenario in scenarios]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, name, path, scenario, args.python, args.force, args.keep, runner, args.verify)
                   for name, path, scenario in jobs]
        results = [future.result() for future in futures]
    print_table(results, [name for name, _ in agents], [s.name for s in scenarios])
    if args.verify:
        bad = [f"{r['agent']}/{r['scenario']}" for r in results if not r.get("verified")]
        print(f"[verify] {len(results) - len(bad)}/{len(results)} 个作业与官方逐字节相同" + (f"；不一致：{bad}" if bad else ""))
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
