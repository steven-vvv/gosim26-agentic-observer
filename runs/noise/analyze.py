import json, math, os, statistics as st
S = ["comp-a-s102", "comp-b-s205", "mc-a-9106", "mc-b-9203"]
A = ["obs11-frozen"] + sorted(d for d in os.listdir("results") if d.startswith("n-"))
tab = {a: {s: json.load(open(f"results/{a}/{s}.json"))["total"] for s in S if os.path.exists(f"results/{a}/{s}.json")} for a in A}
print(f"{'配置':14}" + "".join(f"{s:>14}" for s in S))
for a in A:
    print(f"{a:14}" + "".join(f"{(tab[a][s] / tab['obs11-frozen'][s] - 1) * 100:+13.3f}%" if s in tab[a] else f"{'—':>14}" for s in S))
sds = []
for s in S:
    vals = [tab[a][s] for a in A if s in tab[a]]
    if len(vals) >= 3:
        sds.append(st.stdev(v / st.mean(vals) for v in vals) * 100)
print("各场景相对标准差（%）:", [round(x, 3) for x in sds])
if sds:
    sigma = st.mean(sds)
    stage = sigma / math.sqrt(2)   # 阶段得分 = 两个场景的平均；两场的扰动独立
    for n, c in ((10, 1.539), (20, 1.867), (30, 2.043)):
        print(f"  提交 {n:2d} 个中性变体取最好：期望比单次多 {c * stage:.2f}%（单场 σ≈{sigma:.2f}%，阶段 σ≈{stage:.2f}%）")
