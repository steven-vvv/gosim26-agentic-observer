"""比较 /tmp/robust/<变体>/<场景>/score_report.json 与 results/<基准>/<场景>.json：逐场差异、均值、标准误、变好场数。"""
import json, sys, statistics as st
from pathlib import Path
base = sys.argv[1]; variants = sys.argv[2:]
for v in variants:
    diffs, rows = [], []
    for rep in sorted(Path(f"/tmp/robust/{v}").glob("*/score_report.json")):
        s = rep.parent.name
        ref = Path(f"results/{base}/{s}.json")
        if not ref.exists():
            continue
        r = json.loads(ref.read_text()); score = json.loads(rep.read_text())["score"]
        d = (score["total"] / r["total"] - 1) * 100
        diffs.append(d)
        rows.append(f"{s}:{d:+.2f}%")
    if diffs:
        se = st.stdev(diffs) / len(diffs) ** 0.5 if len(diffs) > 1 else float("nan")
        print(f"[{v}] {len(diffs)} 场  均值 {st.mean(diffs):+.3f}%  标准误 {se:.3f}  变好 {sum(d > 0 for d in diffs)}/{len(diffs)}")
        print("   " + "  ".join(rows))
