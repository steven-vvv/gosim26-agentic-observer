# 实验记录索引

这里保存策略、评测、平台核验和排程实验的结论。文件名以日期开头，保留当时的实验快照和没有采用的方向；可再生的完整运行输出放在被忽略的 `runs/`，平台原始结果放在 `result_temp/`。

## 策略演进

- [`2026-09-23-observer-v1.md`](2026-09-23-observer-v1.md)：确定性基线与第一轮策略。
- [`2026-09-24-obs11.md`](2026-09-24-obs11.md)：精确读数、天气持续性和 obs11/obs12 变化。
- [`2026-09-25-obs13.md`](2026-09-25-obs13.md)：Fable 评估后的策略筛选与 obs13/obs14。
- [`2026-09-25-obs16.md`](2026-09-25-obs16.md)：协议容错和按预报规划。
- [`2026-09-25-obs17.md`](2026-09-25-obs17.md)：寒潮期在线效率修正。
- [`2026-09-26-beta11.md`](2026-09-26-beta11.md)：机会价格系数实验。
- [`2026-09-26-final-selection.md`](2026-09-26-final-selection.md)：策略收束和提交选择。
- [`2026-09-27-forward-optimizations.md`](2026-09-27-forward-optimizations.md)：前向未知场景的优化筛选。

## 性能与排程

- [`2026-09-24-performance.md`](2026-09-24-performance.md)：本地模拟与评测提速。
- [`2026-09-24-planner.md`](2026-09-24-planner.md)：夜间价格规划和离线试验台。
- [`2026-09-26-cpython-runner.md`](2026-09-26-cpython-runner.md)：CPython runner 与向量化路径验证。
- [`2026-09-27-omni-schedule.md`](2026-09-27-omni-schedule.md)：练习场景全知排程和复现方法。
- [`2026-09-27-omni-audit.md`](2026-09-27-omni-audit.md)：评分器、上界和全局最优性审计。
- [`2026-09-27-public-once.md`](2026-09-27-public-once.md)：公开场景一次性排程。

## 平台与提交

- [`2026-09-26-platform-result-analysis.md`](2026-09-26-platform-result-analysis.md)：平台评测结果和遥测核验。
- [`2026-09-26-platform-submission-checklist.md`](2026-09-26-platform-submission-checklist.md)：完整项目提交清单。
- [`2026-09-26-complete-project-telemetry-plan.md`](2026-09-26-complete-project-telemetry-plan.md)：完整项目和遥测方案。
- [`2026-09-26-complete-project-public-config.json`](2026-09-26-complete-project-public-config.json)：公开项目配置快照。

## 会话归档

- [`2026-09-27-claude-e5e6a5dc.md`](2026-09-27-claude-e5e6a5dc.md)：记录外部会话对练习榜和模型资格的核对。

工作区目录和输出边界见 [`../docs/workspace-layout.md`](../docs/workspace-layout.md)。
