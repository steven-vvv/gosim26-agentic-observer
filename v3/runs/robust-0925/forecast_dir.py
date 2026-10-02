"""方向性事件（cloudy / smoggy / cold_wave / rocket_launch）：发生多少、被当期每周预报命中多少。"""
import csv, glob
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
def rows(p): return list(csv.DictReader(open(p, encoding="utf-8-sig")))
ev_n, hit, fp, fc = Counter(), Counter(), Counter(), Counter()
for scen in sorted(glob.glob("scenarios/*/")):
    ref = Path(scen) / "outputs" / "reference"
    nights = rows(ref / "night_calendar.csv")
    events = {e["event_id"]: e for e in rows(ref / "weather_events.csv")}
    forecasts = rows(ref / "weather_forecasts.csv")
    for i in range(0, len(nights), 7):
        T = t(nights[i]["observing_start_utc"])
        latest = {}
        for f in forecasts:
            if t(f["issued_at_utc"]) <= T and (f["event_id"] not in latest or int(f["revision"]) > int(latest[f["event_id"]]["revision"])):
                latest[f["event_id"]] = f
        vis = {k: f for k, f in latest.items() if t(f["predicted_end_utc"]) > T and t(f["predicted_start_utc"]) < T + timedelta(days=7)}
        for k, f in vis.items():
            fc[f["condition"]] += 1
            if k not in events: fp[f["condition"]] += 1
        for k, e in events.items():
            if T <= t(e["actual_start_utc"]) < T + timedelta(days=7):
                ev_n[e["condition"]] += 1
                hit[e["condition"]] += k in vis
n = len(glob.glob("scenarios/*/"))
for c in sorted(ev_n):
    print(f"{c:15s} 每场 {ev_n[c]/n:4.1f} 个；当期命中 {hit[c]/max(1,ev_n[c]):4.0%}；预报 {fc[c]/n:4.1f} 条/场，假警报 {fp[c]/max(1,fc[c]):4.0%}")
