# v3 优雅退出与运行时对齐（2026-09-29）

本次只处理当前 v3 Agent 的收尾，v4 解读和迁移计划暂时搁置。策略和模型决策逻辑没有调整。

## 配置与生命周期

`v3/agents/observer/observability.config.json` 新增 `termination`；打包器保留这段非机密配置。

| 配置 | 行为 |
| --- | --- |
| `mode=upstream_finish`（正式包默认） | 最后响应正常发送；收到上游 `finish` 后收尾并退出，不回复该消息。 |
| `mode=last_slot` | 保留旧方案，在推断出的末时隙响应前阻塞收尾。 |
| `grace_drain_timeout_seconds=20` | 有有效宽限期时，后台遥测队列最多等待 20 秒；还会扣除最终投递预算及退出余量。 |

环境变量 `OBSERVABILITY_TERMINATION_MODE` 和 `OBSERVABILITY_GRACE_DRAIN_TIMEOUT_SECONDS` 可覆盖以上设置。缺失或无效的模式使用 `upstream_finish`；无有效宽限期时沿用远程记录器的短排空配置。上游正常宽限期为 30 秒，最终投递默认最多 5 秒；短宽限期会进一步减少排空及最终投递等待，避免收尾无限等待网络。

两种模式共用一次性收尾过程：停止接收新的后台事件，等待已排队事件完成有界投递/重试，生成 `RunSummary` 分析报告，再独立投递最终报告，记录 `final_delivery` 和本地 `telemetry_final`。远端拒绝或超时不会改变已有决策，失败和未完成状态保留在统计中。若排空超时，`telemetry_status.thread_alive` / `queue_depth` 会反映未完成情况，不能把退出等同于所有远端数据已经入库。

`upstream_finish` 的最终报告包含最后一次响应的 stdout 耗时和完整响应计数；`last_slot` 的报告生成时最后响应尚未发送，因此 `final_response_pending=true`，保持旧统计口径。最后响应之后收到 `finish` 不会重复生成报告。

stdin 正常 EOF 而未收到 `finish` 时仍生成本地分析与最终投递记录。已推断末时隙则记为 `termination_source=eof_fallback`，否则为 `stdin_eof`。`termination_reason` 仅记录上游提供的原因，本地推断另记为 `inferred_termination_reason`；没有结束消息时不冒充引擎确认的正常结束。EOF 回退无法应对 SIGTERM/SIGKILL；旧运行器应配置 `last_slot`。

## 上游源码对齐

核对克隆仓库 `../hackathon-survey26` 的 `2400cc0`：

- `challenge/run_challenge.py`、`starter_kit/challenge/run_challenge.py` 和 `project_platform/transport.py` 都提供 `finish`；字段为 `termination_reason`、`last_decision_sequence`、`grace_seconds`。
- `starter_kit/local_runner.py` 在 `survey_complete` 或 `global_wallclock_expired` 后调用 `finish`；评分结果此前已确定，随后关闭 stdin 并最多等待 30 秒进程自主退出，再强制关闭。无 stdout 收尾响应的要求，stderr 在等待阶段仍收集。
- `project_platform/trusted_engine.py::run_session` 对这两个正常原因执行同样的结束通知，通知失败不更改已有评分；`project_platform/executor.py` 的旧式会话路径还依赖服务端状态携带终止原因。
- 传输层在等待决策响应的全局截止时间耗尽时可能已经强制关闭进程；异常退出、初始化失败及旧服务端状态缺少终止原因时也不能保证通知。因此这里支持的是明确收到 `finish` 的优雅路径，而非依赖结束消息覆盖所有异常。
- `finish` 不携带最终得分或最终状态快照。Agent 分析报告仍基于已收到的观测、策略和服务记录；正式分数以引擎的 `score_report.json` 为准。

重新 `fetch --prune` 后上游 `main` 仍为 `2400cc0`，工作区干净。本地同步了 `v3/kit/challenge/run_challenge.py`、`v3/kit/local_runner.py` 和 `v3/kit/challenge/challenge_workflow.py`，三个文件均与上游 starter-kit 逐字一致。其中工作流还修正了初始化消息：向 Agent 发布实际生效的墙钟预算，而不是场景默认预算。`v3/kit/challenge/scoring_core.py` 与上游正式引擎评分器逐字一致，工作流也与正式引擎一致。

