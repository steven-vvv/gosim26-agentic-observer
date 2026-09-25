"""故障损失：故障区在故障开始后、检出前/后完成的曝光，若无故障本可多得的分数（用 results/obs13-cf 的 fault_delay_hours 作为检出时刻）。"""
import csv, json, glob, statistics as st
from datetime import datetime, timedelta
from pathlib import Path
def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
def rows(p): return list(csv.DictReader(open(p, encoding="utf-8-sig")))
out = []
for scen in sorted(glob.glob("scenarios/*/")):
    name = Path(scen).name; ref = Path(scen)/"outputs"/"reference"
    res = json.loads(Path(f"results/obs13-cf/{name}.json").read_text())
    rep = json.loads(Path(f"/tmp/robust/evfc/{name}/score_report.json").read_text())
    fault = [e for e in rows(ref/"weather_events.csv") if e["condition"] == "instrument_fault"]
    if not fault: continue
    fe = fault[0]; regions = set(json.loads(fe["spatial_scope_payload"])["region_ids"]); fs = t(fe["actual_start_utc"]); m = float(fe["instrument_efficiency_multiplier"])
    delay = res.get("fault_delay_hours")
    detect = fs + timedelta(hours=delay) if delay is not None else None
    ack = detect + timedelta(days=1) if detect else None
    repair = ack + timedelta(days=2) if ack else None
    tiles_region = {r["tile_id"]: r["region_id"] for r in rows(ref/"tiles.csv")}
    n_pre = n_known = n_ack = 0; loss_pre = loss_known = loss_ack = 0.0; n_total_after = 0
    obs_hours_pre = 0.0
    for a in rep["actions"]:
        if a["outcome"] != "completed": continue
        s0 = t(a["start_utc"])
        if s0 < fs: continue
        if detect and s0 < detect: obs_hours_pre += a["elapsed_seconds"]/3600
        if tiles_region.get(a["tile_id"]) not in regions: continue
        sc = a["base_science_score"] + a["program_bonus_score"]; loss = sc * (1/m - 1)
        if detect is None or s0 < detect: n_pre += 1; loss_pre += loss
        elif s0 < ack: n_known += 1; loss_known += loss
        elif s0 < repair: n_ack += 1; loss_ack += loss
    out.append(dict(s=name, m=m, nreg=len(regions), delay=delay, obs_h_pre=obs_hours_pre, n_pre=n_pre, loss_pre=loss_pre, n_known=n_known, loss_known=loss_known, n_ack=n_ack, loss_ack=loss_ack, total=rep["score"]["total"], fault_start_night=(fs - t(rows(ref/"night_calendar.csv")[0]["observing_start_utc"])).days))
T = st.mean(o["total"] for o in out)
print(f"n={len(out)}  故障乘数均值 {st.mean(o['m'] for o in out):.2f}  分区数均值 {st.mean(o['nreg'] for o in out):.2f}  故障开始夜 中位 {st.median(o['fault_start_night'] for o in out)} 范围 [{min(o['fault_start_night'] for o in out)},{max(o['fault_start_night'] for o in out)}]")
print(f"检出延迟 中位 {st.median(o['delay'] for o in out if o['delay'] is not None):.1f} h  均值 {st.mean(o['delay'] for o in out if o['delay'] is not None):.1f} h  未检出 {sum(1 for o in out if o['delay'] is None)}；检出前观测小时 均值 {st.mean(o['obs_h_pre'] for o in out):.1f} h")
print(f"检出前 故障区曝光 {st.mean(o['n_pre'] for o in out):.1f} 块/场, 损失 {st.mean(o['loss_pre'] for o in out):.0f} 分/场 ({100*st.mean(o['loss_pre'] for o in out)/T:.3f}%)")
print(f"检出后~回执前(1天) 故障区曝光 {st.mean(o['n_known'] for o in out):.1f} 块/场, 损失 {st.mean(o['loss_known'] for o in out):.0f} 分/场 ({100*st.mean(o['loss_known'] for o in out)/T:.3f}%)")
print(f"回执后~修复(2天) 故障区曝光 {st.mean(o['n_ack'] for o in out):.1f} 块/场, 损失 {st.mean(o['loss_ack'] for o in out):.0f} 分/场 ({100*st.mean(o['loss_ack'] for o in out)/T:.3f}%)")
for o in sorted(out, key=lambda o: -o['loss_pre'])[:5]: print("  ", {k: (round(v,1) if isinstance(v,float) else v) for k,v in o.items()})
