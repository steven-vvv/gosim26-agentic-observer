"""校准分分析：用官方基线代替“参考算法平均”，估算 obs14（= obs13-cf）在 46 个决赛规模场景上的校准分分布，
以及“一批 = A、B 各一场取平均，30 批取最好”的期望。只读 results/，不写仓库。"""
import glob, json, math, os, random, statistics as st

ROOT = "/home/steven/code/GOSIM26/v3"
W = -20800.0  # 全程等待分（简报实测 −20,300 ~ −21,400；对校准分的影响 ±2 分以内）


def load(agent):
    out = {}
    for f in glob.glob(f"{ROOT}/results/{agent}/*.json"):
        d = json.load(open(f))
        out[d["scenario"]] = d
    return out


obs = load("obs13-cf")
base = load("base")
ub = {}
for f in glob.glob(f"{ROOT}/results/upper_bound/*.json"):
    d = json.load(open(f))
    ub[d["scenario"]] = d

scen = sorted(s for s in obs if s in base and (s.startswith("comp") or s.startswith("mc")))
print(f"场景数 {len(scen)}")

rows = []
for s in scen:
    o, b = obs[s], base[s]
    cal = 10000.0 * (o["total"] - W) / (b["total"] - W)
    kind = "A" if "-a-" in s else "B"
    rows.append(dict(s=s, kind=kind, obs=o["total"], base=b["total"], cal=cal, rel=(o["total"] / b["total"] - 1) * 100,
                     fault_mult=o.get("fault_multiplier"), nfault=len(o.get("fault_regions") or []),
                     ub=ub.get(s, {}).get("upper_bound"), primal=(ub.get(s, {}).get("primal") or {}).get("total"),
                     even=o.get("coverage_evenness"), tiles=o.get("completed_tiles"), rep=o.get("report_reward")))

for kind in ("A", "B"):
    r = [x for x in rows if x["kind"] == kind]
    cal = [x["cal"] for x in r]
    rel = [x["rel"] for x in r]
    print(f"[{kind}] n={len(r)} 校准分 均值 {st.mean(cal):.0f} 标准差 {st.stdev(cal):.0f} 范围 {min(cal):.0f}–{max(cal):.0f}；"
          f"相对基线 +{st.mean(rel):.2f}%（{min(rel):+.2f}% ~ {max(rel):+.2f}%）")
    rawrel = [x["obs"] for x in r]
    print(f"     原始分 均值 {st.mean(rawrel):.0f} 相对标准差 {st.stdev(rawrel) / st.mean(rawrel) * 100:.1f}%；"
          f"校准分相对标准差 {st.stdev(cal) / st.mean(cal) * 100:.2f}%")

A = [x["cal"] for x in rows if x["kind"] == "A"]
B = [x["cal"] for x in rows if x["kind"] == "B"]
batches = [(a + b) / 2 for a in A for b in B]
mu, sd = st.mean(batches), st.stdev(batches)
print(f"\n一批（A、B 各一场平均）：均值 {mu:.0f}，标准差 {sd:.0f}（按独立配对 {len(batches)} 组）")
# 30 批取最好：经验 bootstrap
random.seed(1)
for n in (1, 5, 10, 20, 30):
    sims = []
    for _ in range(20000):
        sims.append(max((random.choice(A) + random.choice(B)) / 2 for _ in range(n)))
    print(f"  {n:2d} 批取最好：期望 {st.mean(sims):.0f}（标准差 {st.stdev(sims):.0f}），P10 {sorted(sims)[2000]:.0f}，P90 {sorted(sims)[18000]:.0f}")
c30 = 2.043
print(f"  正态近似：μ + 2.043σ = {mu + c30 * sd:.0f}")

# 轨迹噪声（中性扰动）对“取最好”的额外贡献
sigma_traj = 0.0015 * mu / math.sqrt(2)  # 单场 0.15% → 两场平均
print(f"\n轨迹噪声（中性扰动，两场平均）σ≈{sigma_traj:.0f}；与种子噪声 σ≈{sd:.0f} 合成 σ≈{math.hypot(sd, sigma_traj):.0f}，"
      f"30 批取最好的期望只多 {c30 * (math.hypot(sd, sigma_traj) - sd):.1f} 分")

# 校准分与天气/故障的关系
def corr(x, y):
    mx, my = st.mean(x), st.mean(y)
    sx, sy = st.stdev(x), st.stdev(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / ((len(x) - 1) * sx * sy)

print("\n校准分与场景特征的相关系数（46 场）：")
print(f"  与基线原始分（天气好坏）: {corr([x['cal'] for x in rows], [x['base'] for x in rows]):+.2f}")
print(f"  与故障乘数: {corr([x['cal'] for x in rows], [x['fault_mult'] for x in rows]):+.2f}")
print(f"  与故障分区数: {corr([x['cal'] for x in rows], [x['nfault'] for x in rows]):+.2f}")
print(f"  与上报奖励: {corr([x['cal'] for x in rows], [x['rep'] for x in rows]):+.2f}")
have = [x for x in rows if x["primal"]]
if have:
    gap = [(x["primal"] / x["obs"] - 1) * 100 for x in have]
    print(f"  全知可行排程高于 obs14：均值 {st.mean(gap):.2f}%，范围 {min(gap):.2f}% ~ {max(gap):.2f}%（n={len(have)}）")
    gap_ub = [(x["ub"] / x["obs"] - 1) * 100 for x in have]
    print(f"  完美信息上界高于 obs14：均值 {st.mean(gap_ub):.2f}%")

# 参考算法更强/更弱时校准分的变化（只影响绝对值，不影响相对排名；但影响“分母随天气”的权重）
print("\n参考算法平均 = 基线 × k 时的校准分均值：")
for k in (0.90, 0.95, 1.00, 1.02, 1.05):
    cal = [10000.0 * (x["obs"] - W) / (x["base"] * k - W) for x in rows]
    print(f"  k={k:.2f}: 均值 {st.mean(cal):.0f}，标准差 {st.stdev(cal):.0f}")

# 校准分的相对权重：Δ原始分 1% 折合多少校准分
d = st.mean([10000.0 * 0.01 * x["obs"] / (x["base"] - W) for x in rows])
print(f"\n原始分每 +1% ≈ 校准分 +{d:.0f}（以基线为参考）")

print("\n逐场（校准分从低到高）：")
for x in sorted(rows, key=lambda r: r["cal"]):
    print(f"  {x['s']:<12} {x['kind']} cal={x['cal']:.0f} rel={x['rel']:+.2f}% base={x['base']:.0f} fault={x['fault_mult']} x{x['nfault']} even={x['even']} tiles={x['tiles']} rep={x['rep']}")
