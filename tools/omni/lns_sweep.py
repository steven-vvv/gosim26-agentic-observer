"""对当前科学观测做滑动窗口 LNS 扫描（窗口内逐格精确 MILP）。

    uv run python tools/omni/lns_sweep.py kit/scenarios/dev-reference --init X.npy --out Y.npy [--width 6 --step 3]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP
from lns import window_milp
from model import Scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=6)
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--time-limit", type=float, default=300)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--windows", type=str, default="", help="逗号分隔的 a-b 夜窗口，覆盖滑动窗口")
    args = parser.parse_args()
    sc = Scenario(args.scenario.resolve())
    cdp = CDP(sc)
    sci = np.load(args.init).astype(np.int32)
    best = cdp.evaluate(sci)
    print(f"初始 {best:.6f}", flush=True)
    night = np.repeat(sc.night_of_slot, sc.per)
    nn = int(night.max()) + 1
    if args.windows:
        windows = [tuple(int(x) for x in w.split("-")) for w in args.windows.split(",")]
    else:
        windows = [(a, min(nn - 1, a + args.width - 1)) for a in range(0, nn, args.step)]
    for p in range(args.passes):
        improved = False
        for n1, n2 in windows:
            if not any(sci[i] >= 0 and n1 <= night[sci[i]] <= n2 for i in range(sc.n_tiles)):
                continue
            _total, plan = cdp.plan(sci)
            new, status, dt = window_milp(sc, sci, plan, n1, n2, args.time_limit)
            if new is None:
                print(f"  夜 {n1}-{n2}: {status}", flush=True)
                continue
            val = cdp.evaluate(new)
            if val > best + 1e-7:
                print(f"  夜 {n1}-{n2}: {best:.6f} -> {val:.6f}（{status}，{dt:.0f}s）", flush=True)
                best, sci = val, new
                improved = True
                np.save(args.out, sci)
        print(f"第 {p + 1} 轮结束：{best:.6f}", flush=True)
        if not improved:
            break
    np.save(args.out, sci)


if __name__ == "__main__":
    main()
