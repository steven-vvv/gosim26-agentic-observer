"""(1) 混合不同变体提交能否抬高“30 批取最好”的上尾；(2) 校准分对场景特征的多元回归。只读 results/。"""
import glob, json, os, random, statistics as st
import numpy as np

ROOT = "/home/steven/code/GOSIM26"
W = -20800.0


def load(agent):
    out = {}
    for f in glob.glob(f"{ROOT}/results/{agent}/*.json"):
        d = json.load(open(f))
        if d["scenario"].startswith(("comp", "mc")):
            out[d["scenario"]] = d
    return out


base = load("base")
ref = load("obs13-cf")
cal = lambda o, b: 10000.0 * (o["total"] - W) / (b["total"] - W)
ref_cal = {s: cal(ref[s], base[s]) for s in ref if s in base}

print("变体 vs obs14（同场景校准分差；混合提交的 30 批取最好用 bootstrap 估算）")
print(f"{'变体':<16}{'n':>4}{'均值差':>9}{'差的σ':>8}{'相关':>7}{'全obs14':>9}{'15/15混合':>10}{'25/5混合':>9}")
random.seed(7)
agents = sorted(d for d in os.listdir(f"{ROOT}/results") if d.startswith("obs1") and d not in ("obs13-cf", "obs14"))
for a in agents:
    v = load(a)
    common = sorted(s for s in v if s in ref_cal)
    if len(common) < 12:
        continue
    vc = {s: cal(v[s], base[s]) for s in common}
    diffs = [vc[s] - ref_cal[s] for s in common]
    A = [s for s in common if "-a-" in s]
    B = [s for s in common if "-b-" in s]
    if len(A) < 4 or len(B) < 4:
        continue
    corr = np.corrcoef([vc[s] for s in common], [ref_cal[s] for s in common])[0, 1]

    def emax(n_ref, n_var, trials=20000):
        tot = 0.0
        for _ in range(trials):
            best = -1e9
            for _ in range(n_ref):
                best = max(best, (ref_cal[random.choice(A)] + ref_cal[random.choice(B)]) / 2)
            for _ in range(n_var):
                best = max(best, (vc[random.choice(A)] + vc[random.choice(B)]) / 2)
            tot += best
        return tot / trials

    print(f"{a:<16}{len(common):>4}{st.mean(diffs):>+9.1f}{st.stdev(diffs):>8.1f}{corr:>7.3f}{emax(30, 0):>9.0f}{emax(15, 15):>10.0f}{emax(25, 5):>9.0f}")

# 多元回归：校准分 ~ 故障乘数 + 故障分区数 + A/B + 基线原始分
rows = []
for s, c in ref_cal.items():
    o = ref[s]
    rows.append((c, o.get("fault_multiplier") or 0.55, len(o.get("fault_regions") or []), 1.0 if "-b-" in s else 0.0, base[s]["total"] / 1e5))
y = np.array([r[0] for r in rows])
X = np.column_stack([np.ones(len(rows))] + [np.array([r[k] for r in rows]) for k in range(1, 5)])
beta, *_ = np.linalg.lstsq(X, y, rcond=None)
pred = X @ beta
r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
print("\n回归 cal = b0 + b1·故障乘数 + b2·故障分区数 + b3·[B 场景] + b4·基线原始分/1e5")
print("  系数:", [round(float(b), 1) for b in beta], " R² = %.2f" % r2, " 残差σ = %.0f" % float(np.std(y - pred, ddof=5)))
print("  解释：故障乘数从 0.4 → 0.7 约改变 %.0f 分；B 场景比 A 高约 %.0f 分" % (beta[1] * 0.3, beta[3]))
