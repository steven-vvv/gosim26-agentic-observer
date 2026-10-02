"""汇总天气信息价值实验：各变体相对 price 1h（现有定价）的配对差异。"""
import re
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAT = re.compile(r"\[(.+?)\s*\]\s+天区\s+(\d+)\s+Σ 得分 =\s+(\d+)")


def scores(path):
    return {m.group(1).strip(): int(m.group(3)) for m in PAT.finditer(path.read_text(encoding="utf-8"))} if path.exists() else {}


rows = {}
for f in sorted(HERE.glob("comp-*.txt")):
    s = f.stem
    rows[s] = scores(f)
    for g in (HERE / "decomp").glob(f"{s}-*.txt"):
        rows[s].update(scores(g))
variants = ["完美预报", "知道关闭", "知道每夜天气", "知道每格天气", "持续性"]
print(f"{'场景':12} {'price 1h':>9} " + " ".join(f"{v:>8}" for v in variants))
gains = {v: [] for v in variants}
for s, r in rows.items():
    if "price 1h" not in r:
        continue
    cells = []
    for v in variants:
        if v in r:
            g = (r[v] / r["price 1h"] - 1) * 100
            gains[v].append(g)
            cells.append(f"{g:+7.2f}%")
        else:
            cells.append(f"{'—':>8}")
    print(f"{s:12} {r['price 1h']:9d} " + " ".join(cells))
print()
for v in variants:
    g = gains[v]
    if g:
        sd = st.stdev(g) if len(g) > 1 else 0.0
        print(f"{v:8}  {len(g):2d} 场  均值 {st.mean(g):+.2f}%  标准误 {sd / len(g) ** 0.5:.2f}  范围 [{min(g):+.2f}%, {max(g):+.2f}%]")
