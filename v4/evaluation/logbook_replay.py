"""Replay the duty log of a platform run through the model reader, without the engine.

Reads `messages.jsonl` of one card from a downloaded result, reads every log text the
agent would read (in parallel, same prompt and validation), and prints the faults and
terrain heights the agent would act on next to the reports the run actually made.
Credentials come from the environment (OBSERVER_MODEL_* or OPENAI_*), never printed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from v4.agent.observer4.config import load
from v4.agent.observer4.geometry import parse_utc
from v4.agent.observer4.logbook import SYSTEM_PROMPT, Logbook, decode_log

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="card directory of an unpacked platform result")
    parser.add_argument("--offset", type=float, required=True, help="site UTC offset in hours")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--show", action="store_true", help="print every reading")
    parser.add_argument("--no-thinking", action="store_true", help="switch provider reasoning off (as the Worker does)")
    parser.add_argument("--thinking", action="store_true", help="ask the Worker to keep reasoning")
    parser.add_argument("--save", type=Path, help="write the extracted appointments, cancellations and quiet windows")
    args = parser.parse_args()
    config = load(ROOT / "v4" / "agent" / "config.json")
    events = []
    book = Logbook(SimpleNamespace(longitude=0.0), {"site": {"utc_offset_hours": args.offset}}, config,
                   lambda kind, payload: events.append((kind, payload)))
    if book.reader is None:
        raise SystemExit("reader unavailable: " + json.dumps(events))
    messages = [json.loads(line) for line in (args.run / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    book.collect(messages)
    queue = list(book.queue)
    book.queue.clear()
    reader = book.reader
    if args.no_thinking:
        # The platform reaches the provider through the team Worker, which switches reasoning
        # off; a direct local endpoint needs the same switch in the request body.
        def transport(request, timeout):
            body = json.loads(request.data)
            body["thinking"] = {"type": "disabled"}
            request.data = json.dumps(body).encode("utf-8")
            request.remove_header("Content-length")
            return urllib.request.urlopen(request, timeout=timeout)
        reader._transport = transport

    durations = []

    def read(item):
        issued, stamp, text = item
        local = datetime.fromtimestamp(issued, timezone.utc) + timedelta(hours=book.offset_hours)
        payload = {"issued_at_utc": stamp, "issued_at_local": local.strftime("%Y-%m-%d %H:%M %A"),
                   "site_utc_offset_hours": book.offset_hours, "log": decode_log(text)}
        started = time.perf_counter()
        extra = {"chat_template_kwargs": {"thinking": True}, "max_tokens": 4096} if args.thinking else None
        reading = reader.extract("duty_log", SYSTEM_PROMPT, payload, extra_body=extra,
                                 timeout=115.0 if args.thinking else None)
        durations.append(time.perf_counter() - started)
        return reading

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        readings = list(pool.map(read, queue))
    failures = 0
    for (issued, stamp, text), reading in zip(queue, readings):
        if not isinstance(reading, dict):
            failures += 1
            continue
        if args.show:
            print(stamp, json.dumps(reading, ensure_ascii=False)[:300])
        book._apply(reading, issued)
    local = lambda ts: (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=book.offset_hours)).strftime("%m-%d %H:%M")
    print(f"texts {len(queue)}, failed readings {failures}, calls {reader.calls}, usage {reader.usage_totals}")
    ordered = sorted(durations)
    print(f"seconds per reading: median {ordered[len(ordered) // 2]:.1f}, p90 {ordered[int(len(ordered) * 0.9)]:.1f}, max {ordered[-1]:.1f}")
    print("named faults (local start):", [local(item["start"]) for item in book.faults if item["state"] == "open"])
    print(f"faults {sum(item['state'] == 'open' for item in book.faults)}, cancelled {len(book.cancelled)}, "
          f"quiet windows {len(book.quiet)}")
    print("terrain:", book.terrain)
    if args.save:
        args.save.write_text(json.dumps({
            "offset_hours": book.offset_hours,
            "faults": sorted(item["start"] for item in book.faults if item["state"] == "open"),
            "cancelled": sorted(book.cancelled), "quiet": sorted(book.quiet)}, indent=1) + "\n", encoding="utf-8")
    reports = [m for m in messages if m.get("record_type") == "report_result"]
    print("run reports (local):", [(local(parse_utc(m["issued_at_utc"]).timestamp()), m["correct"]) for m in reports])


if __name__ == "__main__":
    main()