其余源码差异为可选 `sha256-v1` 随机流派生及回放页面模板：前者用于生成场景，不改变从已有 CSV 加载后的评分与状态推进，默认仍使用历史种子算法；后者影响展示。本轮没有整体替换生成器或回放模板。需要新派生方式的场景可使用上游生成器，不能让旧的本地生成器冒充支持新模式。

## 首次核验记录

| 验证 | 结果 |
| --- | --- |
| `uv run pytest -q` | 87 项通过，覆盖两个收尾模式、两种正常结束原因、EOF 回退、关闭后拒绝排队、零预算不启动投递、遥测 503 和本地运行器衔接。 |
| 上游 `test_graceful_finish` | 6 项在上游通过；同样 6 项在同步后的本地传输层通过，包括忽略消息、崩溃、EOF 自主退出和宽限期后强制终止。 |
| 上游正式 `JsonlTransport` + 新正式包 + 本地 HTTP 接收端 | 优雅模式约 0.071 秒退出，旧模式约 0.065 秒退出；两者最终报告均成功投递且仅生成一次。 |
| 短宽限期 + 不返回结果的接收端 | `global_wallclock_expired` / 1.5 秒宽限期下约 0.567 秒退出，保留本地报告和投递失败状态，没有等待网络直至被引擎杀死。 |
| 最新上游运行器 + 正式包 + `comp-a-s101` | 完整 30 夜 `decision-snapshot-v3`，`survey_complete`；详见下文。 |
| 源码/正式包 + 最新上游运行器；正式包 + 本地运行器 | `demo-week` 均完成 299 次决策，三份 `decisions.csv` 逐字一致，得分 6773.563991，正常收到 `finish` 并收尾。该场景是旧机制，只验证传输与打包一致性，不作为 v3 策略评测。 |
| ruff / `git diff --check` / 上游 ZIP 和 manifest 校验 | 通过；ZIP 14 个文件，`jsonl-v2`，同时携带 cp312 的 x86_64/aarch64 NumPy wheel。 |

完整 v3 运行使用新正式包，并由上游 `starter_kit/local_runner.py` 执行：

- 引擎记录 1382 个已提交决策、1397 个动作（动作数包含引擎推进产生的等待等），没有忽略在途响应；Agent 的 1382 次响应与决策一一对应，序号 1..1382 无缺口。
- 总分 183287.506204，完成 1171 块天区，REQUIRED 缺失和 FLEXIBLE 短缺均为 0，所有惩罚为 0；场景墙钟耗时 376.271 秒，运行器总耗时 377.330 秒。
- 本地记录 2520 条事件，无解析或未知消息错误；`finish_received` 与 `final` 各一次。最终来源 `upstream_finish`，实际原因 `survey_complete`，`final_response_pending=false`，统计包含最后一次响应。
- 这轮完整场景未注入模型/远程凭据，模型调用 0 次、远程投递未启用；不能把 `remote_delivered=false` 当作网络故障，也不能声称本轮重新验证了真实 Worker 或 LLM。远程收尾顺序及失败路径由上表中的本地 HTTP 测试验证；此前真实服务证据见 `2026-09-29-observability-agent-native.md`。
- 规划仍根据运行时墙钟预算调整，且本轮没有模型网络等待，分数不与此前真实模型运行作算法优劣比较。

运行输出保存在被忽略的 `v3/result_temp/evaluations/2026-09-29/v3-finish/`。上述首次核验使用的正式包为 32,050,010 字节，现已保留为 `dist/observer-observer-project-6f6efeb3.zip`，SHA-256：

```text
6f6efeb39376b51efeb3573deffdd390f38a9df8cfc20c174f475c03650f5bdc
```

首次实现提交为 `76b1d1e`；更早的包已保留为 `dist/observer-observer-project-d5c4ac35.zip`。仓库没有配置远程，因此同步以 `codex` 分支的本地分步提交为准。

## 补充核对：上游 v3 正式引擎

再次同步并检查克隆仓库，`HEAD` 与 `origin/main` 均为 `2400cc0dea1319bed61b7dc28fefbafd37653e0e`，克隆工作区干净。仓库确实保留了 v3 正式评分引擎与平台运行时，不只是 starter-kit 示例：

