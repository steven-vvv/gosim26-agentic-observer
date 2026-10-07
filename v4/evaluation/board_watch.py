"""Watch the formal-phase boards (A-D, A1-D1 and the super board) of the platform.

Reads the public RPCs ``observer_card_board`` (A-D) and ``observer_super_board`` (A1-D1 card tabs and
the super board). The site's anon key is obtained in-process by ``board_read`` and is never printed
or stored; snapshots only hold team rows. Typical use while waiting::

    uv run python -m v4.evaluation.board_watch --diff        # fetch, save, compare with the previous snapshot
    uv run python -m v4.evaluation.board_watch --from v4/results/board/<ts> --threat   # offline analysis
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from v4.evaluation import board_read

FORMAL_PHASE_SLUG = "online"
OUR_TEAM = "CCB"
BOARD_DIR = Path(__file__).resolve().parents[1] / "results" / "board"
AD_CARDS = ["v4-a", "v4-b", "v4-c", "v4-d"]
SUPER = "super"


def _call(key: str, rpc: str, phase: str, slug: str | None, limit: int) -> dict:
    body = json.dumps({"p_phase": phase, "p_scenario_slug": slug, "p_limit": limit}).encode()
    request = urllib.request.Request(
        f"{board_read.SUPABASE}/rest/v1/rpc/{rpc}",
        data=body,
        method="POST",
        headers={**board_read.HEADERS, "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    # The board RPC answers 500 now and then under load; a short retry usually succeeds.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def _phase_id(key: str) -> str:
    request = urllib.request.Request(
        f"{board_read.SUPABASE}/rest/v1/phases?select=id,slug&slug=eq.{FORMAL_PHASE_SLUG}",
        headers={**board_read.HEADERS, "apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())[0]["id"]


def fetch(limit: int) -> dict[str, dict]:
    """Return {board name: RPC payload}: 'overall' (A-D), each A-D slug, 'super', each A1-D1 slug."""
    key = board_read._anon_key()
    phase = _phase_id(key)
    overall = _call(key, "observer_card_board", phase, None, limit)
    boards = {"overall": overall}
    for card in overall.get("cards") or []:
        boards[card["slug"]] = _call(key, "observer_card_board", phase, card["slug"], limit)
    boards[SUPER] = _call(key, "observer_super_board", phase, None, limit)
    for card in overall.get("extra_cards") or []:
        boards[card["slug"]] = _call(key, "observer_super_board", phase, card["slug"], limit)
    boards["_meta"] = {"phase_id": phase, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "limit": limit}
    return boards


def save(boards: dict[str, dict], root: Path = BOARD_DIR) -> Path:
    directory = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in boards.items():
        (directory / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return directory


def load(directory: Path) -> dict[str, dict]:
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))}


def previous_snapshot(current: Path | None, root: Path = BOARD_DIR) -> Path | None:
    dirs = sorted(path for path in root.glob("*Z") if path.is_dir() and path != current)
    if current is not None:
        dirs = [path for path in dirs if path.name < current.name]
    return dirs[-1] if dirs else None


# ---------------------------------------------------------------- tables


def card_slugs(boards: dict[str, dict]) -> tuple[list[str], list[str]]:
    overall = boards["overall"]
    ad = [card["slug"] for card in overall.get("cards") or []]
    extra = [card["slug"] for card in overall.get("extra_cards") or []]
    return ad, extra


def rows_of(boards: dict[str, dict], name: str) -> list[dict]:
    return (boards.get(name) or {}).get("rows") or []


def team_table(boards: dict[str, dict]) -> dict[str, dict]:
    """Per team: A-D / A1-D1 card scores, science, counts, super-board numbers."""
    ad, extra = card_slugs(boards)
    teams: dict[str, dict] = {}
    for row in rows_of(boards, "overall"):
        entry = teams.setdefault(row["team_name"], {"name": row["team_name"]})
        entry.update(
            ad_mean=row["total_score"],
            ad_rank=row.get("rank"),
            ad_scores=row.get("card_scores") or {},
            scored_at=row.get("scored_at"),
            final_version=row.get("final_version"),
        )
    for slug in ad + extra:
        for row in rows_of(boards, slug):
            entry = teams.setdefault(row["team_name"], {"name": row["team_name"]})
            comps = row.get("components") or {}
            entry.setdefault("cards", {})[slug] = {
                "score": row["total_score"],
                "science": comps.get("sum_best_scores"),
                "targets": row.get("targets_observed"),
                "missing": row.get("required_missing"),
                "requests": comps.get("observation_request_reward"),
                "submissions": row.get("submission_count"),
                "scored_at": row.get("scored_at"),
            }
    for row in rows_of(boards, SUPER):
        entry = teams.setdefault(row["team_name"], {"name": row["team_name"]})
        entry.update(
            super_score=row["total_score"],
            super_rank=row.get("rank"),
            super_cards=row.get("card_scores") or {},
            super_scored_at=row.get("scored_at"),
            submissions=row.get("submission_count"),
        )
    for entry in teams.values():
        cards = entry.get("cards", {})
        entry["a1d1_mean"] = None
        if extra and all(slug in cards for slug in extra):
            entry["a1d1_mean"] = sum(cards[slug]["score"] for slug in extra) / len(extra)
        entry["science_ad"] = sum((cards.get(slug) or {}).get("science") or 0 for slug in ad)
        entry["targets_ad"] = sum((cards.get(slug) or {}).get("targets") or 0 for slug in ad)
        ex_targets = sum((cards.get(slug) or {}).get("targets") or 0 for slug in extra)
        entry["extra_science_per_target"] = (
            sum((cards.get(slug) or {}).get("science") or 0 for slug in extra) / ex_targets if ex_targets else None
        )
        entry["science_per_target"] = entry["science_ad"] / entry["targets_ad"] if entry["targets_ad"] else None
    return teams


def _fmt(value, digits=0):
    return "-" if value is None else f"{value:.{digits}f}"


def print_summary(boards: dict[str, dict], top: int, team: str) -> None:
    ad, extra = card_slugs(boards)
    teams = team_table(boards)
    meta = boards.get("_meta", {})
    print(
        f"# 快照 {meta.get('fetched_at', '?')}  A–D 队数 {len(rows_of(boards, 'overall'))}  "
        f"超级榜队数 {len(rows_of(boards, SUPER))}  卡 {ad} + {extra}"
    )
    ranked = sorted((t for t in teams.values() if t.get("ad_mean") is not None), key=lambda t: -t["ad_mean"])
    head = (
        "名次 队名                均分     "
        + " ".join(f"{slug[3:]:>8}" for slug in ad)
        + "  科学分Σ 目标Σ 科/目标 缺必 提交 终版 最后评分"
    )
    print("\n## A–D 正式榜（按均分）\n" + head)
    for index, t in enumerate(ranked[:top], 1):
        cards = t.get("cards", {})
        miss = "/".join(str((cards.get(slug) or {}).get("missing", "-")) for slug in ad)
        subs = max([(cards.get(slug) or {}).get("submissions") or 0 for slug in ad] or [0])
        print(
            f"{index:>3}  {t['name'][:18]:<18} {t['ad_mean']:>8.1f} "
            + " ".join(f"{(t['ad_scores'].get(slug) or 0):>8.0f}" for slug in ad)
            + f"  {t['science_ad']:>8.0f} {t['targets_ad']:>6} {_fmt(t['science_per_target'], 3):>7} {miss:>7} {subs:>4} "
            f"{'是' if (t.get('final_version') or {}).get('chosen') else '否':>3} {(t.get('scored_at') or '')[:16]}"
        )
    mine = next((i for i, t in enumerate(ranked, 1) if t["name"] == team), None)
    if mine and mine > top:
        t = ranked[mine - 1]
        print(f"...\n{mine:>3}  {t['name'][:18]:<18} {t['ad_mean']:>8.1f}")
    if mine:
        me = ranked[mine - 1]
        print(f"\n## {team} 第 {mine}/{len(ranked)}，均分 {me['ad_mean']:.1f}；与榜首 / 前三均值逐卡差距")
        for label, group in (("榜首", ranked[:1]), ("前三均值", ranked[:3])):
            gaps = []
            for slug in ad:
                other = sum(g["ad_scores"].get(slug, 0) for g in group) / len(group)
                gaps.append(f"{slug[3:]}:{me['ad_scores'].get(slug, 0) - other:+.0f}")
            print(
                f"  vs {label} ({', '.join(g['name'] for g in group)}): 均分 {me['ad_mean'] - sum(g['ad_mean'] for g in group) / len(group):+.1f}  "
                + " ".join(gaps)
            )
        best = {slug: max(t["ad_scores"].get(slug, 0) for t in ranked) for slug in ad}
        print("  各卡全榜最高: " + " ".join(f"{slug[3:]}:{value:.0f}" for slug, value in best.items()))
    else:
        print(f"\n(A–D 榜上未找到 {team})")

    for slug in extra:
        rows = sorted((t for t in teams.values() if slug in t.get("cards", {})), key=lambda t: -t["cards"][slug]["score"])
        print(f"\n## {slug} 前 {min(top, len(rows))} / 共 {len(rows)} 队")
        for index, t in enumerate(rows[:top], 1):
            c = t["cards"][slug]
            print(
                f"{index:>3}  {t['name'][:18]:<18} {c['score']:>9.1f}  科学 {_fmt(c['science'])} 目标 {c['targets']} 缺必 {c['missing']} "
                f"请求 {_fmt(c['requests'])} 提交 {c['submissions']} {(c['scored_at'] or '')[:16]}"
            )
        pos = next((i for i, t in enumerate(rows, 1) if t["name"] == team), None)
        if pos:
            print(
                f"  {team}: 第 {pos}，{rows[pos - 1]['cards'][slug]['score']:.1f}，距榜首 {rows[pos - 1]['cards'][slug]['score'] - rows[0]['cards'][slug]['score']:+.1f}"
            )

    sup = sorted((t for t in teams.values() if t.get("super_score") is not None), key=lambda t: -t["super_score"])
    print(f"\n## 超级榜（0.2×ΣA–D + 0.8×ΣA1–D1，同一次评测）前 {min(top, len(sup))} / 共 {len(sup)} 队")
    order = ad + extra
    print("名次 队名                总分      " + " ".join(f"{slug[3:]:>8}" for slug in order) + " 提交 最后评分")
    for index, t in enumerate(sup[:top], 1):
        print(
            f"{index:>3}  {t['name'][:18]:<18} {t['super_score']:>9.0f} "
            + " ".join(f"{t['super_cards'].get(slug, 0):>8.0f}" for slug in order)
            + f" {t.get('submissions') or '-':>4} {(t.get('super_scored_at') or '')[:16]}"
        )
    pos = next((i for i, t in enumerate(sup, 1) if t["name"] == team), None)
    if pos:
        me, lead = sup[pos - 1], sup[0]
        print(
            f"  {team}: 第 {pos}，{me['super_score']:.0f}，距榜首 {me['super_score'] - lead['super_score']:+.0f}；逐卡 "
            + " ".join(f"{slug[3:]}:{me['super_cards'].get(slug, 0) - lead['super_cards'].get(slug, 0):+.0f}" for slug in order)
        )


# ---------------------------------------------------------------- diff


def _scores(boards: dict[str, dict]) -> dict[str, dict[str, tuple[float, int | None]]]:
    """{board: {team: (score, rank)}} for every board with rows."""
    return {
        name: {row["team_name"]: (row["total_score"], row.get("rank")) for row in payload.get("rows") or []}
        for name, payload in boards.items()
        if not name.startswith("_")
    }


def diff(old: dict[str, dict], new: dict[str, dict], team: str, min_delta: float = 0.5) -> list[str]:
    lines: list[str] = []
    old_scores, new_scores = _scores(old), _scores(new)
    for name in new_scores:
        before, after = old_scores.get(name, {}), new_scores[name]
        entries = []
        for t, (score, rank) in after.items():
            if t not in before:
                entries.append(f"  新增 {t}: {score:.1f} (第 {rank})")
            else:
                delta, move = score - before[t][0], (before[t][1] or 0) - (rank or 0)
                if abs(delta) >= min_delta or move:
                    entries.append(
                        f"  {t}: {before[t][0]:.1f} -> {score:.1f} ({delta:+.1f})，名次 {before[t][1]} -> {rank}"
                        + (f" ({'升' if move > 0 else '降'}{abs(move)})" if move else "")
                    )
        entries += [f"  消失 {t}" for t in before if t not in after]
        if entries:
            lines.append(f"[{name}]")
            lines += entries
    mine_old = (old_scores.get("overall") or {}).get(team)
    mine_new = (new_scores.get("overall") or {}).get(team)
    lines.append(f"{team} A–D: {mine_old} -> {mine_new}")
    return lines


# ---------------------------------------------------------------- threat heuristics


def print_threat(boards: dict[str, dict], top: int, team: str) -> None:
    """Rank teams by forward-strength proxies: A1-D1 (fresh, undisclosed truth) vs A-D (reconstructible truth)."""
    teams = [t for t in team_table(boards).values() if t.get("ad_mean") is not None]
    ad_ranked = sorted(teams, key=lambda t: -t["ad_mean"])
    ad_rank = {t["name"]: i for i, t in enumerate(ad_ranked, 1)}
    with_extra = sorted((t for t in teams if t.get("a1d1_mean") is not None), key=lambda t: -t["a1d1_mean"])
    ex_rank = {
        t["name"]: i
        for i, t in enumerate(sorted((t for t in teams if t.get("a1d1_mean") is not None), key=lambda t: -t["a1d1_mean"]), 1)
    }
    if not with_extra:
        print("无 A1–D1 数据")
        return
    with_extra = [t for t in with_extra if t["a1d1_mean"] > 0 or t["name"] == team]  # negative = A1-D1 penalties, no ratio
    ratio_all = sorted(t["a1d1_mean"] / t["ad_mean"] for t in with_extra if t["a1d1_mean"] > 0)
    median = ratio_all[len(ratio_all) // 2]
    print(f"\n## 威胁启发式（A1–D1 均分 / A–D 均分，中位 {median:.2f}；越低越像 A–D 过拟合/离线排程）")
    print("队名                 A–D名 A1D1名  A–D均分  A1D1均分   比值 AD科/目标 A1D1科/目标 提交")
    for t in [t for t in with_extra if t["a1d1_mean"] > 0][: max(top, 15)]:
        subs = t.get("submissions") or max((c.get("submissions") or 0) for c in t["cards"].values())
        print(
            f"{t['name'][:18]:<18} {ad_rank[t['name']]:>6} {ex_rank[t['name']]:>6} {t['ad_mean']:>9.0f} {t['a1d1_mean']:>9.0f} "
            f"{t['a1d1_mean'] / t['ad_mean']:>6.2f} {_fmt(t['science_per_target'], 3):>9} "
            f"{_fmt(t.get('extra_science_per_target'), 3):>11} {subs:>4}"
        )
    flagged = [
        t
        for t in ad_ranked[:15]
        if t["name"] not in ex_rank or t["a1d1_mean"] <= 0 or t["a1d1_mean"] / t["ad_mean"] < median * 0.85
    ]
    print(
        "A–D 前 15 中 A1–D1 缺失或明显偏弱（疑似 A–D 刷榜/只调 A–D）: "
        + (
            ", ".join(
                f"{t['name']}(A–D#{ad_rank[t['name']]}"
                + (f", A1D1#{ex_rank[t['name']]}" if t["name"] in ex_rank else ", 无A1D1")
                + ")"
                for t in flagged
            )
            or "无"
        )
    )
    me = next((t for t in with_extra if t["name"] == team), None)
    if me:
        print(f"{team}: A–D#{ad_rank[team]} A1D1#{ex_rank[team]} 均分 {me['a1d1_mean']:.0f}（榜上是旧评测；负分=必观测罚分）")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--limit", type=int, default=300, help="rows requested per board")
    parser.add_argument("--team", default=OUR_TEAM)
    parser.add_argument("--from", dest="source", type=Path, help="read a saved snapshot directory instead of fetching")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--diff", nargs="?", const="prev", help="compare with the previous snapshot (or a given directory)")
    parser.add_argument("--threat", action="store_true", help="print forward-strength heuristics")
    parser.add_argument("--quiet", action="store_true", help="skip the full summary (useful with --diff)")
    args = parser.parse_args()
    if args.source:
        boards, directory = load(args.source), args.source
    else:
        boards = fetch(args.limit)
        directory = None if args.no_save else save(boards)
        if directory:
            print(f"快照已保存: {directory}")
    if args.diff:
        old_dir = previous_snapshot(directory) if args.diff == "prev" else Path(args.diff)
        if old_dir is None:
            print("没有更早的快照可比较")
        else:
            print(f"## 与 {old_dir.name} 相比")
            print("\n".join(diff(load(old_dir), boards, args.team)))
    if not args.quiet:
        print_summary(boards, args.top, args.team)
    if args.threat:
        print_threat(boards, args.top, args.team)


if __name__ == "__main__":
    main()
