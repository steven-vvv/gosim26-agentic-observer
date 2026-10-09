# wexp（天气相对凸性）进度

读法：`git show wexp:notes/2026-10-08-wexp-weather-convexity.md`。分支 wexp = cand-ov（de30）+ 本次改动。
远端根：/root/gosim/GOSIM26-wexp（gosim、adl2、epyc 三台都已建根并同步，源哈希 0de1700ea1052092）。

## 动机与实现

a/c 卡光纤时间里 13–16% 是"被取代"的浪费，其中约一半是坏天气夜里暗目标的首拍（因子只做了一半左右）后来在好天气重拍。
思路：当前天气水平明显低于本季典型水平时，对"以后还有机会"的目标加大科学价值的凸性，让规划器在坏天气优先做能满的目标
（亮目标、最后机会目标），把暗目标留给好天气。

- 新文件 `v4/agent/observer4/weather_convexity.py`：`m = min(cap, (ref/scale)^lam)`（`scale < ref`，否则 1），
  `exponent' = 1 + (exponent - 1) * m`；`ref` = 本次运行已见 scale 的滚动中位数（window 3000，样本 < 200 时 m = 1）。
  单边：只在坏天气加凸性；`exponent == 1`（今晚之后没有可见时数）的目标保持线性。
- 开关：`policy.weather_convexity`（lam，默认 0.0 = 关，此时动作逐位不变）、`policy.weather_convexity_cap`（默认 2.0）；
  两键同时进 `OVERRIDE_BOUNDS`（lam 0–4，cap 1–5），可放进 structure 各覆盖组。
- 变体：`wc05`（0.5）、`wc1`（1.0）、`wc2`（2.0，cap 3.0）。
- 接线在 `planning.py` 里 scale 算出之后（约 560 行），只加 10 行；planner 上的 `WeatherConvexity` 在 `__init__` 未建，
  首次用到才建（离线单测用 `object.__new__(Planner)` 不会失败）。
- 验证：`pytest v4/tests -q` 334 过；`ruff check v4` 过；新增 `v4/tests/test_weather_convexity.py`（7 个用例覆盖 m 计算、
  中位数参考、窗口、不可用水平、标量/逐目标指数）。
- **默认关闭的逐位一致性**：wexp 的 `default` 在 kind-big-r3-000 / 002 上与 lexp 的 cand-ov `default`
  （源哈希 ad326ac9）`action_sha256` 完全相同（472933716a8c515c / ca32f5720f1192d0），总分也一致（27718.908 / 26731.122）。

## 第 1 阶段筛查（r2wx1：scr-ac32 a8+c8、scr-b16 b8；臂 default/wc05/wc1/wc2）

配对按逐卡差值（跨主机；`/tmp/wexp/paired.py`，读 `<root>/v4/results/remote/<dataset>-r2/<tag>/<variant>/<card>/summary.json`）。

第 1 阶段完整结果（96/96 任务完成：a 8、c 8、b 8 全部配齐；全部 `survey_complete`，无失败、无超时）：

| 臂 | a（8 张） | c（8 张） | b（8 张） | CPU 倍率（a/c/b） |
|---|---|---|---|---|
| wc05 | −12.6 [−66.5,+42.3] 3/8 | **−135.2 [−210.7,−73.2] 0/8** | −74.7 [−178.7,+29.5] 3/8 | ×1.00 / ×0.98 / ×1.05 |
| wc1 | +13.0 [−27.0,+46.0] 6/8 | **−72.6 [−115.1,−17.5] 1/8** | −95.9 [−255.1,+52.3] 2/8 | ×1.00 / ×1.01 / ×1.07 |
| wc2 | −23.5 [−65.9,+28.4] 2/8 | **−83.8 [−129.5,−33.5] 1/8** | **−189.8 [−301.9,−84.5] 0/8** | ×0.95 / ×1.02 / ×1.03 |

- 失分全在 `sum_best_scores`（科学分）：c 卡 −135/−79/−90，b 卡 −56/−77/−159；`required_penalty` 基本不动（≤ ±6）、
  `required_missing` 无实质变化（c 2→1，a 27→27），所以不是挤掉必观测，而是抢了科学目标的时间。
- 没有任何一臂达到"CI 下界 > 0 或均值 ≥ +60"的补卡门槛；a 上 wc1 的 +13.0 是唯一正数，但 CI 跨 0 且幅度远低于门槛。
- 计费/CPU：全部 96 张卡都是 `survey_complete`，无 `wallclock` 终止；`policy_seconds`（智能体回合内 CPU 秒）在
  未被节流的卡上中位约 700–780 s，最大值出现在 adl2 上的节流卡（约 2,350–2,600 s CPU）。
  adl2 的 cgroup 是 32 核而当时机器上有三个 worktree 共 60+ 个进程，我们对它的并发（32）让它成了 0.5 核/进程，
  2 vCPU 等价核时间只有约 830–840 s，逼近 900 s 预算但都没触发终止；这些卡的**同卡两臂仍成对**，只抬高了两臂的 CPU 时间，
  不影响分差。后续派发已把 adl2 从 r2wx1 的 host-cap 置 0（gosim 100 / epyc 24），避免再拿节流机器。

结论：**天气相对凸性调制在 c（很瘦的卡）明确负、在 b（中档）也负、在 a 上不显著**，且 lam 越大越负（b 卡上尤其明显）。
原因：c/b 卡本身光纤小时少（0.356 / 0.843 纤维小时每目标），lean 覆盖已经把 `science_exponent` 提到 1.8、`science_cap_quality` 0.6，
再乘 1.4–3 的 m 等于把部分因子压到几乎不计分，规划器于是转向必观测/亮目标，科学分随之下降；
"把暗目标留给好天气"的期望收益抵不过"当夜多做满几个目标"的直接损失。

**不进入补满 scr 剩余卡与 big-ac48v / big-b24v 确认**；开关默认关闭（lam = 0），对本版无影响。
