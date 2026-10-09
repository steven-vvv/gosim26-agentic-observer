# 2026-10-08 lexp：a/c 类科学指数×视野（lean 层 vs very_lean 层）

纯配置筛查，代码零改动，只加 `v4/experiments/*.json` 变体。分支 `lexp`（从 `cand-ov` = de30 起）。
远端根 `/root/gosim/GOSIM26-lexp`（gosim/epyc）与 `/root/gosim/GOSIM26-int2`（阶段 2，集成树）。
分析脚本在会话的 `/tmp/lexp/lx_report.py`、`/tmp/lexp/lx2_report.py`（配对 default、按卡分带汇总）。

## 动机

研究笔记 `2026-10-07-research-loss.md` §1 第 5 条：按"被取代概率"拟合出的**事后等效凸性**按季六段，
a 3.1/2.6/2.45/2.35/1.85/1.15、c 2.95/2.6/2.35/2.0/1.5/1.1——季初远高于当时 lean 的 γ=1.8，季末降到 1；
而 a/c 季中剩余可见时数中位 290–460 h，H=120 让指数在季中几乎不衰减。历史只测过"更低 γ"和"更短视野"，
没测过"更高 γ + 更长 H"。

## 阶段 1（scr-ac32，16 a + 16 c，`r2lx1`，只改 lean_overrides）

| 臂 | a 类 Δ [95%CI] W/L | c 类 Δ [95%CI] W/L | CPU |
|---|---|---|---|
| lx22h300 (γ2.2/H300) | −3.3 [−47.2,+38.4] 6/10 | **+32.0 [−2.1,+67.6] 10/6** | ×1.01/×0.97 |
| lx26h400 (γ2.6/H400) | −1.9 [−38.3,+36.4] 8/8 | −5.4 [−44.7,+34.1] 7/9 | ×0.99/×0.97 |
| lx30h600 (γ3.0/H600) | **−44.3 [−71.2,−14.7] 2/14** | **+42.0 [+10.1,+72.6] 13/3** | ×0.99/×1.01 |

**a 与 c 方向相反**：同一个开关在 a 上明确为负、在 c 上明确为正。因为 `lean_overrides` 同时管 a 和 c，
四卡均值口径下 lx22h300 ≈ +7、lx30h600 ≈ −1，都不值得直接进包。价值全在 c 单类。

## 阶段 2（int2 树，big-ac48v 24 张 c 卡，`r2lx3`/`r2lx4`）

`very_lean_overrides` 只对 c 生效（分带：c→very_lean、a→lean、b→mid、d→rich），且合并顺序
`late→rich→lean→very_lean→mid` 让 very_lean 覆盖 lean 的同名键，所以 c 单类可以单独打。
int2 的 default 已含 tw05（`timing_weight 0.5`，lean 层），因此 default→lxc30h600 就是
`tw05 → tw05+lxc30h600` 的增量。

| 臂（同批 24 卡） | Δ vs notw | Δ vs tw05 | 策略秒均 | 计费 均/最大 | 未完成 | ≥850 s |
|---|---|---|---|---|---|---|
| notw（timing_weight 0） | — | −60.8 | 777 | 619/737 | 0 | 0 |
| tw05（＝int2 default） | +60.8 [+32.8,+91.3] 19/5 | — | 792 | 607/688 | 0 | 0 |
| **lxc30h600**（very_lean 3.0/600） | **+99.1 [+60.1,+138.9] 20/4** | **+38.3 [+7.1,+71.6] 18/6** | 842 | 607/727 | 0 | 0 |

- `default(r2lx3)` 与显式 `tw05(r2lx4)` **动作哈希 24/24 逐位相同**（两次跑的源码版本不同也一样），
  证明 default→lxc30h600 就是 tw05→tw05+lxc30h600。
- 叠加是结构性的：tw05 在 `lean_overrides`、新键在 `very_lean_overrides`，键名不同、不互相覆盖。
- 计费更宽裕：变体最大 727 s < 默认最大 753 s，平均还低 18 s（高凸度→曝光更长更少、规划次数更少）；
  策略秒 792→842，距 900 s 有 58 s 余量，全 24 张 `survey_complete`。

## 逐位核对（a/b/d 必须不变）

| 类 | 卡 | action_sha256 |
|---|---|---|
| a（scr-ac32） | 000/004/008/012 | 4/4 全同 |
| b（big-b24v） | 065/069 | 2/2 全同（计费 690→725 s） |
| d（big-d24v） | 067/071 | 2/2 全同（计费 166→162 s） |

## 结论

- 合入方式：`very_lean_overrides += {science_exponent: 3.0, science_horizon_hours: 600.0}`（**c 专用**）。
  **绝不能放 `lean_overrides`**——同方向在 a 上是 −44.3 2/14。
- 合入默认值时要同步 `v4/tests/test_structure.py`：`expected["c"]` 变成 `{**lean, **narrow, **lxc}`，
  `OVERRIDE_BOUNDS` 计数不用动（这两个键本就在白名单里）。不要改成跳过断言。
- 变体文件：`lx22h300/lx26h400/lx30h600`（lean 层，诊断用）、`lxc30h600/lxc22h300`（very_lean 层，候选）。
- 未 push、未上传平台、未改 int2 默认配置。
