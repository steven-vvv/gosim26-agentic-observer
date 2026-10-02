#!/usr/bin/env python3
"""Submit an agent package (zip) or a decisions.csv results file to the Agent Observer platform (Supabase backend,
standard library only).

  # agent package built by pack_agent.py; the platform runs it on the phase's scenarios (hidden truth allowed)
  python3 sac_submit.py --url https://<ref>.supabase.co --key <anon key> --email you@x.org --password '...' \
      --phase online --kind agent --file my-agent.zip --wait

  # decisions.csv produced by local_runner.py, scored against a public scenario
  python3 sac_submit.py --url ... --key ... --email ... --password ... \
      --phase practice --kind results --scenario dev-reference --file run_output/decisions.csv --wait

The agent package is a zip with minimal_agent.py (or agent.py / main.py) at its root, plus any modules it imports,
an optional requirements.txt (installed into a fresh virtualenv before the run) and an optional .env with your LLM
provider keys (loaded into the agent process only; never shown in logs). Build it with `python3 pack_agent.py`.
A bare .py entry script is accepted too.

The URL and anon key are printed on the platform's Resources page. Environment variables SAC_URL, SAC_KEY,
SAC_EMAIL, SAC_PASSWORD are used when the flags are omitted; SAC_SITE_URL adds a clickable submission link.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path



# filled in by the kit build (web/scripts/build-kit.mjs); override with SAC_URL / SAC_KEY / SAC_SITE_URL or the flags
DEFAULT_URL = "https://vdiemcofukuxglqsmlyz.supabase.co"
DEFAULT_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkaWVtY29mdWt1eGdscXNtbHl6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5NTU3OTcsImV4cCI6MjEwNDUzMTc5N30.qMbZ_C3iKq70SdGDYQPPUD0LMBst-xBspZ-zj_gn75w"
DEFAULT_SITE_URL = "https://create.gosim.org/survey26/platform"

class Api:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key
        self.token = key

    def call(self, method: str, path: str, *, body=None, data: bytes | None = None, headers: dict | None = None):
        hdrs = {"apikey": self.key, "Authorization": f"Bearer {self.token}"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        elif data is not None:
            payload = data
        hdrs.update(headers or {})
        req = urllib.request.Request(self.url + path, data=payload, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                return json.loads(raw) if raw and "json" in resp.headers.get("Content-Type", "") else raw
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(text)
                text = detail.get("message") or detail.get("msg") or detail.get("error_description") or detail.get("error") or text
            except Exception:
                pass
            raise SystemExit(f"HTTP {exc.code} {path}: {text[:300]}")

    def login(self, email: str, password: str) -> None:
        res = self.call("POST", "/auth/v1/token?grant_type=password", body={"email": email, "password": password})
        self.token = res["access_token"]

    def rpc(self, name: str, args: dict):
        return self.call("POST", f"/rest/v1/rpc/{name}", body=args)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Submit to the Agent Observer platform.")
    p.add_argument("--url", default=os.environ.get("SAC_URL") or (None if "{{" in DEFAULT_URL else DEFAULT_URL), help="Supabase project URL")
    p.add_argument("--key", default=os.environ.get("SAC_KEY") or (None if "{{" in DEFAULT_KEY else DEFAULT_KEY), help="Supabase anon (publishable) key")
    p.add_argument("--email", default=os.environ.get("SAC_EMAIL"))
    p.add_argument("--password", default=os.environ.get("SAC_PASSWORD"))
    p.add_argument("--phase", required=True, help="phase slug shown on the website, e.g. practice or online")
    p.add_argument("--kind", choices=["results", "agent"], required=True, help="agent = zip package or .py entry script; results = decisions.csv")
    p.add_argument("--scenario", help="public scenario slug the decisions.csv was produced on (results only)")
    p.add_argument("--file", type=Path, required=True, help="my-agent.zip / entry .py (agent) or decisions.csv (results)")
    p.add_argument("--title", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--wait", action="store_true", help="poll until the evaluation finishes")
    args = p.parse_args(argv)
    for name in ("url", "key", "email", "password"):
        if not getattr(args, name):
            raise SystemExit(f"--{name} (or SAC_{name.upper()}) is required")
    if not args.file.exists():
        raise SystemExit(f"file not found: {args.file}")
    api = Api(args.url, args.key)
    api.login(args.email, args.password)
    me = api.rpc("me", {})
    if not me or not me.get("team"):
        raise SystemExit("your account is not on a team yet; create or join one on the website first")
    team_id = me["team"]["id"]
    payload = args.file.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    ext = args.file.suffix.lower() or ".bin"
    path = f"{team_id}/{int(time.time())}-{secrets.token_hex(3)}{ext}"
    ctype = "text/csv" if ext == ".csv" else ("application/zip" if ext == ".zip" else "text/x-python")
    api.call("POST", f"/storage/v1/object/submissions/{urllib.parse.quote(path)}", data=payload, headers={"Content-Type": ctype, "x-upsert": "false"})
    sid = api.rpc("create_submission", {
        "p_phase_slug": args.phase, "p_kind": args.kind, "p_scenario_slug": args.scenario, "p_storage_path": path,
        "p_filename": args.file.name, "p_sha256": sha, "p_title": args.title, "p_notes": args.notes,
    })
    site = (os.environ.get("SAC_SITE_URL") or DEFAULT_SITE_URL).rstrip("/")
    page = f"{site}/submissions/{sid}" if site and "{{" not in site else ""
    print(f"submission #{sid} queued for team {me['team']['name']}" + (f": {page}" if page else ""))
    if not args.wait:
        return 0
    last = None
    while True:
        time.sleep(4)
        rows = api.call("GET", f"/rest/v1/submissions?id=eq.{sid}&select=id,status,score,base_science,program_bonus,request_reward,penalty_total,"
                               "completed_tiles,required_missing,flexible_shortfall,termination_reason,error,"
                               "evaluations(scenarios(slug),status,score,termination_reason,error)")
        cur = rows[0]
        if cur["status"] in ("queued", "running"):
            note = ""
            if cur["status"] == "queued":
                pos = api.rpc("queue_position", {"p_id": sid})
                note = " (next)" if pos == 0 else f" ({pos} ahead)" if isinstance(pos, int) else ""
            if (cur["status"], note) != last:
                print(f"  status: {cur['status']}{note}", file=sys.stderr)
                last = (cur["status"], note)
            continue
        print(json.dumps({k: cur.get(k) for k in ("id", "status", "score", "base_science", "program_bonus", "request_reward", "penalty_total",
                                                  "completed_tiles", "required_missing", "flexible_shortfall", "termination_reason", "error")}, indent=2))
        for ev in cur.get("evaluations") or []:
            print(f"  {ev['scenarios']['slug']}: {ev['status']} score={ev.get('score')} termination={ev.get('termination_reason') or '-'} {ev.get('error') or ''}")
        if page:
            print(f"  report, replay and logs: {page}")
        return 0 if cur["status"] == "scored" else 1


if __name__ == "__main__":
    raise SystemExit(main())
