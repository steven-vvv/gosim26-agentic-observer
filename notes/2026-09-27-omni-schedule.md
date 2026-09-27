# 全知排程：两个练习场景追平榜首（2026-09-27）

接着 `notes/2026-09-27-public-once.md`：GPT 版本在 dev-reference 上是 20788.54，比榜首低 330.95；
在 dev-fortnight 上是 9872.74，低 9.33。这次用精确模型重做全知排程，两场都**逐位追平练习榜首**：

| 场景 | 本次 | 练习榜首 | GPT 版本 |
| --- | ---: | ---: | ---: |
| dev-reference | **21119.494551** | 21119.494551（Pleiades、AstroNJU） | 20788.544932 |
| dev-fortnight | **9882.067113** | 9882.067113（AstroNJU） | 9872.739437 |

reference 的分项与榜首也完全相同：基础 15469.606253、加成 3853.288298、请求 3920、
空等罚分 2123.40。两份 `decisions.csv` 都用官方 `kit/local_runner.py` 回放，再用
`kit/score_decisions.py` 复核，分数一致。回放包用的排程已写进 `agents/public_once/public_schedules.json`，
其中科学观测的开始格另存在 `agents/public_once/omni/*.npy`。

提交方式：
- **decisions.csv**：Playground 的 `practice` 阶段收结果文件，由 `dist/omni/<场景>-decisions.csv` 重建
  （`tools/omni/export.py` 导出排程后，用官方 runner 回放生成）。
- **完整项目**：打包命令同 `notes/2026-09-27-public-once.md`，用 Python 3.12 回放，decisions.csv 与上面逐字节相同。
  平台逐轮评测按墙钟截断。reference 预算 7200 秒，要 8510 次决策，平台实测约 1.6–2.1 秒一次，**来不及跑完**；
  按 2.08 秒一次算，大约停在第 3460 次，分数为负。fortnight 只要 595 次，来得及。
- 排程 JSON 带 `complete=true`：回放时不再用 `_filler` 临时插入重访（全部重访都已在计划里）。
  罚分相同时，DP 优先选决策更少的路径，reference 由此从 9146 次减到 8510 次。

## 最优性

- **fortnight：已证明全局最优。** `tools/omni/flow.py` 是逐格精确的时间轴流模型 MILP，包含全部候选。
  零 gap 求解 157 秒，对偶界等于 9882.067113。
- **reference：在 Δ=120 邻域内证明最优。** 每块天区的候选限定为价值不低于当前值减 120 的开始格
  （25.1 万个候选，86 万变量），全程模型零 gap，3 线程 12290 秒，峰值内存约 7.6 GB。
  Δ=30 与 Δ=60 的结果相同。
- 两个互相独立的榜首队伍交出完全相同的 reference 分数，也说明这很可能就是全局最优。
- Fable 5.1 审计（`notes/2026-09-27-omni-audit.md`，脚本在 `tools/omni/audit/`）：
  - 没有找到能超过 21119.494551 的合法手段；
  - 不依赖 Δ 的严格上界是 22340.01，由拉格朗日松弛得到，偏松；
  - 不限 Δ 的实测都没有改进：60 个 6 夜窗口的全候选精确 MILP、第 171 夜跨夜成对交换约 20 万次评估；
  - 模型唯一漏掉的官方允许行为是“中途停止”（决策用完即结算，剩余时间不罚）。对全部前缀实测后，
    最优仍是跑完全程，`model.py` 已补上 `stop=True` 语义。

模型只保留三类有利动作，其余动作都严格劣于 wait：
- 无标签的重复观测会被判 `duplicate_tile`，罚 100 并空耗到时隙末；
- 几何或跨夜中断罚 100，而一次观测最多只占 9 格，省下的空等罚分不到 1.35；
- 不安全观测罚 2000。

保留的三类动作是：
- 首次（计分）观测；
- 已完成请求天区的带标签重访，结果为完成或天气中断；
- 未完成天区的注定天气中断观测。天区仍保持未完成，而且这段时间不算等待，所以不计罚。

## GPT 版本差在哪里

