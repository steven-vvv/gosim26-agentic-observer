#!/usr/bin/env python3
"""异常读数分析：给 agent 的每次读数标注真值类别，离线评估标签与故障的上报规则。

    uv run python tools/evaluate.py --agent readlog=<log_reads 打开的 agent>/main.py --scenario ... --keep
    uv run python tools/read_analysis.py runs/eval/readlog

读数来自 agent.log 里 log_reads 打开时的 “observer: read …” 行（比值 = 实现分 / 分段期望分，只有带时刻的新格式）。
真值：tile_anomalies.csv 的标签；instrument_fault 事件的分区与开始时刻，结束取评分报告里正确上报的 repair_complete_utc。
标签上报不影响 agent 的任何决策（只在终局结算），所以离线换一种上报规则，得到的就是真实运行会得到的结果。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics as st
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
READ = re.compile(r"observer: read t=(\d+) tile=(\S+) region=(\S+) ratio=([\d.]+) simple=([\d.]+) clean=(\d) dip=(\d) "
                  r"suspect=(\d) faultreg=(\d) n=(\d+)(?: segs=(\d+) exact=([\d.]+))?(?: interp=([\d.]+))?(?: cw=(\S+) cwexact=([\d.]+))?")
REWARD, PENALTY = 100.0, 150.0


def epoch(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def load_run(run: Path) -> dict:
    scenario = ROOT / "scenarios" / run.name
    ref = scenario / "outputs" / "reference"
    with (ref / "tile_anomalies.csv").open(encoding="utf-8-sig") as handle:
        tags = {row["tile_id"]: row["anomaly_tag"] for row in csv.DictReader(handle)}
    with (ref / "weather_events.csv").open(encoding="utf-8-sig") as handle:
        faults = [row for row in csv.DictReader(handle) if row["condition"] == "instrument_fault"]
    report = json.loads((run / "score_report.json").read_text(encoding="utf-8"))
    fault = None
    if faults:
        row = faults[0]
        end = epoch(row["actual_end_utc"])
        repaired = [epoch(a["report_result"]["repair_complete_utc"]) for a in report["actions"]
                    if a["outcome"] == "report_correct" and (a.get("report_result") or {}).get("repair_complete_utc")]
        detected = [epoch(a["start_utc"]) for a in report["actions"] if a["outcome"] == "report_correct"]
        fault = {"regions": set(json.loads(row["spatial_scope_payload"])["region_ids"]), "start": epoch(row["actual_start_utc"]),
                 "end": min([end, *repaired]), "detected": min(detected) if detected else None,
                 "multiplier": float(row["instrument_efficiency_multiplier"])}
    reads = []
    for line in (run / "agent.log").read_text(encoding="utf-8").splitlines():
        m = READ.match(line)
        if not m:
            continue
        t, tile, region = float(m.group(1)), m.group(2), m.group(3)
        in_fault = bool(fault and region in fault["regions"] and fault["start"] <= t < fault["end"])
        reads.append({"t": t, "tile": tile, "region": region, "ratio": float(m.group(4)), "simple": float(m.group(5)),
                      "clean": m.group(6) == "1", "dip": m.group(7) == "1", "suspect": m.group(8) == "1",
                      "faultreg": m.group(9) == "1", "n": int(m.group(10)), "segs": int(m.group(11) or 0),
                      "exact": float(m.group(12) or 0.0), "interp": float(m.group(13) or 0.0),
                      "cw": m.group(14) if m.group(14) not in (None, "-") else None, "cwexact": float(m.group(15) or 0.0),
                      "tag": tags.get(tile), "fault": in_fault})
    return {"name": run.name, "tags": tags, "fault": fault, "reads": reads, "report": report}


def label(read: dict) -> str:
    return (read["tag"] or "normal") + ("+fault" if read["fault"] else "")


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else float("nan")


def reddening_rule(run: dict, low: float, high: float, reads_needed: int = 1) -> tuple[int, int]:
    """红化上报：干净、非全局下沉、分区不在疑似/已确认故障中的读数落在 [low, high]；reads_needed=2 为 obs10 现行规则。"""
    history: dict = {}
    reported: set = set()
    correct = wrong = 0
    for r in run["reads"]:
        if not r["clean"] or r["dip"]:
            continue  # 与 agent 一致：下沉时直接丢弃（比值低于 NOVA 阈值的读数不进入标签逻辑）
        seen = history.setdefault(r["tile"], [])
        seen.append(r["ratio"])
        if r["tile"] in reported or r["suspect"] or r["faultreg"] or len(seen) < reads_needed:
            continue
        if all(low <= x <= high for x in seen[-reads_needed:]):
            reported.add(r["tile"])
            if r["tag"] == "reddening":
                correct += 1
            else:
                wrong += 1
    return correct, wrong


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", type=Path, help="evaluate.py --keep 的运行目录，如 runs/eval/readlog")
    args = parser.parse_args(argv)
    runs = [load_run(path) for path in sorted(args.runs.iterdir()) if (path / "agent.log").exists() and (path / "score_report.json").exists()]
    runs = [r for r in runs if r["reads"]]
    print(f"{len(runs)} 场，读数 {sum(len(r['reads']) for r in runs)} 次")

    # 1. 各类读数的比值分布（干净且非下沉）
    groups: dict = {}
    for run in runs:
        for r in run["reads"]:
            if r["clean"] and not r["dip"]:
                groups.setdefault(label(r), []).append(r["ratio"])
    print("\n干净、非下沉读数的比值分布        次数     P1     P5    P50    P95    P99")
    for name, values in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {name:28} {len(values):6d}  " + "  ".join(f"{pct(values, q):5.3f}" for q in (0.01, 0.05, 0.5, 0.95, 0.99)))
    normal = sorted(groups.get("normal", []))
    for x in (0.80, 0.82, 0.84, 0.86):
        print(f"  普通读数中比值 ≤ {x:.2f} 的有 {sum(v <= x for v in normal)} 次（{sum(v <= x for v in normal) / max(1, len(normal)):.2%}）")

    # 2. 红化规则网格（净收益 = 100 × 报对 − 150 × 报错）
    local = [r for r in runs if r["name"].startswith("comp-")]
    mc = [r for r in runs if r["name"].startswith("mc-")]
    print("\n红化上报规则（全部场景的报对 / 报错 / 每场净收益；括号里是本地 14 场、蒙特卡洛 16 场各自的每场净收益）")
    current = [reddening_rule(r, 0.68, 0.86, 2) for r in runs]
    c, w = sum(x for x, _ in current), sum(y for _, y in current)
    print(f"  现行（两读都在 [0.68, 0.86]）  报对 {c:3d} 报错 {w:3d}  每场 {(REWARD * c - PENALTY * w) / len(runs):+7.1f}")
    best = None
    for low in (0.64, 0.66, 0.68, 0.70, 0.72):
        for high in (0.78, 0.79, 0.80, 0.81, 0.82, 0.83, 0.84, 0.86):
            res = {name: [reddening_rule(r, low, high) for r in group] for name, group in (("all", runs), ("local", local), ("mc", mc))}
            net = {name: (REWARD * sum(x for x, _ in v) - PENALTY * sum(y for _, y in v)) / max(1, len(v)) for name, v in res.items()}
            c, w = sum(x for x, _ in res["all"]), sum(y for _, y in res["all"])
            print(f"  单读 [{low:.2f}, {high:.2f}]  报对 {c:3d} 报错 {w:3d}  每场 {net['all']:+7.1f}  ({net['local']:+7.1f} / {net['mc']:+7.1f})")
            if best is None or net["all"] > best[0]:
                best = (net["all"], low, high)
    print(f"  → 全部场景最好的单读区间 [{best[1]:.2f}, {best[2]:.2f}]，每场净收益 {best[0]:+.1f}")
    completed_red = []
    for run in runs:
        done = {a["tile_id"] for a in run["report"]["actions"] if a["outcome"] == "completed"}
        completed_red.append(sum(1 for t, tag in run["tags"].items() if tag == "reddening" and t in done))
    print(f"  已完成的红化天区每场 {st.mean(completed_red):.1f} 块（上限 {REWARD * st.mean(completed_red):.0f} 分/场）")

    # 2b. 精确读数（事后期望：各段用所在时隙的真实有效天气）
    exact = [r for run in runs for r in run["reads"] if r["exact"] > 0]
    if exact:
        print(f"\n精确读数（事后期望）{len(exact)} 次，占全部读数 {len(exact) / sum(len(run['reads']) for run in runs):.0%}")
        print("  类别                            次数    最小   P0.1     P1    P50    P99  P99.9    最大")
        classes: dict = {}
        for r in exact:
            classes.setdefault(label(r), []).append(r["exact"])
        for name, values in sorted(classes.items(), key=lambda kv: -len(kv[1])):
            print(f"  {name:28} {len(values):6d}  {min(values):6.3f} " + " ".join(f"{pct(values, q):6.3f}" for q in (0.001, 0.01, 0.5, 0.99, 0.999))
                  + f" {max(values):6.3f}")
        print("\n  红化（精确读数单读）：区间 / 是否排除疑似故障分区 → 报对 / 报错 / 每场净收益（本地 / 蒙特卡洛）")
        for low, high in ((0.70, 0.81), (0.71, 0.805), (0.715, 0.805), (0.715, 0.81), (0.72, 0.80), (0.70, 0.82)):
            for guard in (True, False):
                def rule(run, low=low, high=high, guard=guard):
                    reported, ok, bad = set(), 0, 0
                    for r in run["reads"]:
                        if r["exact"] <= 0 or r["tile"] in reported or (guard and (r["suspect"] or r["faultreg"])):
                            continue
                        if low <= r["exact"] <= high:
                            reported.add(r["tile"])
                            ok, bad = (ok + 1, bad) if r["tag"] == "reddening" else (ok, bad + 1)
                    return ok, bad
                res = {name: [rule(r) for r in group] for name, group in (("all", runs), ("local", local), ("mc", mc))}
                net = {name: (REWARD * sum(x for x, _ in v) - PENALTY * sum(y for _, y in v)) / max(1, len(v)) for name, v in res.items()}
                print(f"    [{low:.3f}, {high:.3f}] {'排除' if guard else '不排除'}  报对 {sum(x for x, _ in res['all']):3d} "
                      f"报错 {sum(y for _, y in res['all']):3d}  每场 {net['all']:+7.1f} ({net['local']:+7.1f} / {net['mc']:+7.1f})")
        covered = []
        for run in runs:
            done = {a["tile_id"] for a in run["report"]["actions"] if a["outcome"] == "completed"}
            red = [t for t, tag in run["tags"].items() if tag == "reddening" and t in done]
            with_exact = {r["tile"] for r in run["reads"] if r["exact"] > 0}
            covered.append((len(red), sum(t in with_exact for t in red)))
        print(f"  已完成的红化天区 {sum(a for a, _ in covered)} 块，其中有精确读数的 {sum(b for _, b in covered)} 块")
        # 故障：精确读数最早什么时候能看出来
        saved = []
        for run in runs:
            f = run["fault"]
            if not f or f["detected"] is None:
                continue
            first = next((r["t"] for r in run["reads"] if r["fault"] and 0 < r["exact"] <= 0.705), None)
            if first is not None and first < f["detected"]:
                saved.append((run["name"], (f["detected"] - first) / 3600))
        false_low = sum(1 for r in exact if not r["fault"] and r["exact"] <= 0.705)
        print(f"  故障：{len(saved)} 场能用精确读数提前检出，提前 {sorted(round(h, 1) for _, h in saved)} 小时；"
              f"非故障读数里精确比值 ≤ 0.705 的有 {false_low} 次")

    # 2c. 插值读数（夹在两个已知时隙之间的那一段用两侧平均估计），只看没有精确读数的情形
    interp = [r for run in runs for r in run["reads"] if r["interp"] > 0 and r["exact"] <= 0]
    if interp:
        print(f"\n插值读数 {len(interp)} 次（没有精确值时才计）")
        print("  类别                            次数    最小   P0.1     P1    P50    P99  P99.9    最大")
        classes = {}
        for r in interp:
            classes.setdefault(label(r), []).append(r["interp"])
        for name, values in sorted(classes.items(), key=lambda kv: -len(kv[1])):
            print(f"  {name:28} {len(values):6d}  {min(values):6.3f} " + " ".join(f"{pct(values, q):6.3f}" for q in (0.001, 0.01, 0.5, 0.99, 0.999))
                  + f" {max(values):6.3f}")
        for low, high in ((0.71, 0.81), (0.72, 0.80), (0.73, 0.80), (0.70, 0.82)):
            ok = bad = 0
            for run in runs:
                reported = {r["tile"] for r in run["reads"] if 0.71 <= r["exact"] <= 0.81}
                for r in run["reads"]:
                    if r["exact"] > 0 or r["interp"] <= 0 or r["tile"] in reported or r["suspect"] or r["faultreg"]:
                        continue
                    if low <= r["interp"] <= high:
                        reported.add(r["tile"])
                        ok, bad = (ok + 1, bad) if r["tag"] == "reddening" else (ok, bad + 1)
            print(f"  红化补报 [{low:.2f}, {high:.2f}]：报对 {ok} 报错 {bad}，每场 {(REWARD * ok - PENALTY * bad) / len(runs):+.1f}")
        for low in (1.25, 1.30):
            ok = bad = 0
            for run in runs:
                reported = {r["tile"] for r in run["reads"] if r["exact"] >= 1.30 or (r["clean"] and 1.28 <= r["ratio"] <= 1.70)}
                for r in run["reads"]:
                    if r["exact"] > 0 or r["interp"] <= 0 or r["tile"] in reported:
                        continue
                    if low <= r["interp"] <= 1.70:
                        reported.add(r["tile"])
                        ok, bad = (ok + 1, bad) if r["tag"] == "nova" else (ok, bad + 1)
            print(f"  新星补报 ≥ {low:.2f}：报对 {ok} 报错 {bad}，每场 {(REWARD * ok - PENALTY * bad) / len(runs):+.1f}")

    # 2d. 寒潮期读数：按事件估计效率乘数 m（用同一事件此前其他天区的读数），读数除以 m 后套用区间
    cold = [r for run in runs for r in run["reads"] if r["cw"] and r["cwexact"] > 0]
    if cold:
        print(f"\n寒潮期读数 {len(cold)} 次（涉及 {len({(run['name'], r['cw']) for run in runs for r in run['reads'] if r['cw']})} 个事件）")
        estimators = {"中位数/0.95": lambda xs: st.median(xs) / 0.95, "最大值": max,
                      "截尾均值/0.95": lambda xs: st.mean(sorted(xs)[len(xs) // 4: len(xs) - len(xs) // 4]) / 0.95}
        for name, estimate in estimators.items():
            for need in (3, 5):
                normalized = []
                for run in runs:
                    seen: dict = {}
                    for r in run["reads"]:
                        if not r["cw"] or r["cwexact"] <= 0:
                            continue
                        prior = seen.setdefault(r["cw"], [])
                        if len(prior) >= need:
                            normalized.append((run, r, r["cwexact"] / estimate(prior)))
                        prior.append(r["cwexact"])
                classes: dict = {}
                for _run, r, x in normalized:
                    classes.setdefault(label(r), []).append(x)
                summary = "  ".join(f"{k} {len(v)} 次 [{pct(v, 0.01):.3f}, {pct(v, 0.99):.3f}]" for k, v in sorted(classes.items(), key=lambda kv: -len(kv[1])))
                ok_r = bad_r = ok_n = bad_n = 0
                for run in runs:
                    done_tags = {r["tile"] for r in run["reads"] if 0.71 <= r["exact"] <= 0.81 or r["exact"] >= 1.30
                                 or (r["clean"] and 1.28 <= r["ratio"] <= 1.70)}
                    for rr, r, x in ((rr, r, x) for rr, r, x in normalized if rr is run):
                        if r["tile"] in done_tags or r["suspect"] or r["faultreg"]:
                            continue
                        if 0.73 <= x <= 0.79:
                            done_tags.add(r["tile"])
                            ok_r, bad_r = (ok_r + 1, bad_r) if r["tag"] == "reddening" else (ok_r, bad_r + 1)
                        elif 1.30 <= x <= 1.70:
                            done_tags.add(r["tile"])
                            ok_n, bad_n = (ok_n + 1, bad_n) if r["tag"] == "nova" else (ok_n, bad_n + 1)
                net = (REWARD * (ok_r + ok_n) - PENALTY * (bad_r + bad_n)) / len(runs)
                print(f"  {name:12} 至少 {need} 个先验读数：红化补报对 {ok_r} 错 {bad_r}，新星补报对 {ok_n} 错 {bad_n}，每场 {net:+.1f}")
                print(f"     {summary}")

    # 3. 新星：漏报原因
    missed = {"没有读数": 0, "只有不干净的读数": 0, "干净读数都不在 [1.28, 1.70]": 0}
    caught = 0
    for run in runs:
        done = {a["tile_id"] for a in run["report"]["actions"] if a["outcome"] == "completed"}
        for tile, tag in run["tags"].items():
            if tag != "nova" or tile not in done:
                continue
            reads = [r for r in run["reads"] if r["tile"] == tile]
            clean = [r for r in reads if r["clean"]]
            if any(1.28 <= r["ratio"] <= 1.70 for r in clean):
                caught += 1
            elif not reads:
                missed["没有读数"] += 1
            elif not clean:
                missed["只有不干净的读数"] += 1
            else:
                missed["干净读数都不在 [1.28, 1.70]"] += 1
    print(f"\n新星：已完成 {caught + sum(missed.values())} 块，报出 {caught}；漏报原因 {missed}")
    unclean = [r["ratio"] for run in runs for r in run["reads"] if r["tag"] == "nova" and not r["clean"]]
    if unclean:
        print(f"  新星的不干净读数比值 P5/P50/P95 = {pct(unclean, 0.05):.3f} / {pct(unclean, 0.5):.3f} / {pct(unclean, 0.95):.3f}")
    spurious = [r["ratio"] for run in runs for r in run["reads"] if r["tag"] != "nova" and not r["clean"] and r["ratio"] >= 1.28]
    print(f"  非新星天区的不干净读数里比值 ≥ 1.28 的有 {len(spurious)} 次（放宽到不干净读数时的错报来源）")

    # 4. 故障：检出前故障分区的读数都去了哪里
    print("\n故障检出前、故障分区内的读数（每场）：")
    for run in runs:
        f = run["fault"]
        if not f or f["detected"] is None:
            continue
        before = [r for r in run["reads"] if r["fault"] and r["t"] < f["detected"]]
        kinds = {"干净且未下沉": sum(r["clean"] and not r["dip"] for r in before), "被下沉过滤": sum(r["clean"] and r["dip"] for r in before),
                 "不干净": sum(not r["clean"] for r in before)}
        delay = (f["detected"] - f["start"]) / 3600
        if delay > 2:
            print(f"  {run['name']:12} 分区 {len(f['regions'])} 个  乘数 {f['multiplier']:.2f}  延迟 {delay:5.1f} h  {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
