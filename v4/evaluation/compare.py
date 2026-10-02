"""Compact paired comparison of research variants (cards present in both are paired)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def load(tag_dir: Path) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for summary in sorted(tag_dir.glob("*/*/summary.json")):
        variant, card = summary.parent.parent.name, summary.parent.name
        result.setdefault(variant, {})[card] = json.loads(summary.read_text(encoding="utf-8"))
    return result


def table(rows: dict[str, dict[str, dict]], reference: str, *, samples: int = 4000) -> list[str]:
    base = rows[reference]
    lines = [f"reference {reference}: {len(base)} cards, mean {np.mean([r['total'] for r in base.values()]):.1f}"]
    rng = np.random.default_rng(20261003)
    for name, values in sorted(rows.items()):
        if name == reference:
            continue
        cards = sorted(base.keys() & values.keys())
        if not cards:
            continue
        delta = np.array([values[c]["total"] - base[c]["total"] for c in cards])
        means = rng.choice(delta, size=(samples, len(cards))).mean(axis=1)
        low, high = np.quantile(means, (0.025, 0.975))
        comp = {key: np.mean([values[c]["components"][key] - base[c]["components"][key] for c in cards])
                for key in ("sum_best_scores", "required_penalty", "report_settlement", "observation_request_reward")}
        seconds = np.mean([values[c]["policy_seconds"] for c in cards])
        lines.append(
            f"{name:34s} n={len(cards):3d} mean {delta.mean():+8.1f} [{low:+7.1f},{high:+7.1f}] "
            f"W/L {int((delta > 1e-6).sum()):2d}/{int((delta < -1e-6).sum()):2d} min {delta.min():+7.1f} "
            f"sci {comp['sum_best_scores']:+6.1f} req {comp['required_penalty']:+6.1f} "
            f"rep {comp['report_settlement']:+5.1f} rq {comp['observation_request_reward']:+5.1f} t {seconds:5.0f}s"
        )
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tags", nargs="+", help="research tag directories under v4/results/research")
    parser.add_argument("--reference", default="default")
    parser.add_argument("--root", type=Path, default=Path("v4/results/research"))
    parser.add_argument("--per-card", action="store_true")
    args = parser.parse_args()
    for tag in args.tags:
        rows = load(args.root / tag)
        print(f"== {tag}")
        print("\n".join(table(rows, args.reference)))
        if args.per_card:
            base = rows[args.reference]
            for card in sorted(base):
                cells = " ".join(f"{name[:12]}={rows[name][card]['total'] - base[card]['total']:+.0f}"
                                 for name in sorted(rows) if name != args.reference and card in rows[name])
                print(f"   {card:24s} {base[card]['total']:8.1f} {cells}")


if __name__ == "__main__":
    main()
