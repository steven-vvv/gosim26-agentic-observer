import json, statistics as st
from pathlib import Path
files = sorted(Path('/tmp/fable-0925c/offline').glob('*.json'))
rows = {}
for f in files:
    d = json.loads(f.read_text()); scen = f.name.split('.')[0]
    base = d.get('base_1h', {}).get('score')
    if not base: continue
    for k, v in d.items():
        rows.setdefault(k, {})[scen] = 100*(v['score']/base-1)
print(f"{'变体':20} {'n':>2} {'均值%':>8} {'sd':>6}  逐场")
for k, v in rows.items():
    vals = list(v.values())
    print(f"{k:20} {len(vals):2d} {st.mean(vals):+8.3f} {st.pstdev(vals) if len(vals)>1 else 0:6.3f}  " + " ".join(f"{s.split('-')[-1]}:{x:+.2f}" for s, x in v.items()))
