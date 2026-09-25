"""两组运行配对比较：variant 对 baseline（都在 /tmp/robust/<名字>/<场景>/score_report.json；
baseline 可以写成 "evfc+obs16"，表示先在 evfc 里找、找不到再到 obs16 里找）。输出总分与分项、按子集分组。"""
import json, sys, statistics as st
from pathlib import Path

def load(name):
    out = {}
    for part in name.split("+"):
        for rep in Path(f"/tmp/robust/{part}").glob("*/score_report.json"):
            out.setdefault(rep.parent.name, json.loads(rep.read_text()))
    return out

base, var = load(sys.argv[1]), load(sys.argv[2])
common = sorted(set(base) & set(var))
groups = {"全部": common, "本地": [s for s in common if s.startswith("comp")],
          "蒙特卡洛(旧 32)": [s for s in common if s.startswith("mc") and int(s[-4:]) % 100 <= 16],
          "蒙特卡洛(新 46)": [s for s in common if s.startswith("mc") and int(s[-4:]) % 100 >= 17]}
for g, keys in groups.items():
    if len(keys) < 2:
        continue
    d = [(var[s]["score"]["total"] / base[s]["score"]["total"] - 1) * 100 for s in keys]
    print(f"{g:14s} {len(keys):3d} 场  均值 {st.mean(d):+.3f}%  标准误 {st.stdev(d) / len(d) ** 0.5:.3f}  变好 {sum(x > 0 for x in d)}/{len(d)}")
comp = {}
for k in ("base_science", "program_bonus", "coverage_bonus", "report_reward"):
    comp[k] = st.mean(var[s]["score"].get(k, 0) - base[s]["score"].get(k, 0) for s in common)
pen = st.mean(sum(var[s]["score"]["penalties"].values()) - sum(base[s]["score"]["penalties"].values()) for s in common)
tiles = st.mean(len(var[s]["completion"]["completed_tiles"]) - len(base[s]["completion"]["completed_tiles"]) for s in common)
print("分项（每场均值）：" + "  ".join(f"{k} {v:+.0f}" for k, v in comp.items()) + f"  罚分 {pen:+.1f}  天区 {tiles:+.1f}")
