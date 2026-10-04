# 上游同步与 v4 交付整理（2026-10-04）

## 同步状态

`../hackathon-survey26` 的 `main` 已从 `5020c2c9d5cfc0b3442bfa9c8f7de60d48617c05` 快进到
`efcb372c5e30986094ab1bd288b24d1e39274219`，与 `origin/main` 一致，工作区干净。
`v4/evaluation/upstream.lock.json` 已同步到同一提交。

本次新增的四个提交均来自 2026-10-04：

| 提交 | 变化 | 对 v4 的影响 |
| --- | --- | --- |
| `656f955` | fair-clock 正式落地，按 Agent 回合内的标准化 CPU 时间计费，等待和引擎时间不计入，增加 30 分钟实际时间上限 | v4 runner 的 `wallclock` 增加计时字段；当前包在新 runner 上验收通过 |
| `db99c1b` | 公共 runner pool 按 13 个仓库分别记录健康度、冷却和并发额度 | 影响平台调度与隐藏赛容量，不改变参赛协议或评分 |
| `482290a` | fair-clock 运行时在各 runner 组织和公开仓库完成批准 | 说明新计时规则已进入组织方运行时 |
| `efcb372` | 新增标准库 `examples/python-pro`，提供双阶段 LLM 规划、故障检测和 pointing 学习参考实现 | 验证了 `OPENAI_BASE_URL`/`OPENAI_API_KEY` 的模型入口约定；不进入本队提交包 |

参考实现还明确了 Kimi Coding Plan 的一个请求约束：`k3`/`kimi-for-coding` 不接受显式
`temperature`。v4 advisor 已按 Kimi 主机名或模型别名省略该字段，普通 OpenAI 兼容端点仍
保留配置值，并有回归测试覆盖。另将模型动态请求上下文上限收紧到 60,000 bytes，低于
上游 64 KiB 请求上限；响应上限 65,536 bytes 仍低于上游 192 KiB。
包内 README 也已区分 full（每阶段 16、总计 24）和默认 conservative（每阶段 8、总计 12）
的模型调用预算。

## 交付物整理

`v4/dist/` 顶层只保留当前交付包和说明：

- `observer4-f7a2a2cf4bf4.zip`
- SHA-256：`f7a2a2cf4bf45cd1506a8f11612ed74c2ebfa0cf890796d07fe026bbf7f6d087`
- 默认 `formal_conservative`，启动时可由同 Worker 云控切换 `full`

The package documentation now matches the startup credential behavior: the formal-run
`TELEMETRY_TOKEN` (or a local `OBSERVER_CONFIG_TOKEN` override) is preferred, while
`OPENAI_API_KEY` is reused only for a same-host, same-port Worker config endpoint. It
also documents that the planner uses the runner's normalized fair-clock remainder
directly and that upstream reserves `OBSERVER_*` team-variable names.

此前的 observer/replay 包保留在 `v4/dist/archive/2026-10-04/`，不再混在可提交包列表中。

## 核验

- 本仓库 v4：`78 passed`，Ruff 通过。
- 上游 fair-clock 与 v4 starter-kit 测试通过。
- 用锁定后的 `efcb372` runner 验收当前包：`screen-000` 正常 `survey_complete`，188 次决策请求。
- 上游隐藏赛和公共池数据库测试未执行：当前环境没有 `psycopg` 及 Postgres harness，属于环境缺口，不判定为代码失败。

fair-clock 改变的是平台如何计量 Agent 时间；当前策略仍以每次请求公开的 `wallclock.remaining_seconds` 为预算依据，并已通过最新 runner 的协议验收。
