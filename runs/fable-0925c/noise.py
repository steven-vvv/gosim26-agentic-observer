"""变体逐场差异结构：标准差、与场景特征的相关、变体之间差异的相关（回答 Q5/Q6）。"""
import json, statistics as st, math
from pathlib import Path
R = Path('results')
def load(agent):
    out = {}
    for p in (R/agent).glob('*.json'):
        d = json.loads(p.read_text()); out[p.stem] = d
    return out
base = load('obs13-cf')
variants = ['obs13-ro5400','obs13-ev1200','obs13-lam','obs13-b90','obs13-fault','obs13-req','obs14','obs12-roev','obs12-rok8','obs12r','obs11p','obs13','obs12-off']
def corr(x, y):
    mx, my = st.mean(x), st.mean(y); sx, sy = st.pstdev(x), st.pstdev(y)
    return sum((a-mx)*(b-my) for a, b in zip(x, y))/len(x)/(sx*sy) if sx and sy else float('nan')
diffs = {}
for v in variants:
    d = load(v)
    common = sorted(set(d) & set(base))
    if len(common) < 4: continue
    x = [100*(d[s]['total']/base[s]['total']-1) for s in common]
    diffs[v] = dict(zip(common, x))
    fm = [base[s]['fault_multiplier'] for s in common]
    bt = [base[s]['total'] for s in common]
    print(f"{v:14} n={len(common):2d} mean {st.mean(x):+.3f}% sd {st.pstdev(x):.3f} se {st.pstdev(x)/math.sqrt(len(x)):.3f}  corr(fault_mult) {corr(x, fm):+.2f} corr(total) {corr(x, bt):+.2f}")
# evfc / evtruth
for var in ['evfc', 'evtruth']:
    x = {}; 
    for rep in Path(f'/tmp/robust/{var}').glob('*/score_report.json'):
        s = rep.parent.name; x[s] = 100*(json.loads(rep.read_text())['score']['total']/base[s]['total']-1)
    diffs[var] = x
    common = sorted(x)
    fm = [base[s]['fault_multiplier'] for s in common]; bt = [base[s]['total'] for s in common]
    v = [x[s] for s in common]
    print(f"{var:14} n={len(v):2d} mean {st.mean(v):+.3f}% sd {st.pstdev(v):.3f} se {st.pstdev(v)/math.sqrt(len(v)):.3f}  corr(fault_mult) {corr(v, fm):+.2f} corr(total) {corr(v, bt):+.2f}")
# 变体差异之间的相关（同一场景上偏差是否同号）
names = [v for v in diffs if len(diffs[v]) >= 30]
print("\n变体逐场差异之间的相关系数：")
for i, a in enumerate(names):
    for b in names[i+1:]:
        common = sorted(set(diffs[a]) & set(diffs[b]))
        if len(common) >= 30:
            print(f"  {a:14} {b:14} n={len(common)} r={corr([diffs[a][s] for s in common],[diffs[b][s] for s in common]):+.2f}")
# 校准分近似：cal = 10000*(total - W)/(base_total - W)，W=-20800
bres = load('base')
cal = {s: 10000*(base[s]['total']+20800)/(bres[s]['total']+20800) for s in base}
print(f"\n校准分(obs14, 用基线当参考) 均值 {st.mean(cal.values()):.0f} sd {st.pstdev(list(cal.values())):.0f}")
# 每个变体在校准分尺度上的逐场差异 sd
for v in ['obs13-ro5400','obs13-ev1200','evfc','evtruth']:
    x = diffs[v]; common = sorted(set(x) & set(cal))
    dc = [cal[s]*x[s]/100 for s in common]
    print(f"  {v}: 校准分差异 mean {st.mean(dc):+.1f} sd {st.pstdev(dc):.1f}")
# 场景特征与校准分：故障乘数、故障区数、开放率
fm = [base[s]['fault_multiplier'] for s in sorted(base)]; c = [cal[s] for s in sorted(base)]
nreg = [len(base[s]['fault_regions']) for s in sorted(base)]
print(f"corr(cal, fault_mult) {corr(c, fm):+.2f}  corr(cal, n_fault_regions) {corr(c, nreg):+.2f}")
