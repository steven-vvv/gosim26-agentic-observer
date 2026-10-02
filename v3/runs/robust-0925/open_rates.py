"""开放率标定：全部夜间时隙；真实全站强制关闭事件以外；agent 可见预报窗口以外 / 以内（按预报概率分档）。"""
import csv, glob, math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
def rows(p): return list(csv.DictReader(open(p, encoding="utf-8-sig")))
tot = defaultdict(lambda: [0, 0])  # 类别 -> [开放, 总数]
for scen in sorted(glob.glob("scenarios/*/")):
    ref = Path(scen) / "outputs" / "reference"
    nights = rows(ref / "night_calendar.csv")
    slots = rows(ref / "slots.csv")
    weather = {w["slot_id"]: w for w in rows(ref / "weather.csv")}
    events = [e for e in rows(ref / "weather_events.csv") if e["force_close"] == "true" and e["spatial_scope_type"] == "ALL"]
    forecasts = rows(ref / "weather_forecasts.csv")
    pubs = [t(nights[i]["observing_start_utc"]) for i in range(0, len(nights), 7)]
    vis_by_pub = []
    for T in pubs:
        latest = {}
        for f in forecasts:
            if t(f["issued_at_utc"]) <= T and (f["event_id"] not in latest or int(f["revision"]) > int(latest[f["event_id"]]["revision"])):
                latest[f["event_id"]] = f
        vis_by_pub.append((T, [f for f in latest.values() if t(f["predicted_end_utc"]) > T and t(f["predicted_start_utc"]) < T + timedelta(days=7)
                               and f["condition"] in ("rainy", "tornado") and f["spatial_scope_type"] == "ALL"]))
    for s in slots:
        ts = t(s["timestamp_utc"]) + timedelta(seconds=450)
        is_open = weather[s["slot_id"]]["is_observable"] == "true"
        in_true = any(t(e["actual_start_utc"]) <= ts < t(e["actual_end_utc"]) for e in events)
        T, vis = max((p for p in vis_by_pub if p[0] <= ts), key=lambda p: p[0])
        # 预报给出的“此刻被关闭”的概率：probability × Φ((ts−start)/u) × Φ((end−ts)/u)
        p = 0.0
        for f in vis:
            u = float(f["start_uncertainty_seconds"])
            a = (ts - t(f["predicted_start_utc"])).total_seconds() / u
            b = (t(f["predicted_end_utc"]) - ts).total_seconds() / u
            inside = 0.5 * (1 + math.erf(a / math.sqrt(2))) * 0.5 * (1 + math.erf(b / math.sqrt(2)))
            p = 1 - (1 - p) * (1 - float(f["probability"]) * inside)
        for key in ["全部", "真实事件以外" if not in_true else "真实事件以内", f"预报 p∈[{min(0.8, math.floor(p * 5) / 5):.1f},+0.2)"]:
            tot[key][0] += is_open; tot[key][1] += 1
for k in sorted(tot):
    o, n = tot[k]
    print(f"{k:22s} 时隙 {n:6d}  开放率 {o / n:.3f}")
