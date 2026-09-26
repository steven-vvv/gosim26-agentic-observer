# Fable 审计：dev-reference 全知排程能否超过 21119.494551（2026-09-27）

Fable 5.1 子代理的审计记录，原样保留；脚本已移到 `tools/omni/audit/`，中间产物（截断 CSV、乘子、日志）留在未入库的 `runs/omni/fable/`。审计时未改仓库文件、未联网，CPU 限 1 核（`taskset -c 3`），内存峰值 < 1 GB。

## 0. 结论

1. **没有找到任何能让 dev-reference 超过 21119.494551 的合法手段。** 官方计分器（旧版分支）对每种合法动作的处理
   与 `tools/omni/model.py` / `dp.c` / `flow.py` 的建模一致；模型唯一没有包含的官方允许行为是"中途停止（不再发决策）"，
   官方评分器对停止后的时间**不判罚**，但对当前解的全部 9147 个前缀实测都不优于全程（截断实测见 §1.3）。
2. 简报第 1–7 条全部成立；第 8 条"提前结束省不掉什么"结论成立但理由需修正（§2）。另发现 `model.py` 与官方两处语义差异，
   都只在"决策序列没有走到调查末尾"或"末尾多出 observe 行"时显现，对当前提交文件无影响（§2.9）。
3. 全局最优性：本机 1 核无法给出零 gap 的严格证明。给出一个**不依赖 Δ 候选剪枝的严格上界 22340.01**
   （拉格朗日松弛，§3.2），并用三类不限 Δ 的精确/穷举实测（前缀截断、跨夜成对交换 106992 次、滑窗全候选 MILP）
   均未找到改进（§3.3）。更好的解如果存在，最可能是"第 171 超级夜内外的多块天区联动重排"，价值损失 60–120 的候选正是
   正在跑的 Δ=120 全程模型的覆盖范围（§3.4）。

## 1. 漏洞审计（`scoring_core.py` 旧版分支、`challenge_workflow.py`、`contracts.py`、`observing_calendar.py`）

### 1.1 每种动作在官方计分器里的结果（`ChallengeScorer.apply_decision`，mechanics=False）

| 动作 | 官方处理 | 相对 wait 的净效果 | 模型 |
| --- | --- | --- | --- |
| wait | 消耗到时隙末，`_consume_wait` 用开始时刻的 `_has_actionable_tile` 判罚 | — | 有 |
| 未来 slot_id | `_consume_until` 逐时隙隐式等待，判罚逻辑同 wait | 等价 | 等价 |
| 过去 slot_id | `stale_decision` 罚 100，游标不动 | 劣 | 排除 ✓ |
| 未知 slot_id / 无效 tile / program | `_invalid`：消耗到时隙末（同样判 avoidable）+ 罚 100 | 严格劣（多罚 100） | 排除 ✓ |
| 开始时天气不可观测 | `unsafe_observation`：同上 + 罚 2000 | 严格劣 | 排除 ✓ |
| 开始时几何不合法 | `outside_tile_window`：同上 + 罚 100 | 严格劣 | 排除 ✓ |
| 已完成天区、无标签 | `duplicate_tile`：同上 + 罚 100 | 严格劣 | 排除 ✓ |
| 标签无效（请求不存在/天区不属于/不在窗口） | `invalid_request_tag`：同上 + 罚 100 | 严格劣 | 排除 ✓ |
| 观测中几何/跨夜/越过调查末尾中断 | 罚 100，已消耗时间不判罚 | 最多免 8 格 = 1.2 分等待罚，净亏 ≥ 98.8 | 排除 ✓ |
| 观测中天气中断 | 无罚，已消耗时间不判罚 | 免费填时间 | 有（注定中断 / 重访） |
| 首次完成 | 计分一次，若带有效标签同时计访问 | — | 有 |
| 已完成天区带有效标签完成 | 不计分，计访问，消耗时间免罚 | 免费填时间 | 有（重访） |
| report_* 行 | decisions.csv 路径：`load_decisions(allow_reports=False)` 抛异常整文件拒绝；协议路径：被丢弃 | 无 | 排除 ✓ |
| 调查结束后的 observe 行 | `_invalid`：slot 为 None，elapsed 0，仍罚 100 | 劣 | model.py 忽略（§2.9） |
| **不再发决策（停止）** | `score_files` 直接 `finalize`，`final_time` = 游标时刻；剩余时间不消耗、不判罚；`deadline > final_time` 的未完成请求不罚；`issued_at > final_time` 的请求不计 | 可能有益 | **模型未包含**，实测无益（§1.3） |

