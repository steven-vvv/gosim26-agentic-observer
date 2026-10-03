"""Read the public per-card leaderboard of the platform, without printing the site key.

The board RPC needs the site's public anon key. It is read from the platform page's
script bundle inside this process and never printed or stored; only team scores and their
components are printed (and optionally the raw rows are saved outside the repository).
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

SITE = "https://create.gosim.org/survey26/platform/"
SUPABASE = "https://vdiemcofukuxglqsmlyz.supabase.co"
PRACTICE_PHASE = "249c223b-2ec8-4f0a-924e-7af3c4e3eb44"
HEADERS = {"User-Agent": "Mozilla/5.0 gosim26-board-read"}


def _get(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def _anon_key() -> str:
    html = _get(SITE)
    for src in re.findall(r'(?:src|href)="([^"]+\.(?:js|mjs))"', html):
        url = src if src.startswith("http") else "https://create.gosim.org" + src
        for token in re.findall(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", _get(url)):
            claims = base64.urlsafe_b64decode(token.split(".")[1] + "==").decode("utf-8", "replace")
            if '"anon"' in claims:
                return token
    raise SystemExit("public anon key not found in the site bundle")


def fetch(phase: str, slugs: list[str], limit: int) -> dict[str, list[dict]]:
    key = _anon_key()
    boards = {}
    for slug in slugs:
        body = json.dumps({"p_phase": phase, "p_scenario_slug": slug, "p_limit": limit}).encode()
        request = urllib.request.Request(f"{SUPABASE}/rest/v1/rpc/observer_card_board", data=body, method="POST",
                                         headers={**HEADERS, "apikey": key, "Authorization": f"Bearer {key}",
                                                  "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            boards[slug] = json.loads(response.read()).get("rows") or []
    return boards


def summarise(boards: dict[str, list[dict]], top: int) -> None:
    teams = defaultdict(dict)
    for slug, rows in boards.items():
        for row in rows:
            teams[row["team_name"]][slug] = row
    complete = [(sum(r["total_score"] for r in cards.values()) / len(boards), name, cards)
                for name, cards in teams.items() if len(cards) == len(boards)]
    print(json.dumps({"teams": len(teams), "teams_with_every_card": len(complete)}, ensure_ascii=False))
    for mean, name, cards in sorted(complete, reverse=True)[:top]:
        science = sum(r["components"]["sum_best_scores"] for r in cards.values())
        targets = sum(r.get("targets_observed") or 0 for r in cards.values())
        print(json.dumps({
            "team": name, "mean": round(mean, 1),
            "cards": {slug: round(r["total_score"], 1) for slug, r in cards.items()},
            "science_per_target": round(science / max(1, targets), 3),
            "required_missing": [r.get("required_missing") for r in cards.values()],
            "requests": sum(r["components"]["observation_request_reward"] for r in cards.values()) / 100 / len(cards),
            "submissions": max(r.get("submission_count") or 0 for r in cards.values()),
            "scored_at": max(r["scored_at"] for r in cards.values())[:16],
        }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default=PRACTICE_PHASE)
    parser.add_argument("--slugs", nargs="+",
                        default=[f"v4-practice-{name}" for name in ("alpha", "beta", "gamma", "delta")])
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--save", type=Path, help="directory for the raw rows (keep it outside Git)")
    args = parser.parse_args()
    boards = fetch(args.phase, args.slugs, args.limit)
    if args.save:
        args.save.mkdir(parents=True, exist_ok=True)
        for slug, rows in boards.items():
            (args.save / f"{slug}.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    summarise(boards, args.top)


if __name__ == "__main__":
    main()
