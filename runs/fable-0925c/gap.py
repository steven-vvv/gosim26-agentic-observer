"""差距分解：obs14(=obs13-cf) / obs16(evfc) / evtruth 与 全知可行排程(primal) / 完美信息上界 的分项差。"""
import json, statistics as st
from pathlib import Path
ROOT = Path('/home/steven/code/GOSIM26')
scen = sorted(p.stem for p in (ROOT/'results/obs13-cf').glob('*.json'))
def load_res(agent, s):
    return json.loads((ROOT/'results'/agent/f'{s}.json').read_text())
def load_rep(var, s):
    p = Path(f'/tmp/robust/{var}/{s}/score_report.json')
    if not p.exists(): return None
    d = json.loads(p.read_text()); sc = d['score']
    # count tiles from actions
    tiles = set(a['tile_id'] for a in d['actions'] if a.get('outcome') == 'completed')
    return {'total': sc['total'], 'base_science': sc['base_science'], 'program_bonus': sc['program_bonus'],
            'coverage_bonus': sc['coverage_bonus'], 'coverage_evenness': sc['coverage_evenness'],
            'report_reward': sc.get('report_reward', 0), 'request_reward': sc.get('request_reward', 0),
            'penalties': sc.get('penalties', {}), 'completed_tiles': len(tiles)}
rows = []
for s in scen:
    o = load_res('obs13-cf', s); b = load_res('base', s); u = load_res('upper_bound', s)
    fc = load_rep('evfc', s); tr = load_rep('evtruth', s)
    pr = u['primal']
    pen_o = sum(o['penalties'].values()) if isinstance(o['penalties'], dict) else 0
    row = dict(s=s, total=o['total'], base=o['base_science'], bonus=o['program_bonus'], cov=o['coverage_bonus'],
               even=o['coverage_evenness'], tags=o['report_reward'], req=o['request_reward'], pen=pen_o, tiles=o['completed_tiles'],
               p_total=pr['total']+o['request_reward'], p_base=pr['base'], p_bonus=pr['bonus'], p_cov=pr['coverage'], p_even=pr['evenness'],
               p_tags=pr['tags'], p_pen=pr['penalties'], p_tiles=pr['tiles'], ub=u['upper_bound'], base_total=b['total'],
               fc_total=fc['total'] if fc else None, tr_total=tr['total'] if tr else None,
               fc=fc, tr=tr)
    rows.append(row)
def pct(x, y): return 100*(x/y-1)
print(f"{'scen':12} {'obs14':>9} {'primal':>9} {'gap%':>6} {'dbase':>7} {'dbonus':>7} {'dcov':>7} {'dtags':>6} {'dpen':>6} {'dtiles':>6} {'even_o':>6} {'even_p':>6} {'ub%':>6} {'fc%':>6} {'tr%':>6}")
agg = {k: [] for k in ['gap','dbase','dbonus','dcov','dtags','dpen','dtiles','ubgap','fc','tr','base_frac','bonus_frac','cov_frac','tags_frac','pen_frac','even_o','even_p']}
for r in rows:
    gap = r['p_total']-r['total']
    d = dict(dbase=r['p_base']-r['base'], dbonus=r['p_bonus']-r['bonus'], dcov=r['p_cov']-r['cov'], dtags=r['p_tags']-r['tags'],
             dpen=-(r['p_pen']-r['pen']), dtiles=r['p_tiles']-r['tiles'])
    fcp = pct(r['fc_total'], r['total']) if r['fc_total'] else float('nan')
    trp = pct(r['tr_total'], r['total']) if r['tr_total'] else float('nan')
    print(f"{r['s']:12} {r['total']:9.0f} {r['p_total']:9.0f} {pct(r['p_total'], r['total']):6.2f} {d['dbase']:7.0f} {d['dbonus']:7.0f} {d['dcov']:7.0f} {d['dtags']:6.0f} {d['dpen']:6.0f} {d['dtiles']:6d} {r['even']:6.3f} {r['p_even']:6.3f} {pct(r['ub'], r['total']):6.2f} {fcp:6.2f} {trp:6.2f}")
    agg['gap'].append(pct(r['p_total'], r['total'])); agg['ubgap'].append(pct(r['ub'], r['total']))
    for k in ['dbase','dbonus','dcov','dtags','dpen','dtiles']: agg[k].append(d[k])
    agg['fc'].append(fcp); agg['tr'].append(trp)
    agg['base_frac'].append(100*d['dbase']/r['total']); agg['bonus_frac'].append(100*d['dbonus']/r['total']); agg['cov_frac'].append(100*d['dcov']/r['total'])
    agg['tags_frac'].append(100*d['dtags']/r['total']); agg['pen_frac'].append(100*d['dpen']/r['total'])
    agg['even_o'].append(r['even']); agg['even_p'].append(r['p_even'])
