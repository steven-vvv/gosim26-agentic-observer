#!/usr/bin/env python3
"""汇总 py-spy 的原始采样（--format raw，可带 --subprocesses）：按进程列出自身耗时与累计耗时最高的函数。

    uvx py-spy record --subprocesses --format raw -r 100 -o prof.txt -- python kit/local_runner.py ...
    uv run python tools/pyspy_summary.py prof.txt [--top 25]

每行是“帧;帧;…;帧 次数”，带 --subprocesses 时第一帧是 process PID:"命令行"。函数按“函数名 (文件)”合并，
不区分行号；累计耗时里同一个栈中重复出现的函数只计一次（递归不重复计）。
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

FRAME = re.compile(r"^(.*) \((.*?)(?::\d+)?\)$")


def label(frame: str) -> str:
    m = FRAME.match(frame)
    return f"{m.group(1)} ({m.group(2)})" if m else frame


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--rate", type=float, default=100.0, help="采样频率（次/秒），用于把样本数换算成秒")
    args = parser.parse_args(argv)
    total, self_time, inclusive, names = Counter(), defaultdict(Counter), defaultdict(Counter), {}
    for line in args.raw.read_text(encoding="utf-8").splitlines():
        stack, _, count = line.rpartition(" ")
        frames = stack.split(";")
        process = "main"
        while frames and frames[0].startswith("process "):  # 子进程的栈前面还挂着父进程的帧：取最内层的进程
            process = frames[0].split(":")[0]
            frames = frames[1:]
        n = int(count)
        total[process] += n
        if frames:
            labels = [label(f) for f in frames]
            self_time[process][labels[-1]] += n
            for name in set(labels):
                inclusive[process][name] += n
            names.setdefault(process, next((x for x in labels if ".py" in x and "<module>" in x), labels[0]))
    for process, n in total.most_common():
        print(f"== {process}  {names.get(process, '')}  共 {n} 个样本 ≈ {n / args.rate:.0f} 秒 CPU")
        print(f"   {'自身':>7} {'占比':>6}   {'累计':>7} {'占比':>6}   函数")
        rows = sorted(set(self_time[process]) | set(inclusive[process]), key=lambda k: -inclusive[process][k])
        shown = set()
        for key, _ in self_time[process].most_common(args.top):
            shown.add(key)
        for key in rows[:args.top]:
            shown.add(key)
        for key in sorted(shown, key=lambda k: (-self_time[process][k], -inclusive[process][k])):
            s, c = self_time[process][key], inclusive[process][key]
            print(f"   {s / args.rate:6.1f}s {s / n:6.1%}   {c / args.rate:6.1f}s {c / n:6.1%}   {key}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
