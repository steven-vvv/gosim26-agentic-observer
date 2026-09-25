"""时间账：每场观测/等待/中断各占多少秒；中断浪费的曝光时间。"""
import json, statistics as st
from pathlib import Path
rows = []
for rep in sorted(Path('/tmp/robust/evfc').glob('*/score_report.json')):
    d = json.loads(rep.read_text())
    obs = wait = intr = 0.0; n_intr = 0; n_wait = 0; n_obs = 0; invalid = 0
    per_intr = []
    for a in d['actions']:
        el = float(a.get('elapsed_seconds') or 0)
        if a['action'] == 'wait':
            wait += el; n_wait += 1
        elif a['outcome'] == 'completed':
            obs += el; n_obs += 1
        elif a['outcome'] == 'weather_interrupted':
            intr += el; n_intr += 1; per_intr.append(el)
        else:
            invalid += 1
    rows.append((rep.parent.name, obs, wait, intr, n_obs, n_wait, n_intr, invalid, d['wait_seconds'] if 'wait_seconds' in d else None))
tot = lambda i: st.mean(r[i] for r in rows)
print(f"n={len(rows)}  observe {tot(1)/3600:.1f} h ({tot(4):.0f} 次)  wait {tot(2)/3600:.1f} h ({tot(5):.0f} 次)  interrupted {tot(3)/3600:.2f} h ({tot(6):.1f} 次)  other {tot(7):.1f}")
print(f"中断浪费时间占观测时间 {100*tot(3)/tot(1):.2f}%")
print(rows[0])
# 看看一个中断动作的样子
d = json.loads(Path('/tmp/robust/evfc/comp-a-s101/score_report.json').read_text())
for a in d['actions']:
    if a['outcome'] == 'weather_interrupted':
        print(json.dumps(a)[:600]); break
for a in d['actions']:
    if a['action'] == 'wait':
        print(json.dumps(a)[:600]); break
print(json.dumps(d['completion'])[:500]); print(d.get('wait_seconds'))
