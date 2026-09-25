"""Generate the self-contained ``decision_replay.html`` for a scored run.

The challenge package ships the visualization but not its generator. This module rebuilds the
``const DATA = {...}`` payload from a ``score-report-v3`` file plus the public scenario root and
injects it into ``challenge/templates/decision_replay.html`` (the shipped sample with the literal
replaced by the ``__REPLAY_DATA__`` token; the inline JavaScript is untouched).

DATA fields the template JavaScript actually reads (grep of the inline <script>)::

  site.latitude_deg, site.longitude_deg                        alt-az projection of every tile
  tiles[].tile_id, ra_deg, dec_deg, region_id,
          scheduling_class, nominal_exptime_seconds            sky dots, REQUIRED rings, decision panel
  nights[<night_id>].tile_status[].tile_id,
          .windows[].window_start_utc / window_end_utc         "window open" colouring at decision time
  nights[<night_id>].forecasts[].condition, probability,
          predicted_start_utc, affected_region_ids             "seven-day signal" list
  events[<event_id>].condition                                 names for active event ids
  rounds[].round_id, decision_time_utc, slot_id, night_id      timeline, clock, night counter
  rounds[].weather.is_observable, seeing_arcsec, transparency,
          sky_quality, instrument_efficiency, active_event_ids,
          regional_overrides[].active_event_ids                telemetry bars, banner, particle effects
  rounds[].decision.action, tile_id, program, reason           decision panel
  rounds[].transition.outcome                                  OUTCOME row and the observed-tile set
  rounds[].score.base_science_score, program_bonus_score,
          penalty                                              action delta and running totals
  score_summary.science_score, total_penalty                   totals shown on the final round

Carried for schema parity with the sample but not read by the JavaScript: ``meta.*``,
``site.utc_offset_hours``, ``site.sun_altitude_limit_deg``, ``tile_status[].is_available_tonight``,
``nights[].forecast_snapshot``, ``events[].regions`` / ``severity``,
``transition.start_time_utc`` / ``end_time_utc`` / ``elapsed_seconds`` / ``next_slot_id``,
``score.outcome`` and ``score_summary.score`` / ``base_science_score`` / ``program_bonus_score``.

Derivation of every field:

  meta            title argument; counts of rounds, nights and tiles below.
  site            ``config/calendar_config.json`` ``site`` block.
  tiles           ``outputs/reference/tiles.csv`` in file order (public columns only).
  nights          one entry per night touched by the rounds, in order of first appearance:
                  tile_status  = ``TileGeometrySimulator.get_tile_windows(night_date, 1)`` grouped
                                 per tile (``is_available_tonight`` == has a legal window tonight);
                  forecast_snapshot / forecasts = ``WeatherSimulator.get_weather_forecast`` as of the
                                 night's ``observing_start_utc`` with the workflow weekly horizon.
  events          organizer ``weather_events.csv`` reduced to the anonymised
                  ``{condition, regions, severity}`` per event id, in file order; ``regions`` is the
                  sorted set of region ids the event actually overrode during the replayed rounds.
                  No timestamps, scope payloads, multipliers or force-close flags are exposed.
  rounds          one per ``report["actions"]`` entry:
                  round_id = 1-based position; decision_time_utc = action ``start_utc``;
                  weather  = ``get_effective_conditions(slot_id)`` (site weather + site-wide
                             ``active_event_ids``) plus ``regional_overrides`` = per-region list of
                             directional events that apply to at least one tile of that region;
                  decision = action fields plus the ``reason`` text of the matching ``decisions.csv``
                             row (empty when no decisions file is supplied);
                  transition = start_utc, start_utc + elapsed_seconds, elapsed_seconds, outcome and
                             the following action's slot id (``""`` after the last action);
                  score    = outcome, base_science_score, program_bonus_score, penalty.
  score_summary   ``report["score"]``: total, base + bonus + request_reward, base, bonus and the sum of
                  all penalty buckets.
"""

from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import DECISION_COLUMNS, format_utc, parse_utc, read_exact_csv, write_text_lf
from .scoring_core import ChallengeScorer
from .tile_geometry_simulator import Tile
from .weather_simulator import WeatherEvent, _azimuth_inside, _separation_deg


