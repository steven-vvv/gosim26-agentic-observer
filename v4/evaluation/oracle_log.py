"""Evaluation-only oracle for the duty log: what the agent would do if it read the log perfectly.

Cards A1-D1 announce instrument faults and terrain heights in their observation-request
texts; research cards carry no such text. An experiment variant may add an ``oracle``
section, which is removed before the agent configuration is validated and never reaches
the submitted agent: the truth fault starts are handed to the agent's log book a fixed
delay after each fault begins (as if a log entry named it then) or, with ``appointments``,
ahead of time as the A1-D1 engineers announce them; ``quiet`` adds the truth flat-lamp
windows of the synthetic cards; and the truth terrain heights are pinned once the terrain
model exists.
"""

from __future__ import annotations

import csv
from pathlib import Path

from v4.agent.observer4.geometry import parse_utc
from v4.agent.observer4.policy import Observer
from v4.agent.observer4.terrain import DIRECTIONS


def _nearest(azimuth: float) -> str:
    return min(DIRECTIONS, key=lambda name: abs((azimuth - DIRECTIONS[name] + 180.0) % 360.0 - 180.0))


def oracle_factory(card: Path, oracle: dict, config: dict):
    rows = list(csv.DictReader((card / "truth" / "v4_events.csv").open(encoding="utf-8")))
    faults = sorted(parse_utc(row["actual_start_utc"]).timestamp() for row in rows
                    if row["event_type"] == "instrument_fault")
    heights: dict[str, float] = {}
    for row in rows:
        if row["event_type"] != "terrain_obstruction":
            continue
        start, end = float(row["azimuth_start_deg"]), float(row["azimuth_end_deg"])
        centre = (start + ((end - start) % 360.0) / 2.0) % 360.0
        direction = _nearest(centre)
        heights[direction] = max(heights.get(direction, 0.0), float(row["max_altitude_deg"]))
    delay = oracle.get("fault_delay_hours")
    margin = oracle.get("terrain_margin_deg")
    flats = []
    windows = card / "truth" / "v4_flat_windows.csv"
    if oracle.get("quiet") and windows.exists():
        flats = [(parse_utc(row["start_utc"]).timestamp(), parse_utc(row["end_utc"]).timestamp())
                 for row in csv.DictReader(windows.open(encoding="utf-8"))]

    def build(publication, record):
        agent = Observer(publication, config, record)
        original = agent.respond
        pending = [start + float(delay) * 3600.0 for start in faults] if delay is not None else []
        agent.logbook.quiet.extend(flats)
        if oracle.get("appointments"):
            # A perfect log reader: every move announced ahead, due exactly when it happens.
            agent.logbook.faults.extend({"start": start, "issued": start, "state": "open", "future": True,
                                         "sources": {"model", "rules"}, "texts": set()} for start in faults)
            pending = []
        pinned = {"done": margin is None}

        def respond(snapshot):
            now = parse_utc(snapshot["now_utc"]).timestamp()
            while pending and pending[0] <= now:
                issued = pending.pop(0)
                agent.logbook.faults.append({"start": issued - float(delay) * 3600.0, "issued": issued,
                                             "state": "open", "sources": {"model", "rules"}, "texts": set()})
            action = original(snapshot)
            model = agent.quality.terrain_model
            if not pinned["done"] and model is not None:
                for direction, top in heights.items():
                    model.pin(direction, top + float(margin))
                pinned["done"] = True
            return action

        agent.respond = respond
        return agent

    return build
