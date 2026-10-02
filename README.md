# GOSIM 2026 巡天智能体（Agentic Observer）参赛工作区

比赛自 2026-10-02 起使用 v4 规则（每张任务卡 900 秒、16 根光纤、逐轮下发观测）。代码按规则版本分成平行的
`v3/` 与 `v4/` 两棵树；跨版本的文档、实验记录和遥测 Worker 留在根目录。

## 目录

| 路径 | 内容 |
|---|---|
| `v4/` | 当前参赛实现：智能体、等价加速评测、卡片族生成器、实验规格与测试；见 [`v4/README.md`](v4/README.md) |
| `v3/` | 已停用的 v3 实现、官方入门包副本、场景、实验脚本和平台归档；见 [`v3/README.md`](v3/README.md) |
| `notes/` | 两个版本的实验记录，按主题索引见 [`notes/README.md`](notes/README.md) |
| `docs/` | 平台页面与公告的 Markdown 存档、本机密钥与真实链路测试说明、工作区边界约定 |
| `telemetry-worker/` | 两个版本共用的 Cloudflare 遥测 Worker（源码、迁移与客户端测试） |
| `AD.md` | 比赛宣传文案存档 |

## 版本库

git，默认分支 `main`；Agent 在 `claude` 分支工作，完成一段即快进同步到 `main`。各版本的生成卡片、运行结果和
构建产物（`v4/cards`、`v4/results`、`v4/dist`、`v3/runs`、`v3/results`、`v3/dist` 等）不入库，见 `.gitignore`。
目录边界与归档约定见 [`docs/workspace-layout.md`](docs/workspace-layout.md)。

## 环境

用 uv 管理，开发用 Python 3.14（`.python-version`），平台运行 Python 3.12；依赖见 `pyproject.toml`。

```bash
uv sync                                        # 建立 .venv
uv run pytest -q                               # v4/tests 与 telemetry-worker/client
uv run pytest -q v3/tests                      # v3 测试单独运行（v3/kit 的 challenge 包与 v4 引擎同名）
uv run ruff check v4 v3/agents v3/tools v3/tests
```

v4 的上游锁、评测与打包命令见 `v4/README.md`；v3 的评测、打包和资源说明见 `v3/README.md`。
本机模型和遥测密钥的存放与真实链路测试步骤见 [docs/local-api-testing.md](docs/local-api-testing.md)。
