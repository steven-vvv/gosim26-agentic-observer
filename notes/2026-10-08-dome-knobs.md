# 2026-10-08 穹顶两键（zero_wait_after / wet_night_max_duration）实验归档

结论：**本轮不进包**（main 决定）。归档原因：两条结论有长期价值——「rich 带天然免疫」与「c 计费被 wet 截断吃穿」——将来换卡型可能还要用。所有数字来自同一棵 dome 树的池上配对，逐卡差值 + 2 万次 bootstrap。

## 结论摘要

| 卡型（带） | 臂 | n | Δ均值 | 95% CI | 胜/负 | 计费 | 必观测缺失 |
|---|---|---|---|---|---|---|---|
| b（mid） | z1w1800（scr-b16） | 16 | +91.3 | [+56.2,+124.5] | 14/2 | ×0.85 | 0 |
| a（lean） | z1w1800（scr-ac32） | 16 | +27.3 | [−5.8,+57.6] | 11/5 | ×0.94 | −1 |
| c（very_lean） | z1w1800（scr-ac32） | 16 | +45.0 | [+0.3,+85.3] | 12/4 | ×0.93 | 0 |
| a（lean） | z1w1800（big-ac48v） | 48 | +44.3 | [+21.3,+67.3] | 35/13 | ×0.96 | 0 |
| b（mid） | z1w1800（big-b24v） | 21 | +38.5 | [+16.0,+61.4] | 15/6 | ×0.94 | 0 |
| d（rich） | z1w1800（big-d24v） | 22 | **−79.5** | [−139.0,−12.0] | 6/16 | ×1.02 | −1 |

d 型的负值是硬伤（最差 −374.5）。拆解（big-d24v，配 r2dm2d 的 default）：

| d16 臂 | n | Δ均值 | 95% CI | 胜/负 | 说明 |
|---|---|---|---|---|---|
| 只 zero_wait_after=1 | 10 | +93.0 | [+17.4,+205.1] | 7/3 | 在 d 上是正的 |
| 只 wet_night_max_duration=1800 | 10 | −28.1 | [−101.2,+71.9] | 2/8 | **回归全在这里** |
| lean/mid 生效、rich 写回（dome-lmr） | 16 | **±0.0** | [0,0] | 0/0 | 逐卡比特级 0.0 |

机制：d 每卡有 ~246 h 空闲可观测时间，最优是少数几次长曝光；把雨夜的长曝光截到 1800 s 等于纯损失。a/b/c 时间紧、被关闭时段切碎，短曝光反而提高完成率。所以两个键的作用域天然不同，不是折中。

## c 的计费问题（本次自己发现并纠正的坑）

`very_lean_overrides` 只有 `anchors/anchor_pool/cluster_anchors/refine_top` 四个键，**不含**这两个键，所以 c 会继承 `lean_overrides` 的值。实测（big-ac48v 的 24 张 c 卡，dome-lm）：

- 分值：Δ +39.5 [+6.4,+70.8]，17/7（分数上是赚的）
- **计费：10/24 张 charged ≥850 s，最高 926/900**；同卡 default 是 614–892（7/24 ≥850）
- 终止原因全部 `survey_complete`，但计费余量被吃穿——c 计时最紧（default 最坏 875–892），规则不鼓励靠运行速度换分

写回 `very_lean_overrides: {zero_wait_after: 2, wet_night_max_duration: 0}`（dome-lmc）后：charged 回到 579–701（0/24 张 ≥850，7/24 → 0/24），必观测缺失 +0，终止全 `survey_complete`；Δ 均值 +9.6 [+0.6,+21.0]，20/24 张 `action_sha256` 与 default 逐位相同（其余 4 张的差来自 wallclock 计费项带来的运行间非确定性：同一 default 配置在 2a 与 5a 两次运行上同样得到不同哈希与 ±130 分差，见下）。

## 逐位不变性验算

