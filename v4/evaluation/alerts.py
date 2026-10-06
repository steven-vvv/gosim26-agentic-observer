"""Short alert requests in the style of cards A1-D1 (version 5), added to a generated card.

The organizer's request generator issues only two-night "time-critical follow-up" requests.
A1-D1 also issue alerts (gravitational-wave, gamma-ray burst, neutrino): four or five of our
targets inside an error region near the zenith, two or three of them to be completed within
three to five hours, for 300-500 points. They are appended to the card's truth request file,
issued at an open slot, so the engine publishes and settles them like any other request.
"""

from __future__ import annotations

import csv
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

REWARDS = (300.0, 350.0, 400.0, 450.0, 500.0)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _sidereal(moment: datetime, longitude: float) -> float:
    days = moment.timestamp() / 86400.0 + 2440587.5 - 2451545.0
    return (280.46061837 + 360.98564736629 * days + longitude) % 360.0


def _separation(ra1, dec1, ra2, dec2) -> float:
    a1, d1, a2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cosine = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def add_alerts(card: Path, seed: int, every_nights: float = 12.0) -> int:
    rng = random.Random(seed)
    site = json.loads((card / "config" / "v4_fiber_config.json").read_text(encoding="utf-8"))["site"]
    latitude, longitude = float(site["latitude_deg"]), float(site["longitude_deg"])
    targets = [(row["target_id"], float(row["ra_deg"]), float(row["dec_deg"]))
               for row in csv.DictReader((card / "public" / "targets.csv").open(encoding="utf-8"))]
    open_slots: dict[str, list[datetime]] = {}
    for row in csv.DictReader((card / "truth" / "v4_weather_truth.csv").open(encoding="utf-8")):
        if row["is_observable"] == "true":
            open_slots.setdefault(row["night_id"], []).append(_utc(row["timestamp_utc"]))
    nights = sorted(open_slots)
    path = card / "truth" / "v4_observation_requests.jsonl"
    requests = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    added = 0
    for _ in range(max(1, int(len(nights) / every_nights))):
        night = rng.choice(nights)
        slots = open_slots[night]
        issued = rng.choice(slots[: max(1, 2 * len(slots) // 3)])
        centre_ra = (_sidereal(issued, longitude) + rng.uniform(-20.0, 20.0)) % 360.0
        centre_dec = max(-89.0, min(89.0, latitude + rng.uniform(-15.0, 15.0)))
        radius = rng.uniform(3.0, 4.0)
        inside = [target_id for target_id, ra, dec in targets
                  if _separation(ra, dec, centre_ra, centre_dec) <= radius]
        count = 5 if rng.random() < 0.15 else 4
        if len(inside) < count:
            continue
        chosen = rng.sample(inside, count)
        requests.append({
            "completion_factor_threshold": 0.5,
            "completion_reward": rng.choice(REWARDS),
            "deadline_utc": (issued + timedelta(hours=rng.choice((3, 3, 3, 4, 5)))).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "issued_at_utc": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "minimum_completed": 3 if count == 5 else 2,
            "reason": "alert follow-up",
            "record_type": "observation_request",
            "request_id": f"V4RQ{2001 + added:04d}",
            "schema_version": "v4-observation-request-v1",
            "target_ids": chosen,
        })
        added += 1
    requests.sort(key=lambda row: (row["issued_at_utc"], row["request_id"]))
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in requests), encoding="utf-8")
    return added
