"""预报模型分析：(1) 假警报能否按 (概率, 不确定度, 提前量) 分离；(2) 全站闭站概率的两种模型（现行：p·Φ·Φ 查标定表；生成器一致：过滤假警报后 Φ·Φ）的 Brier/对数损失；
(3) 寒潮夜的识别；(4) 中断动作的可预报性；(5) 故障期内拍到的天区损失。"""
import csv, glob, json, math, statistics as st
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys
sys.path.insert(0, '/home/steven/code/GOSIM26/v3/agents/observer')
from events import open_from_probability, _phi
def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
def rows(p): return list(csv.DictReader(open(p, encoding="utf-8-sig")))
HOR = 7*86400.0
def expected_prob(lead):  # 生成器：prob = .52 + .43*closeness
    closeness = 1 - min(1.0, lead / HOR)
    return .52 + .43 * closeness
def expected_unc(lead):
    closeness = 1 - min(1.0, lead / HOR)
    return max(900, int(round(((1 - closeness) * 4 + 0.25) * 4)) * 900)
sep_stats = {'real': [], 'fp': []}
brier = {'old': [], 'new': [], 'base': []}; ll = {'old': [], 'new': []}
cold_nights = {'tp':0,'fp':0,'fn':0,'tn':0}
intr = defaultdict(int); intr_time = defaultdict(float); alarm = defaultdict(int)
fault_loss = []
n_scen = 0
for scen in sorted(glob.glob("scenarios/*/")):
    n_scen += 1
    name = Path(scen).name
    ref = Path(scen) / "outputs" / "reference"
    nights = rows(ref / "night_calendar.csv"); slots = rows(ref / "slots.csv")
    weather = {w["slot_id"]: w for w in rows(ref / "weather.csv")}
    events = {e["event_id"]: e for e in rows(ref / "weather_events.csv")}
    forecasts = rows(ref / "weather_forecasts.csv")
    pubs = [t(nights[i]["observing_start_utc"]) for i in range(0, len(nights), 7)]
    vis_by_pub = []
    for T in pubs:
        latest = {}
        for f in forecasts:
            if t(f["issued_at_utc"]) <= T and (f["event_id"] not in latest or int(f["revision"]) > int(latest[f["event_id"]]["revision"])):
                latest[f["event_id"]] = f
        vis = [f for f in latest.values() if t(f["predicted_end_utc"]) > T and t(f["predicted_start_utc"]) < T + timedelta(days=7)]
        for f in vis:
            lead = max(0.0, (t(f["predicted_start_utc"]) - t(f["issued_at_utc"])).total_seconds())
            # 用预报自身的 issued_at 算提前量（agent 看得到 issued_at_utc 字段）
            dp = float(f["probability"]) - expected_prob(lead)
            du = float(f["start_uncertainty_seconds"]) - expected_unc(lead)
            kind = 'fp' if f["event_id"].startswith("FP") else 'real'
            sep_stats[kind].append((dp, du, float(f["probability"]), float(f["start_uncertainty_seconds"]), lead/86400, f["condition"]))
        vis_by_pub.append((T, vis))
    def is_fp(f):
        lead = max(0.0, (t(f["predicted_start_utc"]) - t(f["issued_at_utc"])).total_seconds())
        return abs(float(f["probability"]) - expected_prob(lead)) > 0.12 or abs(float(f["start_uncertainty_seconds"]) - expected_unc(lead)) > 1
    close_ev = [e for e in events.values() if e["force_close"] == "true" and e["spatial_scope_type"] == "ALL"]
    cold_ev = [e for e in events.values() if e["condition"] == "cold_wave"]
    # 逐时隙闭站概率
    for s in slots:
        ts = t(s["timestamp_utc"]) + timedelta(seconds=450)
        is_open = weather[s["slot_id"]]["is_observable"] == "true"
        T, vis = max((p for p in vis_by_pub if p[0] <= ts), key=lambda p: p[0])
        if ts >= T + timedelta(days=7): continue  # 预报有效期内
        closing = [f for f in vis if f["condition"] in ("rainy", "tornado") and f["spatial_scope_type"] == "ALL"]
        p_old = 0.0; stay_new = 1.0
        for f in closing:
            u = float(f["start_uncertainty_seconds"])
            a = (ts + timedelta(seconds=450) - t(f["predicted_start_utc"])).total_seconds() / u
            b = (t(f["predicted_end_utc"]) - ts + timedelta(seconds=450)).total_seconds() / u
            inside = _phi(a) * _phi(b)
            p_old = 1 - (1 - p_old) * (1 - float(f["probability"]) * inside)
            if not is_fp(f):
                stay_new *= (1 - inside)
        open_old = open_from_probability(p_old)
        open_new = 0.957 * stay_new
        y = 1.0 if is_open else 0.0
        brier['old'].append((open_old - y) ** 2); brier['new'].append((open_new - y) ** 2); brier['base'].append((0.878 - y) ** 2)
        for k, q in (('old', open_old), ('new', open_new)):
            q = min(max(q, 1e-3), 1 - 1e-3); ll[k].append(-(y * math.log(q) + (1 - y) * math.log(1 - q)))
    # 寒潮夜识别：每夜（开夜时刻）真实是否处于寒潮 vs 预报（过滤假警报后）是否有寒潮覆盖夜中点
    for n in nights:
        mid = t(n["observing_start_utc"]) + (t(n["observing_end_utc"]) - t(n["observing_start_utc"])) / 2
        truth = any(t(e["actual_start_utc"]) <= mid < t(e["actual_end_utc"]) for e in cold_ev)
        T, vis = max((p for p in vis_by_pub if p[0] <= mid), key=lambda p: p[0])
        if mid >= T + timedelta(days=7):
            continue
        pred = any(f["condition"] == "cold_wave" and not is_fp(f) and t(f["predicted_start_utc"]) - timedelta(seconds=float(f["start_uncertainty_seconds"])) <= mid < t(f["predicted_end_utc"]) + timedelta(seconds=float(f["end_uncertainty_seconds"])) for f in vis)
        cold_nights['tp' if truth and pred else 'fp' if pred else 'fn' if truth else 'tn'] += 1
    # 中断动作与故障损失（用 evfc 的运行）
    rep = Path(f"/tmp/robust/evfc/{name}/score_report.json")
    if rep.exists():
        d = json.loads(rep.read_text())
        for a in d["actions"]:
            if a["outcome"] != "weather_interrupted": continue
            start = t(a["start_utc"]); el = float(a["elapsed_seconds"])
            tclose = start + timedelta(seconds=el) + timedelta(seconds=450)  # 关闭时隙的中点
            rain = any(t(e["actual_start_utc"]) <= tclose < t(e["actual_end_utc"]) for e in close_ev)
            T, vis = max((p for p in vis_by_pub if p[0] <= tclose), key=lambda p: p[0])
            stay = 1.0
            for f in vis:
                if f["condition"] in ("rainy", "tornado") and f["spatial_scope_type"] == "ALL" and not is_fp(f) and tclose < T + timedelta(days=7):
                    u = float(f["start_uncertainty_seconds"])
                    stay *= 1 - _phi((tclose + timedelta(seconds=450) - t(f["predicted_start_utc"])).total_seconds() / u) * _phi((t(f["predicted_end_utc"]) - tclose + timedelta(seconds=450)).total_seconds() / u)
            pc = 1 - stay
            key = ('rain' if rain else 'bg', 'p>0.3' if pc > 0.3 else 'p>0.1' if pc > 0.1 else 'p<0.1')
            intr[key] += 1; intr_time[key] += el
        # 警报率：开放时隙里新模型 P(closed)>0.3 的比例（这些是规避的代价面）
        for s in slots:
            ts = t(s["timestamp_utc"]) + timedelta(seconds=450)
            T, vis = max((p for p in vis_by_pub if p[0] <= ts), key=lambda p: p[0])
            if ts >= T + timedelta(days=7): continue
            stay = 1.0
            for f in vis:
                if f["condition"] in ("rainy", "tornado") and f["spatial_scope_type"] == "ALL" and not is_fp(f):
                    u = float(f["start_uncertainty_seconds"])
                    stay *= 1 - _phi((ts + timedelta(seconds=450) - t(f["predicted_start_utc"])).total_seconds() / u) * _phi((t(f["predicted_end_utc"]) - ts + timedelta(seconds=450)).total_seconds() / u)
            pc = 1 - stay
            is_open = weather[s["slot_id"]]["is_observable"] == "true"
            alarm[('open' if is_open else 'closed', 'p>0.3' if pc > 0.3 else 'p>0.1' if pc > 0.1 else 'p<0.1')] += 1
        # 故障：故障区内、故障开始后、上报确认前完成的曝光的损失
        fault = [e for e in events.values() if e["condition"] == "instrument_fault"]
        if fault:
            fe = fault[0]; regions = set(json.loads(fe["spatial_scope_payload"])["region_ids"]); fs = t(fe["actual_start_utc"]); m = float(fe["instrument_efficiency_multiplier"])
            first_ok = None
            import csv as _csv
            with open(f"/tmp/robust/evfc/{name}/decisions.csv", encoding="utf-8-sig") as h:
                for row in _csv.DictReader(h):
                    blob = json.dumps(row)
                    if "Instrument_Failure" in blob:
                        ts = row.get("timestamp_utc") or row.get("decision_timestamp_utc") or row.get("cursor_timestamp_utc")
                        if ts:
                            first_ok = t(ts); break
            tiles_region = {}
            for row in rows(ref / "tiles.csv"): tiles_region[row["tile_id"]] = row["region_id"]
            lost_before = lost_after = 0.0; n_before = n_after = 0
            repair = fs + timedelta(days=3)
            for a in d["actions"]:
                if a["outcome"] != "completed" or tiles_region.get(a["tile_id"]) not in regions: continue
                s0 = t(a["start_utc"])
                if s0 < fs: continue
                sc = a["base_science_score"] + a["program_bonus_score"]
                loss = sc * (1 / m - 1)  # 若无故障本可多得
                if first_ok is None or s0 < first_ok:
                    lost_before += loss; n_before += 1
                else:
                    lost_after += loss; n_after += 1
            fault_loss.append((name, m, len(regions), n_before, lost_before, n_after, lost_after, d["score"]["total"], (first_ok - fs).total_seconds()/3600 if first_ok else None))
