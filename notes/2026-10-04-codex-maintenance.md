# v4 Codex 维护与交付记录（2026-10-04）

## 同步状态

- 当前工作分支为 `codex`，以 `041c634` 为同步基线；云控修复和交付提交为 `370daa0`，正式榜复评记录提交为 `6787bda`，因此 `codex` 目前领先本地 `main`、`claude` 两个提交。
- 上游 `../hackathon-survey26` 已从 `8cbf8adc4b18d5f059cc77a71210717e91ecc052` 快进到 `d293dea69b78d15fa8549b73ba242af301abe2b4`，与 `v4/evaluation/upstream.lock.json` 一致，工作树干净。
- 本次上游 12 个提交主要涉及平台阶段管理、CLI、示例和运行环境；`challenge/` 与 `starter_kit_v4/` 无差异，v4 协议和计分实现没有直接变化。
- 上游新增的 `--no-model` 仅用于线上/练习项目评测，隐藏决赛不会设置该开关；因此对当前 formal/full 包没有直接影响。实践期默认值、团队变量和 egress 调整属于平台层行为，已纳入同步但无需改动 v4 agent。
- 当前正式 v4 参考配置的光纤契约是 `grid_side=4`、`n_fibers=16`。智能体继续读取运行时 publication 的实际网格，并在 initialize 遥测中记录 `fiber_contract.matches`；历史压力测试里出现的 25、9、100 等数量不属于当前注册 alpha-delta 卡的契约。

## 云控修复

Worker 新增了 profile-only 的公开读取端点 `POST /v1/config/public/resolve`。它只返回已批准的 `release.profile`，不返回保存的完整 patch、密钥或任意私有字段；完整配置读取仍走认证端点。Worker 已部署，版本 ID 为 `c2bd3cc4-5be8-42bf-9bfc-47959d389ccc`。

公网无 token 探针的实际结果为 `source=remote`、`revision=2`、`profile=formal_conservative`，HTTP 200，响应体只含 profile。这证明当前 D1 云端档位能正常下发，且仍是克制档；本地 `config.json` 的 full 只有在云控返回 full 时才会生效。认证模式仍支持 `OBSERVER_CONFIG_TOKEN` / `TELEMETRY_TOKEN`，同 Worker 的 `OPENAI_API_KEY` 仅在主机名和端口完全匹配时复用。

## Full 锁定交付包

当前锁定包为 `v4/dist/observer4-full-locked-d2c64f69fd5e.zip`，SHA-256 为 `d2c64f69fd5e97912adca2e558154ed1da73217d8964b6521b84a7de977c0b0b`。包内 profile 为 `full`，`remote_config.enabled=false` 且 `locked=true`，启动不会请求云端覆盖。旧的 `observer4-f7a2a2cf4bf4.zip` 保留用于溯源，仍是旧认证回退路径的 `formal_conservative` 包，不作为当前 full 候选。

在上游锁定更新到 `d293dea` 后重新执行一张 r3-000，结果仍为 `7767.768094`、`survey_complete`、1085 次决策请求；因此上游快进没有改变 v4 的计分链路。该本地复验的 `telemetry_config=1` 仅表示验证环境未提供遥测凭据，不是云控启动失败。

## Rollout 研究开关

`v4/experiments/rollout-top2.json` 将 `policy.rollout_top=2`、`rollout_weight=0.35` 打开；正式默认配置仍为 0，故默认运行行为不变。实现对两个确定性候选做一次私有状态前瞻，失败会回退到原候选，不递归 rollout。

两张 `family-validation5` 烟雾卡已完成运行。这里的 `baseline` 是上游官方 starter baseline，不是我方当前 full 策略：

| 变体 | 均分 | 策略耗时/卡 | 必观测缺口/卡 | 结果 |
| --- | ---: | ---: | ---: | --- |
| baseline | 4179.5 | 16.9 s | 6.5 | 2/2 完成 |
| rollout-top2 | 7558.9 | 709.7 s | 2.0 | 2/2 完成 |

这些分数只说明 rollout 与官方 starter baseline 的差异，不能用于估算它相对我方现有方案的收益；策略耗时约 42 倍。随后完成了 64 张 `default` full 对照，均正常结束，均分 6915.755、策略耗时均值 170.475 秒/卡、必观测缺口均值 1.875；r3-000 的 7767.768 与 locked-full 包验证一致。对 `default` 的 64 卡 rollout 全量批次因首批卡运行约一小时仍未完成而停止，保留路径为 `v4/results/family-validation5-r2/rollout-vs-default-val5/`；其中 r3-002 的完整同卡结果为 default 6574.400、rollout 6620.692（+46.293），策略耗时 152.2→676.6 秒。

随后完成了 4 张 `default` 对照复用的小样本 rollout，结果如下：

