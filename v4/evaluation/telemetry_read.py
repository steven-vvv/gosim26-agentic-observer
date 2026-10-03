"""Summarise observer-v4 telemetry stored by the Worker (read API), without printing credentials.

The read token and Worker URL are loaded inside this process from the private env file
(``/home/steven/.config/gosim26-telemetry.env``, outside the repository); only event counts
and selected non-secret payload fields are printed.
"""

from __future__ import annotations

import argparse
import json
import shlex
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ENV = Path("/home/steven/.config/gosim26-telemetry.env")


def _env() -> dict[str, str]:
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export ") and "=" in line:
            key, raw = line[len("export "):].split("=", 1)
            parsed = shlex.split(raw)
            values[key] = parsed[0] if parsed else ""
    return values


def fetch(since: int | None, limit: int, pages: int) -> list[dict]:
    env = _env()
    base = env["TELEMETRY_URL"].rstrip("/")
    base = base.removesuffix("/v1")
    token = env["TELEMETRY_READ_TOKEN"]
    events, cursor = [], None
    for _ in range(pages):
        query = {"limit": str(limit)}
        if cursor:
            query["cursor"] = cursor
        request = urllib.request.Request(f"{base}/v1/telemetry?{urllib.parse.urlencode(query)}",
                                         headers={"Authorization": f"Bearer {token}", "User-Agent": "gosim26-telemetry-read"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read())
        batch = body.get("events") or body.get("items") or []
        events.extend(batch)
        cursor = body.get("next_cursor") or body.get("cursor")
        if not cursor or not batch or (since and min(int(e.get("received_at_ms", 0)) for e in batch) < since):
            break
    if since:
        events = [e for e in events if int(e.get("received_at_ms", 0)) >= since]
    return events


def summarise(events: list[dict]) -> None:
    runs = defaultdict(list)
    for event in events:
        if event.get("source") == "observer-v4":
            runs[event.get("run_id")].append(event)
    print(json.dumps({"events": len(events), "observer_v4_runs": len(runs)}))
    for run_id, items in sorted(runs.items(), key=lambda kv: min(int(e.get("received_at_ms", 0)) for e in kv[1])):
        kinds = Counter(e.get("event_type") for e in items)
        first = min(int(e.get("received_at_ms", 0)) for e in items) / 1000
        info = {"run": str(run_id)[:8], "first_utc": datetime.fromtimestamp(first, tz=timezone.utc).strftime("%m-%d %H:%M:%S"),
                "events": len(items), "types": dict(kinds)}
        for event in items:
            payload = event.get("payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            kind = event.get("event_type")
            if kind == "runtime":
                info["cpu"] = payload.get("cpu_model")
                info["cpus"] = payload.get("cpu_count")
                info["python"] = payload.get("python")
            elif kind == "replay_selected":
                info["replay"] = payload.get("card")
            elif kind == "initialize":
                info["card"] = (payload.get("card") or {}).get("scenario_slug")
            elif kind == "final":
                info["wallclock"] = payload.get("wallclock_seconds")
                info["termination"] = payload.get("termination_reason")
            network = event.get("server_network") or {}
            if network.get("country") and "country" not in info:
                info["country"] = network.get("country")
                info["colo"] = network.get("colo")
        print(json.dumps(info, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO time (UTC); only events received at or after it")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--pages", type=int, default=10)
    args = parser.parse_args()
    since = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp() * 1000) if args.since else None
    summarise(fetch(since, args.limit, args.pages))


if __name__ == "__main__":
    main()