print(f"场景数 {n_scen}")
print("\n[1] 假警报分离（可见预报，按 |prob − 期望| 与 |unc − 期望|）")
for kind in ('real', 'fp'):
    v = sep_stats[kind]
    dps = [abs(x[0]) for x in v]; dus = [abs(x[1]) for x in v]
    print(f"  {kind}: n={len(v)}  |dp| 中位 {st.median(dps):.3f} P95 {sorted(dps)[int(.95*len(dps))]:.3f} max {max(dps):.3f}   |du|=0 的比例 {sum(1 for x in dus if x < 1)/len(v):.3f}  prob 范围 [{min(x[2] for x in v):.2f},{max(x[2] for x in v):.2f}]")
fp_flag = sum(1 for x in sep_stats['fp'] if abs(x[0]) > 0.12 or abs(x[1]) > 1); real_flag = sum(1 for x in sep_stats['real'] if abs(x[0]) > 0.12 or abs(x[1]) > 1)
print(f"  规则 |dp|>0.12 或 |du|>0 判为假警报：假警报识别 {fp_flag}/{len(sep_stats['fp'])}，真事件误判 {real_flag}/{len(sep_stats['real'])}")
print("\n[2] 预报有效期内逐时隙开放概率的 Brier（越小越好）：")
for k in ('base', 'old', 'new'): print(f"  {k}: {st.mean(brier[k]):.4f}", end='')
print(f"\n  对数损失 old {st.mean(ll['old']):.4f}  new {st.mean(ll['new']):.4f}  (n={len(brier['old'])})")
print(f"\n[3] 寒潮夜识别（预报有效期内的夜）: {cold_nights}")
print("\n[4] 中断动作（evfc 46 场）按 真实原因 × 新模型 P(closed)：次数 / 浪费秒数（每场）")
for k in sorted(intr): print(f"  {k}: {intr[k]/n_scen:.2f} 次/场, {intr_time[k]/n_scen:.0f} s/场")
print("  警报面（预报有效期内全部时隙）:", {k: v for k, v in sorted(alarm.items())})
print("\n[5] 故障损失（evfc）：")
tot_b = st.mean(x[4] for x in fault_loss); tot_a = st.mean(x[6] for x in fault_loss); T = st.mean(x[7] for x in fault_loss)
print(f"  上报前故障区完成曝光 {st.mean(x[3] for x in fault_loss):.1f} 块/场，若无故障本可多得 {tot_b:.0f} 分/场 ({100*tot_b/T:.3f}%)；上报后 {st.mean(x[5] for x in fault_loss):.1f} 块/场，{tot_a:.0f} 分/场 ({100*tot_a/T:.3f}%)")
print(f"  检出延迟(h) 中位 {st.median([x[8] for x in fault_loss if x[8] is not None]):.1f}  未上报场数 {sum(1 for x in fault_loss if x[8] is None)}")
for x in fault_loss[:6]: print("   ", x)