其它核对点：曝光时长只有 {450, 600, 900, 1200, 1350}，都是 150 的倍数（`awk` 统计 10/9/18/15/12 块），
中断只发生在段首即时隙边界，所以游标始终在 150 秒网格上；`tile_anomalies.csv` 不存在，`score_config.json` 无
`coverage_bonus_weight` 与异常小节（`anomaly_mechanics_enabled` 为假）；`end_overrides` 只在 `apply_report` 中修改，
旧版分支永远不会调用；`_tile_has_request_opportunity`（豁免）只依赖场景真值。
`tables.npz` 一致性：`actionable ⇒ outcome==COMPLETED` 无反例（0 格），`COMPLETED & ~actionable` 7100 格
（`_can_complete_from` 多检查段中点，官方观测循环只查段首），两张表分别对应官方的两条代码路径。

### 1.2 两条提交路径

- **decisions.csv（`score_files`）**：可任意指定 slot_id（隐式等待）、可随时停止（文件结束即 finalize）。`load_decisions`
  要求 observe 行 program ∈ {DARK, BRIGHT, BACKUP}，否则整文件抛错。
- **协议逐轮（`ChallengeWorkflow.run`）**：slot_id 固定为当前时隙，无 stale/unknown；`reports` 在旧版场景被静默丢弃；
  agent 返回非法响应（如 action 不是 observe/wait、`decision_sequence` 不匹配）会触发 `agent_error` 终止 → 等价于"停止"，
  无额外罚分。**执行风险**：`global_wallclock_seconds = 7200`，当前解 9146 步（协议路径每步至少消耗一个时隙，
  下限约 7928 步），平均每步只有 0.79 s；超时后 `final_time` 停在当前游标，之后的分数全部丢失。

### 1.3 "停止"实测（官方评分器）

`tools/omni/audit/prefix_eval.py` 按官方语义对当前解的每个前缀计分，并用 `kit/score_decisions.py` 对三个截断文件复核：

| 截断行数 | 官方总分 | final_cursor | 说明 |
| ---: | ---: | --- | --- |
| 9146（全程） | 21119.494551 | 调查末 | 基线 |
| 8860 | 21119.494551 | 2027-02-27T12:57:30Z（最后一块天区完成后） | 剩余 7 夜未判罚，分数与全程相同 |
| 8859 | 20825.513498 | 2027-02-27T12:35:00Z | 少 T00056 的 293.98 |
| 8821 | 20505.321461 | 2027-02-27T02:57:30Z | — |

9147 个前缀的最大值 = 全程值（`best prefix m=8860`）。原因：最后 12 块天区在 2 月 26–27 日集中完成（每块 276–458 分），
而任何更早的停止每夜最多省 36 分等待罚。RQ0015 的豁免为真（T00059 在窗口内 1890 格全部为不安全/不合法，无任何可完成格），
因此请求奖励上界 3920 已经拿满，停止也不能通过"deadline 未到不罚"获益。

## 2. 模型复核（简报第 1–8 条）

1. 成立：`duplicate_tile` 经 `_invalid` 消耗到时隙末并同样判 avoidable，再罚 100。
2. 成立：几何/跨夜中断的观测最多消耗 8 格（第一段 ≥ 150 s，中断在段首），免 ≤ 1.2 分等待罚，罚 100。
3. 成立：曝光全为 150 倍数（实测分布见 §1.1），所有非法动作也只消耗到时隙末。
4. 成立：`_tile_has_request_opportunity` 只读 slots/tiles/weather 真值，旧版分支 `end_overrides` 不变。
5. 成立：`_has_actionable_tile` 中 `if not self.mechanics: continue`。
6. 成立：无 `tile_anomalies.csv`（因子 1.0），`coverage_bonus_weight` 缺省 0（官方报告 `coverage_bonus: 0.0`）。
7. 成立：隐式与显式等待都走 `_consume_wait`，逐时隙判定。
8. **结论成立、理由要改**：官方对停止后的时间确实不判罚（§1.3 实测），所以"提前结束省不掉什么"不是规则使然，
   而是当前解的末尾结构使然（最后一块完成后已无计罚等待、请求已拿满、再早停必损失 ≥ 294 分）。
9. `tools/omni/model.py::Scenario.evaluate` 与官方的两处差异（对当前 decisions.csv 无影响，已实测逐位一致）：
   - 决策用完后 `while k < n_ticks: consume_wait(...)` 把剩余时间按等待判罚，官方不判罚（更悲观）。
   - `if k >= n_ticks: break` 忽略调查结束后的多余决策，官方对多余 observe 行每行罚 100（更乐观）。
   `dp.c`/`gmlns.py` 强制每块天区恰好完成一次（`== 1`），`flow.py` 是 `≤ 1`；放弃天区在不停止的前提下必然更差
   （最低天区价值 > 180，而放弃后它在可行动时段永远触发等待罚），所以不影响结论。