| 卡 | default | rollout-top2 | 差值 | 策略耗时（default→rollout） | 必观测缺口（default→rollout） |
| --- | ---: | ---: | ---: | ---: | ---: |
| r3-000 | 7767.768 | 7795.613 | +27.844 | 179.3→746.1 s | 3→3 |
| r3-001 | 7336.301 | 7304.526 | −31.774 | 169.6→691.8 s | 1→1 |
| r3-002 | 6574.400 | 6717.943 | +143.543 | 152.2→615.2 s | 0→0 |
| r3-003 | 6840.955 | 6866.056 | +25.101 | 153.2→628.3 s | 2→2 |

四卡均正常完成，平均差值约 +41.2 分，但仅 3/4 获益，策略耗时约 4.2 倍；这不足以证明隐藏卡收益，rollout 不并入正式包。完整 default 对照结果保存在 `v4/results/family-validation5-r2/rollout-vs-default-val5/default/`，小样本 rollout 保存在 `v4/results/family-validation5-r2/rollout-sample4/`。误用官方 baseline 启动的 `rollout-val5` 批次已停止，已生成的官方基线结果仅供参考。

## 当前排行榜威胁判断

以下只使用练习榜 2026-10-04 04:05 UTC 快照；全知回放分数不能直接与正式在线成绩比较，也不能据此断言对手使用了全知输入。

| 卡 | 我方在线约分 | AstroNJU | Clarrycy | Pleiades | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| alpha | 7805 | 7864（+59） | 7639（−166） | 7249（−556） | 最高即时威胁；差距在噪声范围内，需看正式 A–D 的真实在线表现 |
| beta | 7237 | 7497（+260） | 7322（+85） | 6518（−719） | 直接竞争最强；请求完成与季初排序是主要风险点 |
| gamma | 7316 | 7354（+38） | 7262（−54） | 6751（−565） | 最接近的卡；任何一次首请求失败或必观测缺口都会改变名次 |
| delta | 7116 | 7390（+274） | 6913（−203） | 6423（−693） | 超难卡的不确定性最高；AstroNJU 的真值调参迹象最强，但隐藏卡泛化尚未证明 |

目前证据更支持“多数队伍仍在公开信息或在线调参范围内”，没有证据证明排行榜已经普遍采用全知优化。对我方最大的现实风险仍是自身运行失败或云控错误落入保守档；云控公开读取和 full-locked 包已分别覆盖这两条路径。

## 正式赛榜单复评（2026-10-05，online）

- 通过公开 `current_competition` 与 `observer_card_board` RPC 刷新正式阶段 `049d6029-343d-4d16-80d5-94b56b350301`；四张卡各返回 43 队，43 队均有完整 A-D 成绩。该快照覆盖到 2026-10-05 01:59 UTC 左右，不能与练习榜的分数直接混用。
- CCB 当前正式榜均分 `29133.849`，总榜第 `22/43`；逐卡为 A `23303.570`（19）、B `36220.768`（25）、C `24557.019`（21）、D `32454.041`（11）。缺失必观测为 `1/0/0/2`，每卡只有 1 次提交。
- 当前前列为 Pleiades `33232.460`（第 1）、豆包 `32104.337`（第 2）、starwing `31846.716`（第 3）、测试队伍 `31310.567`（第 4）、AstroNJU `31299.146`（第 5）、Argelander `31150.197`（第 6）、HippObs `30740.506`（第 7）。CCB 距第 5/3/1 名分别为 `2165.297/2712.866/4098.610` 分。
- 威胁仍主要集中在 B：Pleiades 在四卡均为单卡第 1，且 B 领先 CCB 约 `7328.871`；CCB 的 D 已进入第 11，距 Pleiades 仅约 `661.907`，所以当前不是四卡均匀落后。A、C 属于中游，B 是最需要重新评估的策略差距。
- CCB 正式四次运行的遥测均为 `survey_complete`、无错误；12 次模型请求/卡全部返回 200。四个运行的 `config_source=remote`、`config_reason=accepted`、`config_revision=2`，且 `config_sha256=a7813680…` 与已归档的 `observer4-9984f75cdd83.zip` 解析为 `formal_conservative` 的哈希一致。因此当前榜单差距不能归因于云控请求失败或运行崩溃，更符合克制档策略在 B 卡上的收益损失。
- 这只是在线阶段可见榜单，后续提交和隐藏 E-H 的机器/卡状态仍可能改变名次；不能据此断言对手使用了全知输入。

## 任务队列末尾（追加）

1. 在 Worker 公开端点保持 `formal_conservative` 的前提下，继续采集正式运行的启动来源、revision、profile 和请求计数。
2. 若继续研究 rollout，先将前瞻改为受控采样或缓存候选，之后再做完整 `family-validation5` 64 卡 `default` 对照；当前版本已验证为高耗时且收益不稳定，不合入候选包。
3. **最后执行逐卡排行榜威胁刷新**：按 alpha、beta、gamma、delta 分别对齐最新榜单、请求完成、必观测缺口、科学分/目标和提交时间，结合正式 A–D 的新结果重新判断是否切换 full 或保留克制档。
