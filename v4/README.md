# v4 实施与实验

本目录独立维护 v4；v3 的代码、实验和产物已于 2026-10-03 迁到平行的 `../v3/`。跨版本文档、比赛通知、实验记录和共用 Worker 留在根目录。v4 不读取 `v3/` 下的任何文件：模型与遥测客户端随包放在 `agent/observer4/llm/`。

| 目录 | 职责 | VCS |
| --- | --- | --- |
| `agent/observer4/` | 参赛实现，按协议、几何、状态、天气推断、调度、可观测性划分 | 跟踪 |
| `agent/` | 入口、默认配置、manifest；不包含评测引擎或场景真值 | 跟踪 |
| `evaluation/` | 上游适配、语义等价加速、场景生成、批量评测、打包与验收 | 跟踪 |
| `experiments/` | 按研究阶段保存数据集规格、策略变体和消融配置 | 跟踪 |
| `tests/` | 协议、数值一致性、状态重建、稳健性与打包测试 | 跟踪 |
| `cards/` | 生成或下载的场景，评测器可读真值，参赛代码不可读 | 忽略 |
| `results/<stage>/<run-id>/` | 原始日志、动作、评分、运行清单和性能数据 | 忽略 |
| `dist/` | 可重新构建的提交包及校验清单 | 忽略 |

实验结论和可审阅的精简统计进入根目录 `notes/`。实验规格、实现提交、上游提交与结果校验值共同标识一次运行；禁止用相同输出路径覆盖不同配置的结果。

## 约束

- `initialize` 和逐次公开快照是策略唯一的比赛输入。场景路径、天气真值、生成种子和上游隐藏文件不进入参赛包。
- 确定性决策提供可靠回退；默认启用的模型建议仅在平台注入端点和密钥后调用，有预算、校验和执行记录。无可用端点时自动使用确定性策略。
- 所有决策计时、反馈更新、重建、故障判断与退出事件使用统一可观测性配置；批量实验默认关闭远程发送。
- 开发使用 uv/CPython；提交固定 Python 3.12。运行时依赖与评测依赖分开管理。
- 上游由 `evaluation/upstream.lock.json` 固定。加速器必须通过官方评分、完整决策和观测记录的等价性检验。

实施计划见 [v4 研究计划](../notes/2026-09-29-v4-plan.md)。

## 本地复现

在仓库根目录执行下列命令。上游源码由 `evaluation/upstream.lock.json` 固定，评测卡和结果分别写入忽略的 `v4/cards/` 与 `v4/results/`；已经存在且规格相同的卡和结果会复用。

```bash
uv sync
uv run python -m v4.evaluation.generate --dataset validation --jobs 4
uv run python -m v4.evaluation.batch --dataset validation --variants baseline default --jobs 4 --tag local
uv run python -m v4.evaluation.analyze --runs v4/results/validation-r3/local --reference baseline --variants default --dataset validation
uv run pytest v4/tests
uv run ruff check v4
```

分布变化集使用 `--dataset distribution-shift`；每次配置或源码变更应使用新的 `--tag`，分析时必须完整配对且保持源码和评测引擎一致。提交包通过 `uv run python -m v4.evaluation.package --wheels <wheel目录>` 构建，需提供项目声明版本的 Python 3.12 NumPy `x86_64` 与 `aarch64` wheel。独立验证结果和失效机理见 [研究记录](../notes/2026-09-30-v4-research.md)。

上游当前登记的练习卡是 `v4-practice-alpha`–`delta`：均为 Paranal、16 根光纤、38 夜、900 秒；目标数分别为 10,000、9,600、9,900、9,400，γ/δ 含数据丢失压力。完整 `config/`、`public/`、`truth/` bundle 从资源页下载；上游仓库只保留 `cards/<slug>/card.json` 事实与 bundle 校验值，不再提供这些卡的生成配置。旧的 `v4/cards/upstream-practice` 大卡结果只作为历史压力测试，不能当作当前练习赛成绩。`build-v4-practice-cards.py` 现在只用于从已有 bundle 提取事实或生成网页，旧的输出目录生成命令已经失效。

限时观测请求从逐轮公开快照的 `active_requests` 读取，`policy.request_weight` 控制其规划权重（0 表示完整关闭）；对照实验使用 `experiments/exp-request-off.json`。

当前默认版（2026-10-03，见 [第三轮记录](../notes/2026-10-03-v4-claude-round3.md)）按主办方卡片族调参：科学收益指数 1.5、请求权重 6、256 个优先级候选、8 个聚类锚点、搜索前中心扩增与自适应曝光时长，故障监测加入程序档效率似然。调参与验证数据集为 `real`（α–δ 与 L1–L4 八张真卡）和 `family-train`/`family-validation`（`evaluation/family.py`）；`experiments/exp-handoff.json` 复现 codex 交接版的搜索与检测。运行时用目标数 × 光纤数估算几何搜索成本，超过 `policy` 外的 `scale.cost_threshold`（当前 800,000）时自动切换为 32 个候选、6 个锚点、2 个聚类锚点、1 个局部精修候选，并从 450 秒起选曝光时长。规模档按输入规模触发，16 光纤的族卡均不会触发；旧 β/δ 的大卡压力测试不能替代新练习卡验收。`experiments/exp-scale-fast.json` 保留了中间版配置用于对照；官方与加速引擎的逐卡结果及适用范围见 [Codex 收尾记录](../notes/2026-10-02-v4-codex-handoff.md)。模型调用的真实执行路径与提交包可审查性分别由 `tests/test_runtime.py`、`tests/test_guidance.py`、`tests/test_agent_review.py` 检查。遥测只接受专用 `TELEMETRY_URL`/`TELEMETRY_TOKEN`，或与遥测主机一致的模型代理；不会把平台模型密钥发送到默认遥测端点。练习榜回放包（`evaluation/package.py --replay`，入口 `replay/main.py`）另行打开 `observability.remote.model_proxy`，需把队伍的模型端点设为遥测 Worker。