## 3. reference 的全局最优性

### 3.1 已有证据

- 全程流模型 Δ=30（32844 候选）与 Δ=60（69255 候选）都 Optimal（零 gap），最优 21119.494551；
  Δ=60 根 LP 界折算总分 21517.65（gap 1.9%），割平面后 21446.9。
- 聚合模型 LP 松弛（`lp_relax.py`，dense 候选）上界 22153.08。
- `scan.py --loss 30 --pairs`：单块 28319 次 + 成对 64027 次评估无改进（在 21118.89 的旧解上）。

### 3.2 不依赖 Δ 的严格上界：拉格朗日松弛（`tools/omni/audit/lagr_bound.py`）

记 A(k) 为格 k 上 `_can_complete_from` 为真的天区，A'(k) 为同一时隙内 k 之前所有格的交集（等待动作从任意
k0 ≤ k 开始时 A'(k) ⊆ A(k0)），R 为全部"带标签重访 / 注定天气中断"动作区间的并（忽略状态条件，是超集）。
硬格 H = {k ∉ R : A'(k) ≠ ∅}，|H| = 15402（有可行动天区的格共 44648，R 覆盖 29646）。
当前解 14156 个计罚等待格中 13717 个在 H 上，即罚分几乎全部是结构性的。

对任意合法决策序列：总分 ≤ 3920 + Σ_i v_i(k_i) − Pen，且
Pen ≥ 0.15·Σ_{h∈H} Σ_i λ_{h,i}·[e_i > h] − 0.15·|H ∩ 科学观测格|，λ_{h,·} 是 A'(h) 上任意概率分布；
科学观测两两不相交（每格乘子 μ_t ≥ 0）。于是每块天区独立最大化，UB(λ, μ) 对任意乘子都是上界。
其它罚分只会更大（invalid/duplicate 走同样的等待判罚再加 100；几何中断 100 > 免掉的 1.2；unsafe 2000；
flexible 缺额 ≥ 0；REQUIRED 缺失已作为"不完成"选项计 −1000）。请求奖励 ≤ 3920（RQ0015 不可完成）。
"中途停止"= 把硬格与可选开始格截断到 T，脚本对全部 7929 个时隙边界 T 取最大。

结果（4300 次次梯度，Polyak 步长）：**UB = 22340.014（含停止 max_T UB(T) 同为 22340.014）**，gap 1220.5。
这个界弱的原因是 LP 型松弛允许一块天区"一半早完成免罚、一半在超级夜高价值完成"，而多块天区联合时
`p_h ≥ unfinished_i(h)` 只能表达 max 而非 OR。收紧只能靠分支定界，即全候选 MILP（约 92 万二元变量、
每格容量行 550 万非零、完成状态链 100 万行），超出 4 GB / 1 核。

### 3.3 不限 Δ 的实测（全部无改进）

| 实验 | 规模 | 结果 |
| --- | ---: | --- |
| 前缀截断（停止策略） | 9147 个前缀 | 最大 = 全程 21119.494551 |
| 第 171 超级夜跨夜成对交换（`swap171.py`）：夜内 23 块科学观测逐块挪到 168–174 夜任意可完成格（每夜 top 8），同时把 18 块"在 171 夜价值更高"的天区挪入 | 106992 次 cdp 精确评估，84 s | 无改进；最好的交换 −28.12 |
| 同上，跨季节版（`--global-top 40`）：j 的目的地改为全程所有其它夜价值前 40 的不冲突格 | 89160 次 cdp 精确评估，67 s | 无改进；最接近的 −7.64（T00042→第 167 夜、T00044 进 171 夜） |
| 第 2 夜（次优夜，15 块）与第 137 夜（1 月新月）的跨季节版（`--night 2/137 --top 6 --global-top 30`） | 3450 + 2460 次 | 无改进；最接近 −6.71 / −16.76 |
| 滑窗全候选精确 MILP（`lns_sweep.py`，宽 6 夜步 3，窗口内所有可完成格，threads=1，每窗 300 s 上限） | 60 个窗口，总耗时 < 3 min（无窗口触到时限） | 全部 Optimal 无改进，"第 1 轮结束：21119.494551" |

### 3.4 结构分析：更好的解（若存在）在哪里

- 第 171 夜（2027-02-25/26）是"超级夜"：平均 seeing 0.655（其它夜 0.78–0.90），透明度×天光/seeing = 1.42，次优夜 1.21。
  64 块天区里绝大多数的全程最大价值都在这一夜；当前解在 171–172 夜完成 12 块，171 夜 258 格中科学观测 152 格、
  计罚等待 88 格、重访 18 格。等待格上可完成的天区（T00031、T00020、T00029、T00040、T00033）都在同夜稍后以更高的
  高度完成（例如 T00031 06:30 值 421.9 → 07:15 完成 451.0），等待代价 0.15/格远小于价值提升。
- 相对各天区自身最大值的总损失 2147.8 中，最大的 8 项（T00027 220、T00046 176、T00032 156、T00013 139、T00012 130、
  T00022 112、T00014 112、T00030 104）全是请求天区：必须在请求窗口内首次完成才能计访问（重访要求已完成），
  而窗口都在 171 夜之前；放弃任一请求的代价（奖励+罚 330–990）都大于收益。这是结构性约束，不是剪枝遗漏。
- 逐夜计罚等待集中在满月期（第 7–20、35–41、114–125、161–170 夜，每夜 22500–38700 s）。每个时段同时有 25–32 块
  未完成的可行动天区，全部提前完成的价值损失 1429–6135 分，远大于该时段罚分 36–385 分；"整段免罚"不可行。
- 因此 Δ=60 可能漏掉的只剩一类：把某块天区从 171 夜挤到相邻夜（损失 60–120）换另一块天区挤进 171 夜。
  §3.3 的成对交换已穷举 top-8 组合无改进；三块及以上的联动重排正是 Δ=120 全程模型（251392 候选，正在运行）的覆盖范围。
  若 Δ=120 仍返回 21119.494551，配合上述实测，可以把"存在更好解"的可能性压到只剩"价值损失 > 120 的多块联动"，
  而这类移动的收益上界（相邻夜价值差）在数据上看不到来源。

### 3.5 滑窗验证结果

`runs/omni/fable/sweep.log`：`初始 21119.494551` → `第 1 轮结束：21119.494551`。当前解在全部 60 个 6 夜窗口内、
窗口内全候选（不限 Δ）下精确最优；该验证覆盖了 ±3–5 夜内任意多块天区的联动重排，包含 §3.3 的成对交换。

## 4. 复现命令

```bash
cd /home/steven/code/GOSIM26
# 官方评分（全程、截断 8860/8859/8821）
.venv/bin/python kit/score_decisions.py --scenario kit/scenarios/dev-reference --decisions dist/omni/dev-reference-decisions.csv --out runs/omni/fable/ref_official_report.json
.venv/bin/python kit/score_decisions.py --scenario kit/scenarios/dev-reference --decisions runs/omni/fable/trunc_8860.csv --out runs/omni/fable/trunc_8860_report.json
# 前缀评分（官方停止语义）
.venv/bin/python tools/omni/audit/prefix_eval.py kit/scenarios/dev-reference dist/omni/dev-reference-decisions.csv
# 全候选严格上界
.venv/bin/python tools/omni/audit/lagr_bound.py kit/scenarios/dev-reference --iters 300 --save runs/omni/fable/lagr_300.npz
.venv/bin/python tools/omni/audit/lagr_bound.py kit/scenarios/dev-reference --iters 4000 --beta 1.5 --load runs/omni/fable/lagr_300.npz --save runs/omni/fable/lagr_4300.npz
# 第 171 夜跨夜成对交换
.venv/bin/python tools/omni/audit/swap171.py kit/scenarios/dev-reference --night 171 --top 8 --span 3
.venv/bin/python tools/omni/audit/swap171.py kit/scenarios/dev-reference --night 171 --top 8 --global-top 40
# 滑窗全候选精确 MILP
.venv/bin/python tools/omni/lns_sweep.py kit/scenarios/dev-reference --init runs/omni/dev-reference/best.npy --out runs/omni/fable/sweep_best.npy --width 6 --step 3 --passes 1 --time-limit 300
```

## 5. 文件

- `tools/omni/audit/prefix_eval.py`：前缀评分工具（中间产物 prefix_totals.npy 等留在未入库的 runs/omni/fable/）。
- `trunc_{8860,8859,8821}.csv` 与 `*_report.json`：截断文件与官方报告。
- `ref_official_report.json`：当前解的官方报告。
- `lagr_bound.py`、`lagr_300.npz`、`lagr_4300.npz`、`lagr_4300.log`：拉格朗日上界。
- `swap171.py`：跨夜 / 跨季节成对交换实测（`--night`、`--span`、`--global-top`）。
- `sweep.log`、`sweep_best.npy`：滑窗验证。
