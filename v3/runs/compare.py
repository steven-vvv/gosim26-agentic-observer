"""对比若干 agent 相对参照 agent 的逐场差异（只统计都有结果的场景）。  python3 runs/compare.py obs10 obs11 obs11p"""
import json, os, statistics as st, sys
ref, agents = sys.argv[1], sys.argv[2:]
scen = sorted(f[:-5] for f in os.listdir(f"results/{ref}") if f.startswith(("comp-", "mc-")))
for a in agents:
    rows = []
    for s in scen:
        p = f"results/{a}/{s}.json"
        if os.path.exists(p):
            rows.append((s, json.load(open(f"results/{ref}/{s}.json")), json.load(open(p))))
    if not rows:
        continue
    g = [(b["total"] / r["total"] - 1) * 100 for _, r, b in rows]
    sd = st.stdev(g) if len(g) > 1 else 0.0
    print(f"== {a} 相对 {ref}：{len(rows)} 场，均值 {st.mean(g):+.3f}%（标准误 {sd / len(g) ** 0.5:.3f}），范围 [{min(g):+.2f}%, {max(g):+.2f}%]，"
          f"变好 {sum(x > 0 for x in g)} 场")
    for k in ("base_science", "program_bonus", "coverage_bonus", "report_reward", "request_reward"):
        print(f"   {k:15} {st.mean(b[k] - r[k] for _, r, b in rows):+9.1f}")
    pen = st.mean(sum((b.get("penalties") or {}).values()) - sum((r.get("penalties") or {}).values()) for _, r, b in rows)
    print(f"   {'罚分':15} {pen:+9.1f}")
    dr = [(r.get("fault_delay_hours"), b.get("fault_delay_hours")) for _, r, b in rows]
    dr = [(x, y) for x, y in dr if x is not None and y is not None]
    if dr:
        print(f"   故障检出延迟（小时）中位数 {st.median(x for x, _ in dr):.1f} → {st.median(y for _, y in dr):.1f}，"
              f"均值 {st.mean(x for x, _ in dr):.1f} → {st.mean(y for _, y in dr):.1f}；故障误报 "
              f"{sum(r.get('fault_misreports') or 0 for _, r, _ in rows)} → {sum(b.get('fault_misreports') or 0 for _, _, b in rows)}")