- dome（新增两键、默认值）与 claude 分支（domebase 根）的 `default`：scr-b16 001、scr-ac32 000/002 三卡 `action_sha256` **完全一致**，total 与 policy_seconds 也一致 → 默认配置动作逐位不变。
- 反例记录（重要）：同一 `default` 配置在 big-ac48v 066/070 上两次独立运行得到**不同**的 `action_sha256`（26714.41 vs 27071.71；26811.84 vs 26951.90），差 130–360 分。原因是 `remaining_wallclock`（fair-clock）进入 `_paced_anchors` → 计划随主机负载变化。所以**在 900 s 计费制下，"同配置两次运行逐位相同"并不成立**，配对结论只能看分布，不能用哈希相等去证明等价（除非在空载同机同窗）。这也是为什么本题的确认集效应（0.1%）容易被此类噪声淹没。

## 现成配置片段（若将来换卡型重用）

`v4/experiments/dome-lmc.json`（a/lean 与 b/mid 生效、c/very_lean 显式写回关闭、d/rich 天然免疫——policy 段保持默认，不写这两个键）:

```json
{"structure": {"lean_overrides": {"zero_wait_after": 1, "wet_night_max_duration": 1800}, "mid_overrides": {"zero_wait_after": 1, "wet_night_max_duration": 1800}, "very_lean_overrides": {"zero_wait_after": 2, "wet_night_max_duration": 0}}}
```

其余变体：`dome-z1`（只 1）、`dome-w1800`（只 1800）、`dome-w900`、`dome-z1w1800`、`dome-lm`（a/b 生效、c 未保护）、`dome-lmr`、`dome-lmc`，以及叠 `required_stretch: 4` 的 `dome-rs4` / `dome-rs4lmr`。

## 代码改动清单（f02ece9，若将来要合）

| 文件 | 行数 | 内容 |
|---|---|---|
| `v4/agent/observer4/policy.py` | +1/−1 | `zero_observes >= 2` → `>= int(settings.get("zero_wait_after", 2))`（默认逐位不变） |
| `v4/agent/observer4/planning.py` | +7 | 湿夜时把 `wet_night_max_duration` 并入既有 `cap`；值为 0 时完全绕过，不引入新的浮点运算 |
| `v4/agent/observer4/config.py` | +15/−1 | 两键进 `OVERRIDE_BOUNDS`（(1,20) 整数 / (0,3600)），加 policy 校验；`OVERRIDE_INTEGERS` 增 `zero_wait_after` |
| `v4/agent/config.json` | +3/−1 | policy 段补默认值 `zero_wait_after: 2`、`wet_night_max_duration: 0` |
| `v4/tests/test_dome.py` | +132 | 30 个用例（范围、拒绝、覆盖、行为） |
| `v4/tests/test_structure.py` | +1/−1 | `OVERRIDE_BOUNDS` 计数 26 → 28（合到 main 后为 34 → 36） |

## 为什么本轮放弃（main 的判断，记录备查）

- b 上 `required_stretch: 4`（已合入 mid）在 big-b24v 是 +47.5，但真实 trial-b 同机同窗是 **−43.5**（效应都只有 0.1%，符号翻转）；b 已饱和（42,911.6 超过全知上界 42,337）。
- 本线的 a +44.3 出在"被证明会系统性高估"的样本上（tw05 +59.3 → 真实 +2.5；c +91.3 → +60.8），而且需要改代码、还带 c 的计费风险 → 风险收益比不划算。

## 资源与产物位置

- 工作树 `/home/steven/code/GOSIM26-dome`，分支 `dome`（未 push）；提交 f02ece9（两键 + 变体 + 测试）、10c52a3（配对报告脚本）、66d8e11（回退 config 压缩）。
- 配对报告工具 `v4/evaluation/dome_report.py`（按卡型出 Δ/CI/胜-负/CPU/必观测缺失/终止，可对照另一根的 `action_sha256`）。
- 结果：`v4/results/remote/{scr-b16,scr-ac32,big-ac48v,big-b24v,big-d24v}-r2/{r2dm1b,r2dm1c,r2dm2a,r2dm2b,r2dm2d,r2dm3a,r2dm3b,r2dm3d,r2dm3e,r2dm4b,r2dm4c,r2dm4d,r2dm4e,r2dm5a}/`。
- 远端临时根 `/root/gosim/GOSIM26-dome` 与 `/root/gosim/GOSIM26-domebase` 收尾时已从六台主机清理干净，无残留进程。
