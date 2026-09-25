#!/usr/bin/env python3
"""Generate and time-safely publish temporary observation requests."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import (
    write_text_lf,
    REQUEST_COLUMNS,
    REQUEST_TILE_COLUMNS,
    TILE_COLUMNS,
    format_utc,
    parse_utc,
    read_exact_csv,
    sha256_file,
    write_exact_csv,
)
from .observing_calendar import Night, load_nights
from .project_paths import CONFIG_DIR, REFERENCE_OUTPUT_DIR


SCHEMA_VERSION = "observation-requests-v1"


@dataclass(frozen=True)
class ObservationRequest:
    request_id: str
    issued_at_utc: datetime
    available_from_utc: datetime
    deadline_utc: datetime
    deadline_class: str
    completion_mode: str
    required_tile_count: int
    completion_reward: float
    miss_penalty: float
    reason: str

    def public_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "issued_at_utc": format_utc(self.issued_at_utc),
            "available_from_utc": format_utc(self.available_from_utc),
            "deadline_utc": format_utc(self.deadline_utc),
            "deadline_class": self.deadline_class,
            "completion_mode": self.completion_mode,
            "required_tile_count": self.required_tile_count,
            "completion_reward": round(self.completion_reward, 6),
            "miss_penalty": round(self.miss_penalty, 6),
            "reason": self.reason,
        }


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported request schema_version")
    if int(config["issue_every_n_nights"]) < 1 or not 0 <= float(config["occurrence_probability"]) <= 1:
        raise ValueError("invalid request cadence or occurrence probability")
    low, high = map(int, config["tiles_per_request"])
    if not 1 <= low <= high:
        raise ValueError("invalid tiles_per_request")
    if set(config["completion_modes"]) != {"ALL", "AT_LEAST_N"}:
        raise ValueError("completion_modes must contain ALL and AT_LEAST_N")
    if not config["deadline_classes"] or any(int(item["days"]) < 1 or float(item["weight"]) <= 0 for item in config["deadline_classes"].values()):
        raise ValueError("invalid deadline classes")
    return config


def _weighted_choice(rng: random.Random, weights: Mapping[str, float]) -> str:
    threshold = rng.random() * sum(float(value) for value in weights.values())
    total = 0.0
    for key, value in weights.items():
        total += float(value)
        if threshold <= total:
            return key
    return next(reversed(weights))


def load_requests(path: Path) -> list[ObservationRequest]:
    result = []
    seen = set()
    for row in read_exact_csv(path, REQUEST_COLUMNS):
        request = ObservationRequest(
            row["request_id"], parse_utc(row["issued_at_utc"]), parse_utc(row["available_from_utc"]),
            parse_utc(row["deadline_utc"]), row["deadline_class"], row["completion_mode"],
            int(row["required_tile_count"]), float(row["completion_reward"]), float(row["miss_penalty"]), row["reason"],
        )
        if not request.request_id or request.request_id in seen or request.deadline_utc <= request.available_from_utc:
            raise ValueError(f"invalid request {request.request_id!r}")
        seen.add(request.request_id)
        result.append(request)
    return result


def load_request_tiles(path: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in read_exact_csv(path, REQUEST_TILE_COLUMNS):
        request_id, tile_id = row["request_id"], row["tile_id"]
        visits = int(row["required_visits"])
        if visits < 1 or tile_id in result.setdefault(request_id, {}):
            raise ValueError(f"invalid request tile row for {request_id}/{tile_id}")
        result[request_id][tile_id] = visits
    return result


def generate_schedule(config: Mapping, nights: Sequence[Night], tile_rows: Sequence[Mapping[str, str]]) -> tuple[list[ObservationRequest], list[dict[str, object]]]:
    rng = random.Random(int(config["seed"]) + 4000)
    candidates = [row for row in tile_rows if parse_utc(row["available_until_utc"]) > nights[0].observing_start_utc]
    requests = []
    links = []
    cadence = int(config["issue_every_n_nights"])
    for issue_night in nights[::cadence]:
        if rng.random() >= float(config["occurrence_probability"]):
            continue
        active = [row for row in candidates if parse_utc(row["available_from_utc"]) <= issue_night.observing_start_utc < parse_utc(row["available_until_utc"])]
        if not active:
            continue
        low, high = map(int, config["tiles_per_request"])
        selected = rng.sample(active, min(len(active), rng.randint(low, high)))
        request_id = f"RQ{len(requests) + 1:04d}"
        deadline_class = _weighted_choice(rng, {key: item["weight"] for key, item in config["deadline_classes"].items()})
        days = int(config["deadline_classes"][deadline_class]["days"])
        completion_mode = _weighted_choice(rng, config["completion_modes"])
        required_count = len(selected) if completion_mode == "ALL" else rng.randint(1, len(selected))
        requests.append(ObservationRequest(
            request_id, issue_night.observing_start_utc, issue_night.observing_start_utc,
            issue_night.observing_start_utc + timedelta(days=days), deadline_class,
            completion_mode, required_count,
            float(config["reward_per_required_tile"]) * required_count,
            float(config["miss_penalty_per_required_tile"]) * required_count,
            rng.choice(config["reasons"]),
        ))
        for row in sorted(selected, key=lambda value: value["tile_id"]):
            links.append({"request_id": request_id, "tile_id": row["tile_id"], "required_visits": 1})
    return requests, links


class ObservationRequestSimulator:
    """Participant-safe request publication; the pre-generated future stays hidden."""

    def __init__(self, requests: Sequence[ObservationRequest], request_tiles: Mapping[str, Mapping[str, int]]) -> None:
        self.requests = sorted(requests, key=lambda item: (item.issued_at_utc, item.request_id))
        self.request_tiles = {key: dict(value) for key, value in request_tiles.items()}
        known = {item.request_id for item in requests}
        if set(request_tiles) != known:
            raise ValueError("request and request-tile IDs do not match")

    @classmethod
    def from_files(cls, requests_path: Path, request_tiles_path: Path) -> "ObservationRequestSimulator":
        return cls(load_requests(requests_path), load_request_tiles(request_tiles_path))

    def get_observation_requests(self, as_of_utc: datetime, include_expired: bool = False) -> list[dict[str, object]]:
        rows = []
        for request in self.requests:
            if request.issued_at_utc > as_of_utc or (not include_expired and request.deadline_utc <= as_of_utc):
                continue
            payload = request.public_dict()
            payload["tile_requirements"] = [
                {"tile_id": tile_id, "required_visits": visits}
                for tile_id, visits in sorted(self.request_tiles[request.request_id].items())
            ]
            rows.append(payload)
        return rows


def generate(config_path: Path, nights_path: Path, tiles_path: Path, output_dir: Path) -> dict[str, object]:
    config = load_config(config_path)
    nights = load_nights(nights_path)
    tiles = read_exact_csv(tiles_path, TILE_COLUMNS)
    requests, links = generate_schedule(config, nights, tiles)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {"requests": output_dir / "observation_requests.csv", "request_tiles": output_dir / "observation_request_tiles.csv"}
    write_exact_csv(paths["requests"], REQUEST_COLUMNS, (item.public_dict() for item in requests))
    write_exact_csv(paths["request_tiles"], REQUEST_TILE_COLUMNS, links)
    metadata = {
        "schema_version": "observation-request-metadata-v1", "seed": int(config["seed"]),
        "row_counts": {"requests": len(requests), "request_tiles": len(links)},
        "deadline_class_counts": dict(sorted(Counter(item.deadline_class for item in requests).items())),
        "sha256": {"config": sha256_file(config_path), "nights": sha256_file(nights_path), "tiles": sha256_file(tiles_path), **{key: sha256_file(path) for key, path in paths.items()}},
    }
    write_text_lf(output_dir / "observation_request_metadata.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "request_config.json")
    parser.add_argument("--nights", type=Path, default=REFERENCE_OUTPUT_DIR / "night_calendar.csv")
    parser.add_argument("--tiles", type=Path, default=REFERENCE_OUTPUT_DIR / "tiles.csv")
    parser.add_argument("--requests", type=Path, default=REFERENCE_OUTPUT_DIR / "observation_requests.csv")
    parser.add_argument("--request-tiles", type=Path, default=REFERENCE_OUTPUT_DIR / "observation_request_tiles.csv")
    commands = parser.add_subparsers(dest="command", required=True)
    generated = commands.add_parser("generate")
    generated.add_argument("--output-dir", type=Path, default=REFERENCE_OUTPUT_DIR)
    current = commands.add_parser("current")
    current.add_argument("--as-of-utc", required=True, type=parse_utc)
    current.add_argument("--include-expired", action="store_true")
    args = parser.parse_args()
    if args.command == "generate":
        payload = generate(args.config, args.nights, args.tiles, args.output_dir)
    else:
        payload = ObservationRequestSimulator.from_files(args.requests, args.request_tiles).get_observation_requests(args.as_of_utc, args.include_expired)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
