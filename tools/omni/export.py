"""把全知排程写成 agents/public_once 的回放格式，并可用本地官方 runner 验证。

    uv run python tools/omni/export.py --reference runs/omni/dev-reference/best.npy \
        --fortnight runs/omni/dev-fortnight/flow_sci.npy --out agents/public_once/public_schedules.json

回放 agent（agents/public_once/schedule.py）在计划时刻之前等待，到点执行计划动作。
计划里包括科学观测、请求重访与注定中断（全部动作都显式列出），所以回放不再需要 _filler 猜测。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "agents" / "public_once"))
sys.path.insert(0, str(ROOT / "kit"))

from cdp import CDP
from model import Scenario


def entry(scenario: Path, sci_path: Path):
    from challenge.challenge_workflow import ChallengeWorkflow
    from schedule import fingerprint

    sc = Scenario(scenario)
    cdp = CDP(sc)
    sci = np.load(sci_path).astype(np.int32)
    total, plan = cdp.plan(sci)
    exact = sc.evaluate(sc.decisions_from_plan(plan))
    print(f"{scenario.name}: DP {total:.6f}，精确评分 {exact['total']:.6f}，动作 {len(plan)}", flush=True)
    publication = ChallengeWorkflow(root=scenario).initial_publication()
    return fingerprint(publication), {"scenario": scenario.name, "actions": sc.schedule_actions(plan)}, exact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--fortnight", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = {}
    for name, path in (("dev-reference", args.reference), ("dev-fortnight", args.fortnight)):
        key, item, _exact = entry(ROOT / "kit" / "scenarios" / name, path)
        result[key] = item
    args.out.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
