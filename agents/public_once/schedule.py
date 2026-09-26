"""Replay only schedules whose public catalog exactly matches a known scene."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path


def fingerprint(publication: dict) -> str:
    catalog = publication.get("tile_catalog") or {}
    tiles = [
        {key: tile.get(key) for key in (
            "tile_id", "ra_deg", "dec_deg", "nominal_exptime_seconds", "region_id",
            "scheduling_class", "available_from_utc", "available_until_utc", "tile_science_value",
        )}
        for tile in catalog.get("tiles") or []
    ]
    fields = {"calendar": publication.get("calendar"), "tiles": tiles}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PublicSchedule:
    def __init__(self, publication: dict, path: Path) -> None:
        match = json.loads(path.read_text(encoding="utf-8")).get(fingerprint(publication))
        if not isinstance(match, dict):
            raise KeyError("no matching public schedule")
        if not isinstance(match.get("actions"), list):
            raise TypeError("invalid public schedule actions")
        self.scenario = str(match.get("scenario", "unknown"))
        self.actions = match["actions"]
        self.index = 0
        # 新版排程（complete=true）已显式列出全部重访与注定中断观测，回放时不再临时插入重访
        self.complete = bool(match.get("complete"))

    def _filler(self, snapshot: dict, next_time: str | None) -> dict | None:
        cursor = snapshot.get("cursor") or {}
        now = datetime.fromisoformat(cursor["timestamp_utc"].replace("Z", "+00:00"))
        available = float("inf")
        if next_time:
            planned = datetime.fromisoformat(next_time.replace("Z", "+00:00"))
            available = min(available, int((planned - now).total_seconds()))
        request_tiles = {}
        for request in snapshot.get("active_requests") or []:
            if request.get("available_from_utc", "") > cursor["timestamp_utc"]:
                continue
            for requirement in request.get("tile_requirements") or []:
                request_tiles.setdefault(requirement["tile_id"], request["request_id"])
        choices = []
        for tile in snapshot.get("candidate_tiles") or []:
            tile_id = tile["tile_id"]
            if not tile.get("already_completed") or tile_id not in request_tiles:
                continue
            if not (tile.get("effective_weather") or {}).get("is_observable"):
                continue
            seconds = int(tile["nominal_exptime_seconds"])
            if seconds > available:
                continue
            if now + timedelta(seconds=seconds) > datetime.fromisoformat(tile["window_end_utc"].replace("Z", "+00:00")):
                continue
            choices.append((seconds, tile_id, request_tiles[tile_id]))
        if not choices:
            return None
        _seconds, tile_id, request_id = max(choices)
        return {"action": "observe", "tile_id": tile_id, "program": "DARK",
                "request_id": request_id, "reason": "public request revisit"}

    def decide(self, snapshot: dict) -> dict | None:
        now = (snapshot.get("cursor") or {}).get("timestamp_utc", "")
        while self.index < len(self.actions) and self.actions[self.index]["timestamp_utc"] < now:
            self.index += 1
        if self.index >= len(self.actions):
            filler = None if self.complete else self._filler(snapshot, None)
            if filler:
                return filler
            return {"action": "wait", "tile_id": "", "program": "", "request_id": "",
                    "reason": "public schedule complete"}
        action = self.actions[self.index]
        if now < action["timestamp_utc"]:
            filler = None if self.complete else self._filler(snapshot, action["timestamp_utc"])
            if filler:
                return filler
            return {"action": "wait", "tile_id": "", "program": "", "request_id": "",
                    "reason": "await public schedule"}
        self.index += 1
        # Published windows can omit a tile even when the authoritative scorer
        # considers it legal. The schedule was validated against the scorer's truth.
        request_id = action.get("request_id", "")
        return {"action": "observe", "tile_id": action["tile_id"], "program": action["program"],
                "request_id": request_id, "reason": "public schedule"}
