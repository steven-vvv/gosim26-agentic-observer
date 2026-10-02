"""agent 实际看得到的每周预报（第 0/7/14/21/28 夜开夜时发布、覆盖 7 天）对全站强制关闭事件（rainy / tornado）的预报能力。"""
import csv, json, glob, statistics as st
from datetime import datetime, timedelta
from pathlib import Path

def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
def rows(p): return list(csv.DictReader(open(p, encoding="utf-8-sig")))

agg = {"events": 0, "hit": 0, "fc": 0, "fp": 0, "closed_h": 0.0, "closed_rain_h": 0.0, "covered_h": 0.0, "fc_h": 0.0,
       "start_err": [], "end_err": [], "prob_hit": [], "prob_fp": []}
for scen in sorted(glob.glob("scenarios/*/")):
    ref = Path(scen) / "outputs" / "reference"
    nights = rows(ref / "night_calendar.csv")
    slots = rows(ref / "slots.csv")
    weather = {w["slot_id"]: w for w in rows(ref / "weather.csv")}
    events = {e["event_id"]: e for e in rows(ref / "weather_events.csv")}
    forecasts = rows(ref / "weather_forecasts.csv")
    close_ev = {k: e for k, e in events.items() if e["force_close"] == "true" and e["spatial_scope_type"] == "ALL"}
    pubs = [t(nights[i]["observing_start_utc"]) for i in range(0, len(nights), 7)]
    for T in pubs:
        latest = {}
        for f in forecasts:
            if t(f["issued_at_utc"]) <= T and (f["event_id"] not in latest or int(f["revision"]) > int(latest[f["event_id"]]["revision"])):
                latest[f["event_id"]] = f
        vis = [f for f in latest.values() if t(f["predicted_end_utc"]) > T and t(f["predicted_start_utc"]) < T + timedelta(days=7)
               and f["condition"] in ("rainy", "tornado") and f["spatial_scope_type"] == "ALL"]
        agg["fc"] += len(vis)
        for f in vis:
            e = close_ev.get(f["event_id"])
            if e is None:
                agg["fp"] += 1; agg["prob_fp"].append(float(f["probability"]))
            else:
                agg["prob_hit"].append(float(f["probability"]))
                agg["start_err"].append((t(f["predicted_start_utc"]) - t(e["actual_start_utc"])).total_seconds() / 3600)
                agg["end_err"].append((t(f["predicted_end_utc"]) - t(e["actual_end_utc"])).total_seconds() / 3600)
        for k, e in close_ev.items():
            if T <= t(e["actual_start_utc"]) < T + timedelta(days=7):
                agg["events"] += 1
                agg["hit"] += any(f["event_id"] == k for f in vis)
        # 本周内：夜间关闭时长、其中降雨事件造成的、被预报窗口（±不确定度）覆盖的
        win = [(t(f["predicted_start_utc"]) - timedelta(seconds=float(f["start_uncertainty_seconds"])),
                t(f["predicted_end_utc"]) + timedelta(seconds=float(f["end_uncertainty_seconds"]))) for f in vis]
        for s in slots:
            ts = t(s["timestamp_utc"])
            if not (T <= ts < T + timedelta(days=7)): continue
            h = float(s["duration_seconds"]) / 3600
            in_fc = any(a <= ts < b for a, b in win)
            agg["fc_h"] += h * in_fc
            if weather[s["slot_id"]]["is_observable"] != "true":
                agg["closed_h"] += h
                rain = any(t(e["actual_start_utc"]) <= ts < t(e["actual_end_utc"]) for e in close_ev.values())
                agg["closed_rain_h"] += h * rain
                agg["covered_h"] += h * (rain and in_fc)
n = len(glob.glob("scenarios/*/"))
print(f"{n} 个场景；每周发布里可见的全站强制关闭预报 {agg['fc']} 条，其中假警报 {agg['fp']}（{agg['fp']/agg['fc']:.0%}）")
print(f"实际全站强制关闭事件（发布后 7 天内开始）{agg['events']} 个，被当期预报命中 {agg['hit']}（{agg['hit']/agg['events']:.0%}）")
print(f"命中事件的预报概率均值 {st.mean(agg['prob_hit']):.2f}；假警报的预报概率均值 {st.mean(agg['prob_fp']):.2f}")
print(f"起始时间误差（预报 − 实际）中位数 {st.median(agg['start_err']):+.1f} h，|误差| 中位数 {st.median([abs(x) for x in agg['start_err']]):.1f} h；结束 {st.median([abs(x) for x in agg['end_err']]):.1f} h")
print(f"每场夜间关闭 {agg['closed_h']/n:.1f} h，其中降雨/龙卷风 {agg['closed_rain_h']/n:.1f} h，被预报窗口（含不确定度）覆盖 {agg['covered_h']/n:.1f} h；预报窗口总长 {agg['fc_h']/n:.1f} h")
