"""Research trace: fault-monitor state around a time window (diagnostics only)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from v4.agent.observer4.config import load
from v4.agent.observer4.policy import Observer

from .run import evaluate
from .upstream import ROOT, resolve


def main():
    card, out, patch_name, start, end = sys.argv[1:6]
    patch = json.loads((ROOT / "v4" / "experiments" / f"{patch_name}.json").read_text()) if patch_name != "default" else None
    config = load(ROOT / "v4" / "agent" / "config.json", patch)
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()
    t1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp()
    log = []

    class Traced(Observer):
        def respond(self, snapshot):
            action = super().respond(snapshot)
            now = datetime.fromisoformat(snapshot["now_utc"].replace("Z", "+00:00")).timestamp()
            if t0 <= now <= t1:
                monitor = self.instrument
                levels = monitor.segment()
                log.append({
                    "now": snapshot["now_utc"], "action": action.get("action"), "dur": action.get("duration_seconds"),
                    "program": action.get("program"),
                    "segment": [(round(r["level"], 3), r["night"], round((r["end"] - r["start"]) / 60)) for r in levels[-8:]],
                    "records": len(monitor.records), "segment_start": monitor.segment_start,
                    "last": None if not monitor.records else {k: (round(v, 3) if isinstance(v, float) else v)
                                                              for k, v in monitor.records[-1].items()
                                                              if k in ("level", "zero", "mixed", "night", "samples", "matched")},
                    "vetoes": monitor.vetoes, "reports": len(monitor.reports),
                    "step": None if monitor._step(1.0) is None else "found",
                })
            return action

    resolve()
    summary = evaluate(Path(card), Path(out), config, fast=True, factory=lambda pub, rec: Traced(pub, config, rec))
    (Path(out) / "fault_trace.json").write_text(json.dumps(log, indent=1))
    print(summary["total"])


if __name__ == "__main__":
    main()
