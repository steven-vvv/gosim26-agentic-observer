"""Paired screen over many fresh-seed cards, pooled across datasets and broken down by card kind.

Runs of one variant on one card may live under several dataset directories (the same
generated card can belong to `kind-big` and to a kind subset such as `big-b`); they are
pooled by card name and must agree bit for bit.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

KINDS = "abcd"
FIBERS = {16: "a", 25: "b", 9: "c", 100: "d"}


def kind_of(card: str) -> str:
    if card.startswith(("kind-big-r3-", "kind-sweep-r3-")):
        return KINDS[int(card.rsplit("-", 1)[1]) % 4]
    config = Path("v4/cards") / card / "config" / "v4_fiber_config.json"
    if config.exists():
        return FIBERS.get(json.loads(config.read_text())["field"]["n_fibers"], "?")
    return card[-1] if card.startswith("trial-") else "?"


def load(roots: list[Path]) -> dict[str, dict[str, dict]]:
    runs: dict[str, dict[str, dict]] = defaultdict(dict)
    for root in roots:
        for summary in sorted(root.glob("*/*/summary.json")):
            variant, card = summary.parent.parent.name, summary.parent.name
            row = json.loads(summary.read_text(encoding="utf-8"))
            seen = runs[variant].get(card)
            if seen is not None and seen["action_sha256"] != row["action_sha256"]:
                raise SystemExit(f"{variant}/{card}: runs under different datasets disagree")
            runs[variant][card] = row
    return runs


def interval(delta: np.ndarray, rng, samples: int = 4000) -> tuple[float, float]:
    means = rng.choice(delta, size=(samples, len(delta))).mean(axis=1)
    low, high = np.quantile(means, (0.025, 0.975))
    return float(low), float(high)


def report(runs: dict[str, dict[str, dict]], reference: str, variants: list[str] | None) -> list[str]:
    base = runs[reference]
    rng = np.random.default_rng(20261006)
    lines = [f"reference {reference}: {len(base)} cards, mean {np.mean([r['total'] for r in base.values()]):.1f}"]
    bad = [c for c, r in base.items() if r.get("termination_reason") != "survey_complete"]
    if bad:
        lines.append(f"  reference not survey_complete: {bad}")
    for name in variants or sorted(runs):
        if name == reference or name not in runs:
            continue
        cards = sorted(base.keys() & runs[name].keys())
        if not cards:
            continue
        rows = runs[name]
        delta = np.array([rows[c]["total"] - base[c]["total"] for c in cards])
        low, high = interval(delta, rng)
        comp = {key: np.mean([rows[c]["components"][key] - base[c]["components"][key] for c in cards])
                for key in ("sum_best_scores", "observation_request_reward", "report_settlement", "required_penalty")}
        seconds = np.mean([rows[c]["policy_seconds"] for c in cards]) - np.mean([base[c]["policy_seconds"] for c in cards])
        unfinished = [c for c in cards if rows[c].get("termination_reason") != "survey_complete"]
        lines.append(f"{name:22s} n={len(cards):3d} {delta.mean():+7.1f} [{low:+6.1f},{high:+6.1f}] "
                     f"W/L {int((delta > 1e-6).sum())}/{int((delta < -1e-6).sum())} "
                     f"sci {comp['sum_best_scores']:+6.1f} rq {comp['observation_request_reward']:+5.1f} "
                     f"rep {comp['report_settlement']:+5.1f} reqpen {comp['required_penalty']:+5.1f} "
                     f"dt {seconds:+5.0f}s" + (f" UNFINISHED {unfinished}" if unfinished else ""))
        by_kind = defaultdict(list)
        for c, d in zip(cards, delta):
            by_kind[kind_of(c)].append(d)
        parts = []
        for kind in sorted(by_kind):
            values = np.array(by_kind[kind])
            if len(values) >= 3:
                lo, hi = interval(values, rng)
                parts.append(f"{kind}:{values.mean():+.0f}[{lo:+.0f},{hi:+.0f}]n{len(values)}")
            else:
                parts.append(f"{kind}:{values.mean():+.0f}n{len(values)}")
        lines.append("    " + "  ".join(parts))
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path, help="tag directories (<dataset>-r2/<tag>)")
    parser.add_argument("--reference", default="default")
    parser.add_argument("--variants", nargs="*")
    args = parser.parse_args()
    print("\n".join(report(load(args.roots), args.reference, args.variants)))


if __name__ == "__main__":
    main()
