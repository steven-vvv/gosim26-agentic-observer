# d 型卡第三段故障：天花板测量（2026-10-08）

## 结论：不值得修

用**官方 runner**（`examples/_local/runner/run_local.py`，与平台逐字节同一引擎）在 trial-d 上量：
在第三段故障开始（2027-09-04T04:15Z）之后立刻强发一次**正确**举报（探针，`GOSIM_V4_FORCE_REPORT_AT`），
与 default 逐项对比：

| 指标 | default | 强发正确举报 | 差值 |
|---|---|---|---|
| total | 32974.841105 | 33122.415065 | **+147.574** |
| components.report_settlement | 200.0 | 300.0 | +100.0 |
| components.sum_best_scores | 32274.843037 | 32322.416053 | +47.573 |
| counts.required_missing | 2 | 2 | 0 |
| counts.targets_observed | 30000 | 30000 | 0 |
| counts.observations | 70637 | 70653 | +16 |
| accounted_wallclock_seconds（计费） | 129.541 | 139.322 | +9.781 |
| account agent wallclock | 224.892 | 206.589 | — |

**科学分+结算 = +147.57 < 150 阈值**，停止投入。

## 为什么不值得修

1. 结算上限只有 +100（200→300，第三次报对的满分）。这是三个故障全报对的上限，
   而 d 卡三次故障的**总**潜力已被前两次吃掉了大半。
2. 抢修带来的科学分只有 +47.57：第三段故障从 09-04 持续到季末（2028-01-01），
   但效率只压到 0.7198，且季末本身可观测窗口与目标覆盖率已接近饱和
   （`targets_observed` 两次都是满值 30000、`required_missing` 都是 2）。
3. 探针只多花 9.8 s 计费，**不是**计费问题，是收益本身太小。

## 顺带查实的两处（都会影响后续结论）

### 1. 加速引擎在 d 卡上与官方引擎不一致（重要）

同一份 de30 包（sha `de30972dd303…`）、同一张 trial-d：

| 运行方式 | report_settlement | instrument.drops | observe_actions |
|---|---|---|---|
| `batch.py --fast`（加速引擎） | **300** | [-0.225, -0.295, -0.281, -0.225] | 4791 |
| 官方 runner / `verify_package` | **200** | [-0.225, -0.302, -0.272, -0.175] | 4938 |

两次的 `observe_actions` 与 drops **不同**，说明不是打分器差异，而是**决策流本身发散了**。
加速引擎的 `_AssignedTargets` 改写 + wallclock 计费口径不同（`run.py` 用 `wallclock=None`，
官方 runner 用卡片预算 900 s 并计费）都会改变 agent 的节奏决策。
**后果：所有 `batch.py` 给出的 d 卡结论都不可信，d 卡结论必须走官方 runner。**

### 2. `generate` 在 published 卡（trial-*）上抛异常

`start_remote_batch` 会对分配到的主机的**所有卡**跑 generate；trial-* 是主办方发布的卡，
没有 `research-manifest.json`，`generate()` 直接 `FileNotFoundError`，
**整批下发失败**（第一次 fd3 下发就是这样挂的）。已在 `fault` 分支修：
destination 存在但没有 manifest → 视为已发布的卡，跳过。

## 复现命令（官方 runner，权威口径）

```
# default 基准（de30 包，官方 runner）
python3 -m v4.evaluation.verify_package --package <pkg>.zip --cards v4/cards/trial-d \
    --python /root/.uvpy/cpython-3.12.14-linux-x86_64-gnu/bin/python3.12 --out <out>

# 天花板探针（须 --inherit-env，否则 run_local.py 不继承 shell 环境）
GOSIM_V4_FORCE_REPORT_AT=2027-09-04T04:15:00 run_local.py --card <trial-d> \
    --agent "python3.12 -u main.py" --agent-cwd <project> --out <out> --quiet --inherit-env
```

`GOSIM_V4_FORCE_REPORT_AT` 是 `fault` 分支上 `policy._forced_report` 的临时注入点，
只在设了该环境变量时生效，发布路径不受影响（**不应进入任何交付包**）。

## 代码侧的事实（供主代理判断）

`InstrumentMonitor.decide()` 的 `strong` 判据要求 `drop < -early_drop(默认 0.5)`，
而 d 型效率范围 `[0.7, 0.8]` → 最深 `log(0.7) = -0.357`，**该分支在 d 卡上恒不成立**，
第三段故障结构性地无法在 `early or (repaired and not free)` 分支被举报——诊断属实。
但在 de30 的实测里，第三段本来也没被漏掉到无法补救：第四次举报（`kind="step"`，
2027-08-01）是**误报**，它把 `free_since_correct` 打到 2、`window_trusted=false`，
第三次故障落进了「无免费额度 + 天窗不可信」的死角。
修复真正要动的是**误报治理**（免费额度被天气浅降烧掉），而不是 `early_drop` 门限。
鉴于天花板只有 +147.6，**不建议**为该卡型改门限。
