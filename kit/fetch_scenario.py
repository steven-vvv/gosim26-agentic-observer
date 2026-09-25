#!/usr/bin/env python3
"""Download a public platform scenario into the kit's scenarios/ folder, in the layout local_runner.py expects.

    python3 fetch_scenario.py --list
    python3 fetch_scenario.py dev-fortnight                 # -> scenarios/dev-fortnight/{config,outputs/reference}
    python3 fetch_scenario.py dev-fortnight --out /tmp/x    # custom target directory

Uses the platform's public storage API; the URL and key default to the values printed in SKILL.md / the
Resources page (or SAC_URL / SAC_KEY in the environment). Only the files the scenario publishes are fetched:
weather.csv, weather_forecasts.csv and weather_events.csv exist only for public-weather practice scenarios.
Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
DEFAULT_URL = os.environ.get("SAC_URL") or "https://vdiemcofukuxglqsmlyz.supabase.co"
DEFAULT_KEY = os.environ.get("SAC_KEY") or "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkaWVtY29mdWt1eGdscXNtbHl6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5NTU3OTcsImV4cCI6MjEwNDUzMTc5N30.qMbZ_C3iKq70SdGDYQPPUD0LMBst-xBspZ-zj_gn75w"
CONFIG_FILES = ("scenario_config.json", "calendar_config.json", "tile_config.json", "weather_config.json", "request_config.json",
                "workflow_config.json", "score_config.json")
ALWAYS = ("night_calendar.csv", "slots.csv", "tiles.csv", "targets.csv", "tile_windows.csv", "observation_requests.csv",
          "observation_request_tiles.csv", "scenario_manifest.json", "calendar_metadata.json", "catalog_metadata.json",
          "observation_request_metadata.json")
FLAGGED = {"weather.csv": "weather_public", "weather_metadata.json": "weather_public", "weather_forecasts.csv": "forecasts_public",
           "weather_events.csv": "events_public"}


def get(url: str, key: str, path: str) -> bytes:
    req = urllib.request.Request(url.rstrip("/") + path, headers={"apikey": key, "Authorization": f"Bearer {key}"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
        except (http.client.HTTPException, urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:  # transient (IncompleteRead, resets)
            if attempt == 3:
                raise SystemExit(f"{path}: {e}")
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit("unreachable")


def scenarios(url: str, key: str) -> list[dict]:
    raw = get(url, key, "/rest/v1/scenarios?select=slug,name,description,n_nights,n_slots,n_tiles,n_requests,global_wallclock_seconds,"
                        "weather_public,forecasts_public,events_public,is_active&order=slug")
    return [s for s in json.loads(raw) if s.get("is_active", True)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug", nargs="?", help="scenario slug, e.g. dev-fortnight")
    ap.add_argument("--list", action="store_true", help="list the scenarios the platform publishes")
    ap.add_argument("--out", type=Path, default=None, help="target directory (default: scenarios/<slug>)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--key", default=DEFAULT_KEY)
    args = ap.parse_args(argv)
    if "{{" in args.url or "{{" in args.key:
        raise SystemExit("set SAC_URL and SAC_KEY (or --url / --key) to the values shown on the platform's Resources page")
    rows = scenarios(args.url, args.key)
    if args.list or not args.slug:
        for s in rows:
            vis = "weather public" if s["weather_public"] else "weather hidden (platform runs only)"
            print(f"{s['slug']:<16} {s.get('n_nights') or '?':>4} nights  {s.get('n_slots') or '?':>5} slots  wall clock {s.get('global_wallclock_seconds') or '?'} s  {vis}")
        return 0
    meta = next((s for s in rows if s["slug"] == args.slug), None)
    if meta is None:
        raise SystemExit(f"unknown scenario {args.slug!r}; --list shows the published ones")
    out = args.out or (KIT_ROOT / "scenarios" / args.slug)
    files = [f"config/{n}" for n in CONFIG_FILES] + [f"outputs/reference/{n}" for n in ALWAYS]
    skipped = []
    for name, flag in FLAGGED.items():
        (files if meta.get(flag) else skipped).append(f"outputs/reference/{name}")
    for rel in files:
        data = get(args.url, args.key, f"/storage/v1/object/scenarios/{args.slug}/{urllib.parse.quote(rel)}")
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"  {rel:<48} {len(data):>9} bytes")
    manifest = json.loads((out / "outputs/reference/scenario_manifest.json").read_text(encoding="utf-8"))
    bad = [rel for rel, info in manifest.get("files", {}).items() if (out / rel).exists() and hashlib.sha256((out / rel).read_bytes()).hexdigest() != info.get("sha256")]
    if bad:
        raise SystemExit("checksum mismatch: " + ", ".join(bad))
    print(f"wrote {len(files)} files to {out} (checksums verified)")
    if skipped:
        print("not published for this scenario: " + ", ".join(skipped) + "  -> it can only be scored by the platform (submit an agent package)")
    else:
        print(f"next: python3 local_runner.py --scenario {out} --agent agent/minimal_agent.py --out run_{args.slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