SCORE_REPORT_VERSION = "score-report-v3"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "decision_replay.html"
DATA_PLACEHOLDER = "__REPLAY_DATA__"
DEFAULT_TITLE = "ASTRA // example3 weekly decision replay"
DEFAULT_AGENT_LABEL = "survey agent · decision replay"
# Header strings baked into the shipped template; ``render_replay_html`` swaps them for the run's own.
TEMPLATE_TITLE = "<title>ASTRA // example3 weekly decision replay</title>"
TEMPLATE_SUBTITLE = '<div class="subtitle">DeepSeek minimal-agent · seven-night replay</div>'
WEATHER_FIELDS = ("is_observable", "seeing_arcsec", "transparency", "sky_quality", "instrument_efficiency")
SITE_FIELDS = ("latitude_deg", "longitude_deg", "utc_offset_hours", "sun_altitude_limit_deg")


def _load_json(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _horizon_days(root: Path, scorer: ChallengeScorer) -> int:
    workflow_config = root / "config" / "workflow_config.json"
    if workflow_config.exists():
        return int(_load_json(workflow_config)["weekly_horizon_days"])
    return int(scorer.weather.config["forecast"]["horizon_days"])


def load_reasons(decisions_path: Path) -> dict[str, str]:
    """Map decision_id -> reason text from a ``decisions.csv`` trace."""
    return {row["decision_id"]: row["reason"] for row in read_exact_csv(Path(decisions_path), DECISION_COLUMNS)}


def _public_tile(tile: Tile) -> dict[str, object]:
    return {
        "tile_id": tile.tile_id,
        "ra_deg": tile.ra_deg,
        "dec_deg": tile.dec_deg,
        "region_id": tile.region_id,
        "scheduling_class": tile.scheduling_class,
        "nominal_exptime_seconds": tile.nominal_exptime_seconds,
    }


def _scope_regions(scope_type: str, payload: Mapping, when: datetime, scorer: ChallengeScorer, tiles: Sequence[Tile]) -> list[str]:
    """Region ids a forecast scope touches, resolved against the public tile catalog."""
    if scope_type == "ALL":
        return sorted({tile.region_id for tile in tiles})
    if scope_type == "REGION_SET":
        return sorted(str(region) for region in payload["region_ids"])
    if scope_type == "TILE_SET":
        wanted = set(payload["tile_ids"])
        return sorted({tile.region_id for tile in tiles if tile.tile_id in wanted})
    if scope_type == "SKY_CAP_ICRS":
        return sorted({
            tile.region_id for tile in tiles
            if _separation_deg(tile.ra_deg, tile.dec_deg, float(payload["ra_deg"]), float(payload["dec_deg"])) <= float(payload["radius_deg"])
        })
    if scope_type == "HORIZON_SECTOR":
        regions = set()
        for tile in tiles:
            sample = scorer.geometry.get_tile_geometry(tile.tile_id, when)
            inside = float(payload["min_altitude_deg"]) <= float(sample["altitude_deg"]) <= float(payload["max_altitude_deg"])
            if inside and _azimuth_inside(float(sample["azimuth_deg"]), float(payload["azimuth_start_deg"]), float(payload["azimuth_end_deg"])):
                regions.add(tile.region_id)
        return sorted(regions)
    return []


def _public_forecast(item: Mapping[str, object], scorer: ChallengeScorer, tiles: Sequence[Tile]) -> dict[str, object]:
    start = parse_utc(str(item["predicted_start_utc"]))
    end = parse_utc(str(item["predicted_end_utc"]))
    payload = json.loads(str(item["spatial_scope_payload"]))
    return {
        "forecast_id": item["forecast_id"],
        "event_id": item["event_id"],
        "revision": item["revision"],
        "condition": item["condition"],
        "severity": item["severity"],
        "probability": item["probability"],
        "predicted_start_utc": item["predicted_start_utc"],
        "predicted_end_utc": item["predicted_end_utc"],
        "spatial_scope_type": item["spatial_scope_type"],
        "affected_region_ids": _scope_regions(str(item["spatial_scope_type"]), payload, start + (end - start) / 2, scorer, tiles),
    }


def _regional_overrides(scorer: ChallengeScorer, slot_id: str, tiles: Sequence[Tile], site_event_ids: set[str]) -> list[dict[str, object]]:
    slot = scorer.slots[scorer.slot_indices[slot_id]]
    directional = [
        event for event in scorer.weather.events
        if event.spatial_scope_type != "ALL" and event.actual_start_utc < slot.end_utc and event.actual_end_utc > slot.timestamp_utc
    ]
    if not directional:
        return []
    by_region: dict[str, dict[str, object]] = {}
    for tile in tiles:
        conditions = scorer.weather.get_effective_conditions(slot_id, tile.tile_id)
        extra = [event_id for event_id in conditions["active_event_ids"] if event_id not in site_event_ids]
        if not extra:
            continue
        entry = by_region.setdefault(tile.region_id, {"region_id": tile.region_id, "active_event_ids": [], "affected_tile_ids": [], "is_observable": True})
        for event_id in extra:
            if event_id not in entry["active_event_ids"]:
                entry["active_event_ids"].append(event_id)
        entry["affected_tile_ids"].append(tile.tile_id)
        entry["is_observable"] = bool(entry["is_observable"]) and bool(conditions["is_observable"])
    return [by_region[region_id] for region_id in sorted(by_region)]


def _slot_weather(scorer: ChallengeScorer, slot_id: str, tiles: Sequence[Tile]) -> dict[str, object]:
    site = scorer.weather.get_effective_conditions(slot_id)
    weather: dict[str, object] = {key: site[key] for key in WEATHER_FIELDS}
    weather["active_event_ids"] = list(site["active_event_ids"])
    weather["regional_overrides"] = _regional_overrides(scorer, slot_id, tiles, set(site["active_event_ids"]))
    return weather


def _build_rounds(scorer: ChallengeScorer, actions: Sequence[Mapping[str, object]], reasons: Mapping[str, str], tiles: Sequence[Tile]) -> tuple[list[dict[str, object]], dict[str, set[str]]]:
    rounds: list[dict[str, object]] = []
    event_regions: defaultdict[str, set[str]] = defaultdict(set)
    for index, action in enumerate(actions):
        slot_id = str(action["slot_id"])
        if slot_id not in scorer.slot_indices:
            raise ValueError(f"action {index + 1} references unknown slot {slot_id!r}; wrong scenario root?")
        start = parse_utc(str(action["start_utc"]))
        elapsed = int(action["elapsed_seconds"])
        weather = _slot_weather(scorer, slot_id, tiles)
        for override in weather["regional_overrides"]:
            for event_id in override["active_event_ids"]:
                event_regions[str(event_id)].add(str(override["region_id"]))
        rounds.append({
            "round_id": index + 1,
            "decision_time_utc": format_utc(start),
            "slot_id": slot_id,
            "night_id": scorer.slots[scorer.slot_indices[slot_id]].night_id,
            "weather": weather,
            "decision": {
                "action": str(action["action"]),
                "tile_id": str(action.get("tile_id", "")),
                "program": str(action.get("program", "")),
                "reason": reasons.get(str(action.get("decision_id", "")), ""),
            },
            "transition": {
                "start_time_utc": format_utc(start),
                "end_time_utc": format_utc(start + timedelta(seconds=elapsed)),
                "elapsed_seconds": elapsed,
                "outcome": str(action["outcome"]),
                "next_slot_id": str(actions[index + 1]["slot_id"]) if index + 1 < len(actions) else "",
            },
            "score": {
                "outcome": str(action["outcome"]),
                "base_science_score": float(action.get("base_science_score", 0.0)),
                "program_bonus_score": float(action.get("program_bonus_score", 0.0)),
                "penalty": float(action.get("penalty", 0.0)),
            },
        })
    return rounds, dict(event_regions)


def _build_nights(scorer: ChallengeScorer, night_ids: Sequence[str], tiles: Sequence[Tile], horizon_days: int) -> dict[str, dict[str, object]]:
    nights: dict[str, dict[str, object]] = {}
    for night_id in night_ids:
        night = scorer.geometry.nights[night_id]
        windows: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
        for row in scorer.geometry.get_tile_windows(night.night_date, 1):
            windows[str(row["tile_id"])].append({"window_start_utc": row["window_start_utc"], "window_end_utc": row["window_end_utc"]})
        as_of = night.observing_start_utc
        forecasts = [_public_forecast(item, scorer, tiles) for item in scorer.weather.get_weather_forecast(as_of, horizon_days)]
        nights[night_id] = {
            "tile_status": [
                {"tile_id": tile.tile_id, "is_available_tonight": bool(windows.get(tile.tile_id)), "windows": windows.get(tile.tile_id, [])}
                for tile in tiles
            ],
            "forecast_snapshot": {"as_of_utc": format_utc(as_of), "horizon_days": horizon_days, "forecast_count": len(forecasts)},
            "forecasts": forecasts,
        }
    return nights


def _build_events(events: Sequence[WeatherEvent], event_regions: Mapping[str, set[str]]) -> dict[str, dict[str, object]]:
    return {
        event.event_id: {"condition": event.condition, "regions": sorted(event_regions.get(event.event_id, ())), "severity": round(event.severity, 6)}
        for event in events
    }


def _score_summary(report: Mapping[str, object]) -> dict[str, float]:
    score = report.get("score") or {}
    base = float(score.get("base_science", 0.0))
    bonus = float(score.get("program_bonus", 0.0))
    reward = float(score.get("request_reward", 0.0))
    total_penalty = sum(float(value) for value in (score.get("penalties") or {}).values())
    total = float(score.get("total", base + bonus + reward - total_penalty))
    return {
        "score": round(total, 6),
        "science_score": round(base + bonus + reward, 6),
        "base_science_score": round(base, 6),
        "program_bonus_score": round(bonus, 6),
        "total_penalty": round(total_penalty, 6),
    }


def unwrap_report(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Accept either a bare score report or a workflow result that embeds one."""
    if "actions" not in payload and isinstance(payload.get("score_report"), Mapping):
        payload = payload["score_report"]
    version = payload.get("schema_version")
    if version not in (None, SCORE_REPORT_VERSION):
        raise ValueError(f"unsupported score report schema_version {version!r}")
    if not isinstance(payload.get("actions"), list):
        raise ValueError("score report has no actions list")
    return payload


def build_replay_data(root: Path, report: dict, *, title: str, agent_label: str, decisions_path: Path | None = None) -> dict:
    """Build the DATA payload consumed by the replay template.

    ``agent_label`` is not part of DATA (the sample carries it only in the HTML header); it is accepted
    here so callers can pass one keyword set to both this function and ``write_replay_html``.
    """
    del agent_label
    root = Path(root)
    report = unwrap_report(report)
    scorer = ChallengeScorer.from_files(root)
    tiles = list(scorer.tiles.values())
    reasons = load_reasons(decisions_path) if decisions_path else {}
    rounds, event_regions = _build_rounds(scorer, report["actions"], reasons, tiles)
    night_ids = list(dict.fromkeys(str(item["night_id"]) for item in rounds))
    nights = _build_nights(scorer, night_ids, tiles, _horizon_days(root, scorer))
    site = _load_json(root / "config" / "calendar_config.json")["site"]
    return {
        "meta": {"title": title, "round_count": len(rounds), "night_count": len(nights), "tile_count": len(tiles)},
        "site": {key: float(site[key]) for key in SITE_FIELDS},
        "tiles": [_public_tile(tile) for tile in tiles],
        "nights": nights,
        "events": _build_events(scorer.weather.events, event_regions),
        "rounds": rounds,
        "score_summary": _score_summary(report),
    }


def serialize_replay_data(data: Mapping[str, object]) -> str:
    """Compact JSON literal that is safe to embed inside an inline <script> block."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")


def render_replay_html(data: Mapping[str, object], *, title: str, agent_label: str, template_path: Path = TEMPLATE_PATH) -> str:
    template = Path(template_path).read_text(encoding="utf-8")
    if DATA_PLACEHOLDER not in template:
        raise ValueError(f"template {template_path} lacks the {DATA_PLACEHOLDER} token")
    page = template.replace(DATA_PLACEHOLDER, serialize_replay_data(data), 1)
    page = page.replace(TEMPLATE_TITLE, f"<title>{html.escape(title)}</title>", 1)
    return page.replace(TEMPLATE_SUBTITLE, f'<div class="subtitle">{html.escape(agent_label)}</div>', 1)


def write_replay_html(root: Path, report: dict, out_path: Path, *, title: str = DEFAULT_TITLE, agent_label: str = DEFAULT_AGENT_LABEL,
                      decisions_path: Path | None = None, template_path: Path = TEMPLATE_PATH) -> Path:
    data = build_replay_data(root, report, title=title, agent_label=agent_label, decisions_path=decisions_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out_path, render_replay_html(data, title=title, agent_label=agent_label, template_path=template_path))
    return out_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Render the self-contained decision replay HTML for a scored run.")
    parser.add_argument("--root", type=Path, required=True, help="scenario root holding config/ and outputs/reference/")
    parser.add_argument("--report", type=Path, required=True, help="score_report.json (score-report-v3) or a workflow_result.json embedding one")
    parser.add_argument("--output", type=Path, required=True, help="destination .html path")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="page title and DATA.meta.title")
    parser.add_argument("--agent-label", default=DEFAULT_AGENT_LABEL, help="header subtitle naming the agent / run")
    parser.add_argument("--decisions", type=Path, help="decisions.csv providing reason text; defaults to decisions.csv beside --report when present")
    args = parser.parse_args(argv)
    decisions = args.decisions
    if decisions is None and (args.report.parent / "decisions.csv").exists():
        decisions = args.report.parent / "decisions.csv"
    output = write_replay_html(args.root, _load_json(args.report), args.output, title=args.title, agent_label=args.agent_label, decisions_path=decisions)
    print(output)


if __name__ == "__main__":
    main()
