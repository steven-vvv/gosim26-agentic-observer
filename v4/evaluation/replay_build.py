"""Plan truth-informed schedules for published cards, keep the best per card, verify replays.

Each (card, variant) runs ``omniscient.plan_card`` in its own process; the best total per
card goes into one schedules.json keyed by the public fingerprint. Verification replays
every chosen schedule through ``v4/replay/main.py``'s policy with the unmodified engine
(not the accelerated one) and requires the planned total.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing
from pathlib import Path

from .omniscient import SEARCH, plan_card
from .upstream import ROOT, resolve


def _patch(name: str) -> dict:
    patch = json.loads(json.dumps(SEARCH))
    if name != "base":
        extra = json.loads((ROOT / "v4" / "experiments" / f"{name}.json").read_text(encoding="utf-8"))
        for section, values in extra.items():
            patch.setdefault(section, {}).update(values)
    return patch


def _plan(card: str, variant: str, base: Path) -> dict:
    out = base / variant / card
    if (out / "schedule.json").is_file():
        schedule = json.loads((out / "schedule.json").read_text(encoding="utf-8"))
        return {"card": card, "variant": variant, "total": schedule["total"], "path": str(out / "schedule.json")}
    result = plan_card(ROOT / "v4" / "cards" / card, out, _patch(variant))
    return {"card": card, "variant": variant, "total": result["total"], "path": str(out / "schedule.json")}


def verify(card: str, schedule: dict, out: Path) -> dict:
    resolve()
    import importlib.util

    from v4.agent.observer4.config import load

    from .run import evaluate

    spec = importlib.util.spec_from_file_location("observer4_replay_main", ROOT / "v4" / "replay" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = load(ROOT / "v4" / "agent" / "config.json")

    def factory(publication, record):
        if module.fingerprint(publication) != schedule["fingerprint"]:
            raise ValueError("fingerprint differs from the planned card")
        return module.Replay(schedule, publication, config, record)

    summary = evaluate(ROOT / "v4" / "cards" / card, out, config, fast=False, factory=factory)
    return {"card": card, "planned": schedule["total"], "replayed": summary["total"],
            "termination": summary["termination_reason"], "elapsed_seconds": summary["elapsed_seconds"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", nargs="+", default=["v4-practice-alpha", "v4-practice-beta", "v4-practice-gamma",
                                                        "v4-practice-delta"])
    parser.add_argument("--variants", nargs="+", default=["base"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    base = ROOT / "v4" / "results" / "replay" / args.tag
    jobs = [(card, variant) for card in args.cards for variant in args.variants]
    context = multiprocessing.get_context("fork")
    results = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs, mp_context=context) as pool:
        futures = [pool.submit(_plan, card, variant, base) for card, variant in jobs]
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            results.append(row)
            print(json.dumps(row), flush=True)
    schedules = {}
    for card in args.cards:
        best = max((row for row in results if row["card"] == card), key=lambda row: row["total"])
        schedule = json.loads(Path(best["path"]).read_text(encoding="utf-8"))
        schedule["variant"] = best["variant"]
        schedules[schedule["fingerprint"]] = schedule
        print(json.dumps({"card": card, "chosen": best["variant"], "total": best["total"]}), flush=True)
    target = base / "schedules.json"
    target.write_text(json.dumps(schedules, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    if args.verify:
        for schedule in schedules.values():
            print(json.dumps(verify(schedule["card"], schedule, base / "verify" / schedule["card"])), flush=True)
    print(json.dumps({"schedules": str(target), "cards": len(schedules)}))


if __name__ == "__main__":
    main()
