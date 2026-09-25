"""预报模型分析 v2：假警报规则（|dp|>0.12 或 |du|>900 或 issued_at ≠ 发布时刻）、按预测开放概率分档的校准、寒潮夜识别。"""
import csv, glob, json, math, statistics as st
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys
sys.path.insert(0, '/home/steven/code/GOSIM26/agents/observer')
from events import open_from_probability, _phi
def t(s): return datetime.fromisoformat(s.replace("Z", "+00:00"))
def rows(p): return list(csv.DictReader(open(p, encoding="utf-8-sig")))
HOR = 7*86400.0
def expected_prob(lead): return .52 + .43 * (1 - min(1.0, lead / HOR))
def expected_unc(lead):
    closeness = 1 - min(1.0, lead / HOR)
    return max(900, int(round(((1 - closeness) * 4 + 0.25) * 4)) * 900)
def flags(f, T):
    lead = max(0.0, (t(f["predicted_start_utc"]) - t(f["issued_at_utc"])).total_seconds())
    dp = abs(float(f["probability"]) - expected_prob(lead)); du = abs(float(f["start_uncertainty_seconds"]) - expected_unc(lead))
    stale = (T - t(f["issued_at_utc"])).total_seconds() > 3600
    return dp > 0.12, du > 900, stale
conf = defaultdict(int)
calib = {m: defaultdict(lambda: [0, 0]) for m in ('old', 'new', 'newtab')}
brier = defaultdict(list)
cold = defaultdict(int)
per_cond = defaultdict(lambda: defaultdict(int))
for scen in sorted(glob.glob("scenarios/*/")):
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
            fp = f["event_id"].startswith("FP"); a, b, c = flags(f, T)
            conf[('FP' if fp else 'real', 'dp' if a else '-', 'du' if b else '-', 'stale' if c else '-')] += 1
            rule = a or b or c
            per_cond[f["condition"]][('FP' if fp else 'real', 'flag' if rule else 'pass')] += 1
        vis_by_pub.append((T, vis))
    cold_ev = [e for e in events.values() if e["condition"] == "cold_wave"]
    for s in slots:
        ts = t(s["timestamp_utc"]) + timedelta(seconds=450)
        is_open = weather[s["slot_id"]]["is_observable"] == "true"
        T, vis = max((p for p in vis_by_pub if p[0] <= ts), key=lambda p: p[0])
        if ts >= T + timedelta(days=7): continue
        closing = [f for f in vis if f["condition"] in ("rainy", "tornado") and f["spatial_scope_type"] == "ALL"]
        p_old = 0.0; stay_new = 1.0; p_tab = 0.0
        for f in closing:
            u = float(f["start_uncertainty_seconds"])
            inside = _phi((ts + timedelta(seconds=450) - t(f["predicted_start_utc"])).total_seconds() / u) * _phi((t(f["predicted_end_utc"]) - ts + timedelta(seconds=450)).total_seconds() / u)
            p_old = 1 - (1 - p_old) * (1 - float(f["probability"]) * inside)
            if not any(flags(f, T)):
                stay_new *= (1 - inside)
                p_tab = 1 - (1 - p_tab) * (1 - float(f["probability"]) * inside)
        preds = {'old': open_from_probability(p_old), 'new': 0.931 * stay_new, 'newtab': open_from_probability(p_tab)}
        y = 1.0 if is_open else 0.0
        informative = p_old > 0.05 or stay_new < 0.95
        for m, q in preds.items():
            brier[m].append((q - y) ** 2)
            if informative: brier[m + '_inf'].append((q - y) ** 2)
            b = min(9, int(q * 10)); calib[m][b][0] += is_open; calib[m][b][1] += 1
    for n in nights:
        mid = t(n["observing_start_utc"]) + (t(n["observing_end_utc"]) - t(n["observing_start_utc"])) / 2
        truth = any(t(e["actual_start_utc"]) <= mid < t(e["actual_end_utc"]) for e in cold_ev)
        T, vis = max((p for p in vis_by_pub if p[0] <= mid), key=lambda p: p[0])
        if mid >= T + timedelta(days=7): continue
        pred = any(f["condition"] == "cold_wave" and not any(flags(f, T)) and t(f["predicted_start_utc"]) <= mid < t(f["predicted_end_utc"]) for f in vis)
        pred_old = any(f["condition"] == "cold_wave" and t(f["predicted_start_utc"]) <= mid < t(f["predicted_end_utc"]) for f in vis)
        cold[('new', 'tp' if truth and pred else 'fp' if pred else 'fn' if truth else 'tn')] += 1
        cold[('old', 'tp' if truth and pred_old else 'fp' if pred_old else 'fn' if truth else 'tn')] += 1
print("假警报规则各特征的触发（真/假 × dp × du × stale）:")
for k in sorted(conf): print("  ", k, conf[k])
fp_total = sum(v for k, v in conf.items() if k[0] == 'FP'); real_total = sum(v for k, v in conf.items() if k[0] == 'real')
fp_flag = sum(v for k, v in conf.items() if k[0] == 'FP' and k[1:] != ('-', '-', '-')); real_flag = sum(v for k, v in conf.items() if k[0] == 'real' and k[1:] != ('-', '-', '-'))
print(f"联合规则：假警报识别 {fp_flag}/{fp_total}，真事件误判 {real_flag}/{real_total}")
fp_flag2 = sum(v for k, v in conf.items() if k[0] == 'FP' and k[1:3] != ('-', '-')); real_flag2 = sum(v for k, v in conf.items() if k[0] == 'real' and k[1:3] != ('-', '-'))
print(f"只用 dp/du（不用 stale）：假警报识别 {fp_flag2}/{fp_total}，真事件误判 {real_flag2}/{real_total}")
print("按事件类型：", {c: dict(v) for c, v in per_cond.items()})
print("\nBrier 全部 / 有信息时隙：", {m: round(st.mean(v), 4) for m, v in brier.items()}, f"(n_inf={len(brier['old_inf'])})")
for m in ('old', 'new', 'newtab'):
    print(f"校准 {m}: " + "  ".join(f"[{b/10:.1f}) {o/n:.2f}(n={n})" for b, (o, n) in sorted(calib[m].items()) if n))
print("寒潮夜识别:", dict(cold))
