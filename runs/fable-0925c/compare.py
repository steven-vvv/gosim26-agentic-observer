"""比较 /tmp/fable-0925c/runs/<变体>/<场景>/score_report.json 与 results/obs13-cf（=obs14）及 /tmp/robust/evfc（=obs16）：逐场差异、均值、标准误、变好场数、分项。"""
import json, sys, statistics as st, math
from pathlib import Path
ROOT = Path('/home/steven/code/GOSIM26')
variants = sys.argv[1:] or ['fc2', 'fc2cold']
def rep(path):
    if not path.exists(): return None
    d = json.loads(path.read_text()); sc = d['score']
    tiles = len({a['tile_id'] for a in d['actions'] if a.get('outcome') == 'completed'})
    return dict(total=sc['total'], base=sc['base_science'], bonus=sc['program_bonus'], cov=sc['coverage_bonus'], even=sc['coverage_evenness'],
                tags=sc.get('report_reward', 0), pen=sum((sc.get('penalties') or {}).values()), tiles=tiles, term=d.get('termination_reason'))
for v in variants:
    rows = []
    for p in sorted(Path(f'/tmp/fable-0925c/runs/{v}').glob('*/score_report.json')):
        s = p.parent.name
        r = rep(p); b = json.loads((ROOT/'results/obs13-cf'/f'{s}.json').read_text()); e = rep(Path(f'/tmp/robust/evfc/{s}/score_report.json'))
        rows.append((s, r, b, e))
    if not rows: print(f"[{v}] 无结果"); continue
    d14 = [100*(r['total']/b['total']-1) for s, r, b, e in rows]
    d16 = [100*(r['total']/e['total']-1) for s, r, b, e in rows if e]
    se = lambda x: st.pstdev(x)/math.sqrt(len(x)) if len(x) > 1 else float('nan')
    print(f"[{v}] {len(rows)} 场  vs obs14: 均值 {st.mean(d14):+.3f}% 标准误 {se(d14):.3f} 变好 {sum(x>0 for x in d14)}/{len(d14)}   vs obs16: 均值 {st.mean(d16):+.3f}% 标准误 {se(d16):.3f} 变好 {sum(x>0 for x in d16)}/{len(d16)}")
    for grp, pred in [('local', lambda s: s.startswith('comp')), ('mc-a', lambda s: s.startswith('mc-a')), ('mc-b', lambda s: s.startswith('mc-b'))]:
        g = [100*(r['total']/e['total']-1) for s, r, b, e in rows if pred(s) and e]
        if g: print(f"    {grp}: n={len(g)} vs obs16 {st.mean(g):+.3f}%")
    comp = lambda k: st.mean(r[k]-e[k] for s, r, b, e in rows if e)
    print(f"    分项 vs obs16: base {comp('base'):+.0f} bonus {comp('bonus'):+.0f} cov {comp('cov'):+.0f} tags {comp('tags'):+.0f} pen {comp('pen'):+.0f} tiles {comp('tiles'):+.1f}")
    bad = [(s, r['term']) for s, r, b, e in rows if r['term'] != 'survey_complete']
    if bad: print("    异常终止:", bad)
    print("    " + "  ".join(f"{s}:{100*(r['total']/e['total']-1):+.2f}" for s, r, b, e in rows if e))