print()
print("均值（46 场）：")
for k, v in agg.items():
    v = [x for x in v if x == x]
    print(f"  {k:10} mean {st.mean(v):9.3f}  sd {st.pstdev(v):8.3f}  min {min(v):9.3f} max {max(v):9.3f}")
tot = st.mean([r['total'] for r in rows])
print(f"\n平均总分 obs14 {tot:.0f}; primal 差分项占总分: base {st.mean(agg['base_frac']):.3f}% bonus {st.mean(agg['bonus_frac']):.3f}% cov {st.mean(agg['cov_frac']):.3f}% tags {st.mean(agg['tags_frac']):.3f}% pen {st.mean(agg['pen_frac']):.3f}%")
# 分组
for grp, pred in [('local', lambda s: s.startswith('comp')), ('mc-a', lambda s: s.startswith('mc-a')), ('mc-b', lambda s: s.startswith('mc-b'))]:
    g = [i for i, r in enumerate(rows) if pred(r['s'])]
    print(f"{grp}: gap {st.mean([agg['gap'][i] for i in g]):.2f}%  ub {st.mean([agg['ubgap'][i] for i in g]):.2f}%  dbase {st.mean([agg['dbase'][i] for i in g]):.0f} dbonus {st.mean([agg['dbonus'][i] for i in g]):.0f} dcov {st.mean([agg['dcov'][i] for i in g]):.0f} dtiles {st.mean([agg['dtiles'][i] for i in g]):.1f}")
# base 相对
print(f"\n基线相对: obs14 vs base 均值 {st.mean([pct(r['total'], r['base_total']) for r in rows]):.2f}%")
# bonus ratio
print(f"bonus/base obs14 {st.mean([r['bonus']/r['base'] for r in rows]):.4f}  primal {st.mean([r['p_bonus']/r['p_base'] for r in rows]):.4f}")
print(f"base per tile obs14 {st.mean([r['base']/r['tiles'] for r in rows]):.2f}  primal {st.mean([r['p_base']/r['p_tiles'] for r in rows]):.2f}")
# evtruth 分项
d_tr = [(r['tr']['base_science']-r['base'], r['tr']['program_bonus']-r['bonus'], r['tr']['coverage_bonus']-r['cov'], r['tr']['report_reward']-r['tags'], r['tr']['completed_tiles']-r['tiles']) for r in rows if r['tr']]
d_fc = [(r['fc']['base_science']-r['base'], r['fc']['program_bonus']-r['bonus'], r['fc']['coverage_bonus']-r['cov'], r['fc']['report_reward']-r['tags'], r['fc']['completed_tiles']-r['tiles']) for r in rows if r['fc']]
for name, d in [('evtruth-obs14', d_tr), ('evfc-obs14', d_fc)]:
    print(f"{name} (n={len(d)}): dbase {st.mean(x[0] for x in d):.0f} dbonus {st.mean(x[1] for x in d):.0f} dcov {st.mean(x[2] for x in d):.0f} dtags {st.mean(x[3] for x in d):.0f} dtiles {st.mean(x[4] for x in d):.1f}")