1. **没用注定中断和重访填补空闲。** 只重做空闲填补 DP、科学观测不动，空等罚分就从 2493.75 降到 2398.95。
2. **科学观测的时机没有和罚分一起优化。** 真正省下罚分的是这几件事：
   - 把最后几块天区的观测挪到“能完成它们的最后时刻”之前，缩短它们仍可行动、从而拖累罚分的时段；
   - 早期用长时间的请求重访串把夜里填满（例如 RQ0001 的 T00062 连拍一整夜）；
   - 在第 171 夜把 23 块天区紧凑排完。
   榜首的 2123.40 与我们最终的罚分相同。

## 方法与工具（`tools/omni/`）

| 文件 | 作用 |
| --- | --- |
| `tables.py` | 逐天区、逐 150 秒开始格，直接调用 kit 函数算出观测结果（完成/中断/非法）、结束格、基础分、三种项目加成，以及 `_can_complete_from`。reference 约 1 分钟 |
| `model.py` | 按官方规则逐条计分，与官方评分器逐位一致；另负责计划与决策序列的互相转换 |
| `dp.c` / `cdp.py` | 给定科学观测开始格，精确求最小空等罚分（含请求补访），reference 每次评估约 3–7 ms |
| `flow.py` | 小场景用的逐格精确流模型 MILP |
| `lns.py`、`lns_sweep.py` | 滑动窗口 LNS：窗口内逐格精确，窗口外固定，子问题最优则全局不劣 |
| `gmlns.py` | 全程流模型：候选限价值 ≥ 当前值 − Δ，可以只放开部分天区（`free_tiles`）；reference 的最终结果出自这里 |
| `mlns.py`、`rlns.py`、`sa.py`、`scan.py` | 多窗口 LNS、随机子集 LNS、模拟退火、单块/成对穷举（搜索过程用过） |
| `pipeline.py` | 串起结果表、初始解、滑动窗口和全程模型；fortnight 端到端可以复现最优值 |
| `export.py` | 导出 `public_schedules.json` |

reference 的搜索路径：GPT 的 20788.54，经 DP 重填到 20883.34，退火后 21079.8，
169–173 夜窗口 LNS 到 21100.18，滑动窗口扫描到 21117.00，0–16 夜宽窗口到 21118.89，
最后 Δ=30 全程模型（18 分钟）到 21119.494551。
单块或成对穷举、多窗口组合、Δ≤60 的近全局 LNS 都停在 21118.89，只有全程模型能跳出去。
差的那 0.6 分来自同时调整几块天区的科学时机，并用重访串改排空闲。

## 复现

```bash
uv run python tools/omni/tables.py kit/scenarios/dev-reference
uv run python tools/omni/tables.py kit/scenarios/dev-fortnight
uv run python tools/omni/pipeline.py kit/scenarios/dev-fortnight --delta 1000   # 约 4 分钟，得 9882.067113
uv run python tools/omni/gmlns.py kit/scenarios/dev-reference --init agents/public_once/omni/dev-reference_sci.npy \
    --out /tmp/x.npy --delta 30 --full --threads 3                              # 复核 21119.494551 为 Δ=30 最优
uv run python tools/omni/export.py --reference agents/public_once/omni/dev-reference_sci.npy \
    --fortnight agents/public_once/omni/dev-fortnight_sci.npy --out agents/public_once/public_schedules.json
```

依赖 `highspy`（已加入开发依赖；scipy 自带的 HiGHS 没有热启动和线程参数）；
`dp.c` 首次使用时由 `cdp.py` 调 gcc 编译。

## 对正式赛的意义

这些分数都用了天气真值，不能拿来估计未知场景的成绩。能迁回在线策略的只有下面这条，但要先验证：

- 空等罚分与“仍未完成、可完成的天区集合”直接挂钩。重访串和注定中断观测都能把等待换成不计罚的动作。
  正式赛是新版规则：`mechanics` 开启，完成的天区可以重复观测并取最高分，按 `_repeat_score_potential` 判断
  是否仍可行动。所以这里的填补思路要按新版规则重新推导，不能照搬。