| 组件 | 核对结果 |
| --- | --- |
| `v3/kit/challenge/scoring_core.py` | 与上游正式 `challenge/scoring_core.py` 逐字一致。 |
| `v3/kit/challenge/challenge_workflow.py` | 与上游正式工作流逐字一致，包括初始化时发布实际预算、截止时忽略在途响应、只提交合法响应。 |
| `v3/kit/challenge/run_challenge.py`、`v3/kit/local_runner.py` | 分别与上游正式传输实现和 starter-kit 运行器逐字一致。 |
| `project_platform/trusted_engine.py`、`project_platform/transport.py` | 直接使用上游源码测试：v3 正常结束在评分确定后发送 `finish`，不要求回复；错误路径仍不保证通知。 |
| 两份公共 `protocol.py` 与 starter-kit 示例入口 | 原有解析器拒绝 `finish`，示例入口因此会异常退出；现与上游逐字对齐，接受通知后记录摘要并自主退出。Observer 主入口原本已支持该消息。 |

协议补齐提交为 `60c271a`。新增回归测试覆盖两种正常结束原因、两份公共解析器，以及示例 Agent 收到结束通知后不响应、不再消费后续决策。配置仍保留 `upstream_finish` 与 `last_slot` 两种模式，默认前者。

本轮没有改策略、重新生成已有场景或引入 v4。剩余五个 Python 文件的差异均属于上游可选 `sha256-v1` 场景生成方式，另有回放 HTML 展示差异；从已有 CSV 加载的状态推进及评分实现一致。新方式的场景生成应使用上游生成器。

### 补充验证

- 本地全套测试：93 项通过；lint 和 `git diff --check` 通过。新增测试补齐 lint 设置后，相关 6 项再次通过。
- 上游选定的 v3 协议、评分、工作流、优雅退出及平台运行时测试：40 项通过，1 项因未配置容器镜像而跳过。这里没有验证真实 Docker 隔离运行。
- 使用上游 ZIP 校验器与 manifest 解析器验证新正式包：14 个文件，`jsonl-v2`，仍携带 cp312 的 x86_64/aarch64 NumPy wheel；在新的结果目录解包，未覆盖旧结果。
- 新包分别由上游正式 `run_session` / `ColocatedProvider` / `JsonlTransport` 和同步后的本地运行器执行完整 `finals-preview`（7 夜、`decision-snapshot-v3`），预算均为 900 秒。平台会话 API 由本地测试对象提供启动记账，其余引擎与传输使用上游实际源码，没有连接线上比赛服务。
- 两条路径均为 `survey_complete`，303 个已提交决策、307 个动作，总分 8314.653233。两份 `decisions.csv` 逐字一致，两份工作流内完整 `score_report` 结构一致；本地运行器单独生成的回放评分报告额外带输入文件摘要，评分内容一致。
- 两条路径均完成 50 块天区，REQUIRED 缺失 1 块、FLEXIBLE 短缺 5 块。这是当前策略在该预览场景的结果，本轮仅验证运行时一致性。
- 两份日志均有 587 条遥测事件，`finish_received` / `final` 各一次，协议和策略错误均为 0；最终统计包含全部 303 次响应，`final_response_pending=false`。上游正式传输层发出通知后约 0.065 秒自主退出，退出码 0。
- 此次模型调用为 0，未连接真实 LLM 或 Worker；远程服务的既有测试证据仍见首次核验记录及可观测性报告。

补充输出与核验脚本位于被忽略的 `v3/result_temp/evaluations/2026-09-29/v3-finish/source-alignment/`，`audit.json` 保存包、运行和退出摘要。动作 CSV 的 SHA-256 为 `70723929dd59f641dd39953690f0cf0f73f4800851eea54e8786dd6363c24c7c`。

当前正式包为 `dist/observer-observer-project.zip`，32,050,089 字节，SHA-256：

```text
38b6c8d69b87c56f8a31a78cc5a7e05fb75eb36f19734750bcaa4f95eea3bf52
```

### 再次复核

按组织方源码重新执行 `git fetch --prune origin` 后，上游 `main` 仍为 `2400cc0`，克隆工作区干净。再次逐文件核对，本地 `v3/kit/challenge/{scoring_core,challenge_workflow,run_challenge}.py` 与上游正式引擎及 starter-kit 对应文件 SHA-256 一致；`v3/kit/local_runner.py` 和 `v3/kit/agent/protocol.py` 也分别与上游 starter-kit 一致。上游 `test_graceful_finish.py` 和 `test_participant_protocol.py` 共 11 项通过，本地全套 98 项通过。没有发现需要同步的 v3 引擎或运行时差异，因此此次复核不改 v3 实现或正式包。
