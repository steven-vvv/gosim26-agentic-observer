# v3 优雅退出与运行时对齐（2026-09-29）

本次只处理当前 v3 Agent 的收尾，v4 解读和迁移计划暂时搁置。策略和模型决策逻辑没有调整。

## 配置与生命周期

`agents/observer/observability.config.json` 新增 `termination`；打包器保留这段非机密配置。

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

重新 `fetch --prune` 后上游 `main` 仍为 `2400cc0`，工作区干净。本地同步了 `kit/challenge/run_challenge.py`、`kit/local_runner.py` 和 `kit/challenge/challenge_workflow.py`，三个文件均与上游 starter-kit 逐字一致。其中工作流还修正了初始化消息：向 Agent 发布实际生效的墙钟预算，而不是场景默认预算。`kit/challenge/scoring_core.py` 与上游正式引擎评分器逐字一致，工作流也与正式引擎一致。

其余源码差异为可选 `sha256-v1` 随机流派生及回放页面模板：前者用于生成场景，不改变从已有 CSV 加载后的评分与状态推进，默认仍使用历史种子算法；后者影响展示。本轮没有整体替换生成器或回放模板。需要新派生方式的场景可使用上游生成器，不能让旧的本地生成器冒充支持新模式。

## 核验记录

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

运行输出保存在被忽略的 `result_temp/evaluations/2026-09-29/v3-finish/`。新正式包为 `dist/observer-observer-project.zip`，32,050,010 字节，SHA-256：

```text
6f6efeb39376b51efeb3573deffdd390f38a9df8cfc20c174f475c03650f5bdc
```

实现提交为 `76b1d1e`；旧包已保留为 `dist/observer-observer-project-d5c4ac35.zip`。仓库没有配置远程，因此本次同步以 `codex` 分支的本地分步提交为准。
