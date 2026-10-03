# 实验记录索引

这里保存策略、评测、平台核验和排程实验的结论。文件名以日期开头，保留当时的实验快照和没有采用的方向；可再生的完整运行输出放在被忽略的 `v3/runs/`，平台原始结果放在 `v3/result_temp/`。

## 策略演进

- [`2026-09-29-v4-plan.md`](2026-09-29-v4-plan.md)：版本目录布局、确定性 v4 实施、大规模随机研究及提交验收计划。
- [`2026-10-01-v4-claude-optimization.md`](2026-10-01-v4-claude-optimization.md)：上游 PR #102 新引擎、故障监测、精确水平、地形/扇区后验、视场搜索及多数据集验证。
- [`2026-10-02-v4-codex-handoff.md`](2026-10-02-v4-codex-handoff.md)：Claude 结果复核、最新版上游限时请求适配、公开练习卡消融、Agent 原生提交物验收。
- [`2026-10-03-v4-claude-round3.md`](2026-10-03-v4-claude-round3.md)：主办方卡片族反推与族数据集、程序档故障检测与连锁误报修复、凸科学收益/请求权重/加宽搜索的验证版、节奏控制修正、练习榜回放包与 v3 目录迁移。
- [`2026-10-03-v4-claude-round4.md`](2026-10-03-v4-claude-round4.md)：科学收益时间视野、公平份额节奏控制、平台模型调用失败的两个根因、榜单对手分析，以及零结果等待、必观测奖励、科学指数、请求优先等未采用方向。
- [`2026-09-23-observer-v1.md`](2026-09-23-observer-v1.md)：确定性基线与第一轮策略。
- [`2026-09-24-obs11.md`](2026-09-24-obs11.md)：精确读数、天气持续性和 obs11/obs12 变化。
- [`2026-09-25-obs13.md`](2026-09-25-obs13.md)：Fable 评估后的策略筛选与 obs13/obs14。
- [`2026-09-25-obs16.md`](2026-09-25-obs16.md)：协议容错和按预报规划。
- [`2026-09-25-obs17.md`](2026-09-25-obs17.md)：寒潮期在线效率修正。
- [`2026-09-26-beta11.md`](2026-09-26-beta11.md)：机会价格系数实验。
- [`2026-09-26-final-selection.md`](2026-09-26-final-selection.md)：策略收束和提交选择。
- [`2026-09-27-forward-optimizations.md`](2026-09-27-forward-optimizations.md)：前向未知场景的优化筛选。
- [`2026-09-28-public-transfer-research.md`](2026-09-28-public-transfer-research.md)：公开场景策略迁移研究计划。
- [`2026-09-28-public-transfer-research-report.md`](2026-09-28-public-transfer-research-report.md)：计划执行结果、策略采用与否及排行榜威胁判断。

## 会议与培训

- [`2026-10-02-survey-hackathon-lecture-1.md`](2026-10-02-survey-hackathon-lecture-1.md)：线上讲座第一讲摘要，涵盖巡天基础、曝光权衡、赛题简化和 AI 决策可解释性；未引入新的评分或提交规则。

## 性能与排程

- [`2026-09-24-performance.md`](2026-09-24-performance.md)：本地模拟与评测提速。
- [`2026-09-24-planner.md`](2026-09-24-planner.md)：夜间价格规划和离线试验台。
- [`2026-09-26-cpython-runner.md`](2026-09-26-cpython-runner.md)：CPython runner 与向量化路径验证。
- [`2026-09-27-omni-schedule.md`](archive/2026-09-27-omni-schedule.md)：（已归档）练习场景全知排程和复现方法。
- [`2026-09-27-omni-audit.md`](archive/2026-09-27-omni-audit.md)：（已归档）评分器、上界和全局最优性审计。
- [`2026-09-27-public-once.md`](archive/2026-09-27-public-once.md)：（已归档）公开场景一次性排程。

## 平台与提交

- [`2026-09-29-v3-graceful-shutdown.md`](2026-09-29-v3-graceful-shutdown.md)：可配置优雅退出、v3 运行时源码对齐、遥测收尾及正式包整场核验。
- [`2026-09-29-upstream-v4.md`](2026-09-29-upstream-v4.md)：上游 v4 任务、阶段切换准备、v3 收尾消息及对当前参赛项目的影响。
- [`2026-09-29-observability-agent-native.md`](2026-09-29-observability-agent-native.md)：统一可观测性、两个模型决策环节和真实端点稳定性核验。
- [`2026-09-28-end-to-end-stability.md`](2026-09-28-end-to-end-stability.md)：真实模型、共享 Worker 遥测、故障回退和打包的完整核验。
- [`2026-09-26-platform-result-analysis.md`](2026-09-26-platform-result-analysis.md)：平台评测结果和遥测核验。
- [`2026-09-26-platform-submission-checklist.md`](archive/2026-09-26-platform-submission-checklist.md)：（已归档）v3 完整项目提交清单，已被 v4 流程取代。
- [`2026-09-27-packaging.md`](2026-09-27-packaging.md)：v3 打包方案核查（09-27）。
- [`2026-09-29-v4-experiment-checkpoint.md`](archive/2026-09-29-v4-experiment-checkpoint.md)：（已归档）历史快照，已被 2026-09-30-v4-research.md 取代。
- [`2026-09-26-complete-project-telemetry-plan.md`](2026-09-26-complete-project-telemetry-plan.md)：完整项目和遥测方案。
- [`2026-09-26-complete-project-public-config.json`](2026-09-26-complete-project-public-config.json)：公开项目配置快照。

## 会话归档

- [`2026-09-27-claude-e5e6a5dc.md`](2026-09-27-claude-e5e6a5dc.md)：记录外部会话对练习榜和模型资格的核对。

工作区目录和输出边界见 [`../docs/workspace-layout.md`](../docs/workspace-layout.md)。
