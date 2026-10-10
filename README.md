# GOSIM 2026 巡天智能体（Agentic Observer）参赛项目

[English summary](#english-summary)

本仓库是我们参加 [GOSIM 2026 巡天智能体黑客松](https://create.gosim.org/survey26/)的完整工作区：参赛智能体、语义等价的本地加速评测、卡片族生成器、实验规格、遥测 Worker，以及比赛期间的全部实验记录。比赛自 2026-10-02 起使用 v4 规则（逐轮下发观测快照，每张任务卡 900 标准化 CPU 秒），代码按规则版本分成平行的 `v4/`（最终参赛实现）与 `v3/`（已停用的旧规则实现）两棵树。

## 最终提交

- 平台最终版本 `observer4-C4-697abf1fca70`，提交包 `observer4-full-locked-697abf1fca70.zip`，
  SHA-256 `697abf1fca70a0079b6db04245d6eb4f718bfca3ab0e8b1c6086119fb05de46c`。
- `v4/agent/` 就是该包的源码。下面的命令从本仓库重建出逐字节相同的包（需另备 NumPy 2.5.3 的 cp312 `x86_64` 与 `aarch64` wheel）：

  ```bash
  uv run python -m v4.evaluation.package --locked-full --wheels <wheel目录>
  ```

- 版本选择与消融依据见 [`notes/2026-10-08-de30-ablation.md`](notes/2026-10-08-de30-ablation.md)；隐藏卡 E–H 的运行遥测见 [`notes/2026-10-08-hidden-run-telemetry.md`](notes/2026-10-08-hidden-run-telemetry.md)。

## 智能体概览

`v4/agent/` 实现 `participant-agent-protocol-v4`（`jsonl-v4`）：`main.py` 逐行读取 JSON 消息，对每个 `decision_request` 返回一个动作；只使用 `initialize` 与逐轮公开快照，不读取任何场景真值。

- **确定性核心**（`v4/agent/observer4/`）：指向、光纤分配、曝光时长与程序的联合搜索，含带时间视野的科学收益和按 CPU 预算公平份额的节奏控制（`planning.py`、`valuation.py`、`fibers.py`）；必观测目标的机会估计与观测时机（`opportunity.py`、`required.py`、`timing.py`）；仪器效率跟踪、故障报修与值班日志读取（`instrument.py`、`logbook.py`、`logrules.py`）；地形与扇区事件后验、天气与仪器证据推断（`terrain.py`、`sectors.py`、`quality.py`）；按卡片公开的赛季结构选择参数档（`structure.py`）。
- **大模型环节**：通过平台注入的 `OPENAI_BASE_URL` / `OPENAI_API_KEY` 调用 OpenAI 兼容接口，由模型决定每周预报后的巡天策略、近似并列候选之间的行动选择，并解读值班日志。每次调用都有超时、格式校验和熔断，失败时回退到确定性策略。
- 细节见 [`v4/agent/PACKAGE_README.md`](v4/agent/PACKAGE_README.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `v4/` | 最终参赛实现：智能体、等价加速评测、卡片族生成器、实验规格与测试；见 [`v4/README.md`](v4/README.md) |
| `v3/` | 已停用的 v3 实现、官方入门包副本、场景、实验脚本和平台归档；见 [`v3/README.md`](v3/README.md) |
| `notes/` | 两个版本的实验记录，按主题索引见 [`notes/README.md`](notes/README.md) |
| `docs/` | 平台页面与公告存档、本机密钥与真实链路测试说明、工作区边界约定 |
| `telemetry-worker/` | Cloudflare 遥测 Worker：遥测接收、启动配置和 OpenAI 兼容模型代理（源码、迁移与测试） |

## 环境与测试

用 uv 管理，开发用 Python 3.14（`.python-version`），平台运行 Python 3.12；依赖见 `pyproject.toml`。

```bash
uv sync                                        # 建立 .venv
uv run pytest -q                               # v4/tests 与 telemetry-worker/client
uv run pytest -q v3/tests                      # v3 测试单独运行（v3/kit 的 challenge 包与 v4 引擎同名）
uv run ruff check v4 v3/agents v3/tools v3/tests
```

本地评测使用主办方的评测引擎：把 [gosimfoundation/hackathon-survey26](https://github.com/gosimfoundation/hackathon-survey26) 克隆到仓库旁的 `../hackathon-survey26`，并检出 `v4/evaluation/upstream.lock.json` 固定的提交。生成卡片、批量评测与分析命令见 [`v4/README.md`](v4/README.md)；v3 的评测与打包见 [`v3/README.md`](v3/README.md)。

## 阅读提示

- `notes/` 保留比赛期间的原始记录，包括当时的机器路径、主机别名和时间口径（UTC 与北京时间混用，以各篇的标注为准）。
- `v4/evaluation/pool*.py`、`pool_*.sh` 是我们自建远程计算池的调度工具，主机别名来自本机 SSH 配置，换环境需要自行配置。
- 仓库不含任何密钥。本机凭据放在仓库外，见 [`docs/local-api-testing.md`](docs/local-api-testing.md)。`v4/agent/config.json` 中的 Worker 地址与 `telemetry-worker/wrangler.jsonc` 中的 D1 标识是比赛期间的部署，访问令牌只保存在部署端和平台的加密配置中。自行部署 Worker 时请换成自己的 Cloudflare 账号与 D1 数据库。
- 生成的卡片、评测结果和构建产物（`v4/cards`、`v4/results`、`v4/dist`、`v3/runs` 的输出等）不入库，可用对应工具重建，见 `.gitignore`。

## 许可

- 我们编写的代码与文档采用 [MIT License](LICENSE)。
- 主办方提供的材料按 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) 授权，版权归主办方（清单见 [NOTICE.md](NOTICE.md)），具体包括：`v3/kit/`、`v3/data/`、各处原样复制的 `protocol.py`、由官方生成器产生的场景数据，以及 `docs/` 与 `v3/runs/platform-2026-09-25/` 中的平台页面、规则和公告存档。使用时请引用 GOSIM 2026 Agentic Observer Hackathon（<https://create.gosim.org/survey26/>）。
- 评测引擎不在本仓库中，按上游仓库的许可使用。

## English summary

This repository is our complete workspace for the [GOSIM 2026 Agentic Observer Hackathon](https://create.gosim.org/survey26/). It contains the survey-scheduling agent, a semantics-preserving local accelerator for the official engine, a generator for the organizer's task-card family, experiment specs, a Cloudflare telemetry Worker, and our research notes. The notes are written in Chinese.

- **Final submission**: `observer4-full-locked-697abf1fca70.zip` (SHA-256 `697abf1f…`). `uv run python -m v4.evaluation.package --locked-full --wheels <dir>` rebuilds it byte for byte from `v4/agent/`, given the NumPy 2.5.3 cp312 wheels.
- **Agent** (`v4/agent/`, protocol `participant-agent-protocol-v4`): a deterministic core searches pointing, fibre assignment and exposure time jointly, values science with a time horizon, paces itself against the CPU budget and monitors instrument faults. LLM stages, called through an OpenAI-compatible API, choose the weekly survey strategy, break near-ties between candidate actions and read duty logs. Every call is bounded and falls back to the deterministic policy.
- **Layout**: `v4/` holds the final implementation, `v3/` the retired v3-rule implementation, `notes/` the experiment log, `docs/` the archived platform pages, and `telemetry-worker/` the Worker.
- **License**: our code is MIT. Material provided by the organizers (`v3/kit/`, `v3/data/`, verbatim `protocol.py` copies, generated scenarios and the archived platform pages) is CC BY-NC 4.0. The evaluation engine is fetched from [gosimfoundation/hackathon-survey26](https://github.com/gosimfoundation/hackathon-survey26) and is not included here. See [NOTICE.md](NOTICE.md).
