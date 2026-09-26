# GOSIM 2026 巡天智能体（Agentic Observer）参赛工作区

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 平台五个说明页面的 Markdown 存档（中英文），见 `docs/README.md` |
| `data/` | 平台公开数据快照：排行榜、决赛场景公开文件、入门包原件，见 `data/README.md` |
| `kit/` | 官方入门包解压副本（只读参考；本地评测用它的 `local_runner.py` 与评分器） |
| `agents/observer/` | 我们的确定性策略（工作副本）：`main.py` 入口，`policy.py` 决策，`planner.py` 对偶价格规划，`geometry.py` 几何与日历，`protocol.py` 原样取自官方 kit |
| `agents/obs*/` | 冻结的实验快照；`params.json` 只覆盖与默认值不同的参数。`obs17` 是最近一次已冻结的策略快照（寒潮期在线效率修正，见 `notes/2026-09-25-obs17.md`）；`obs16` 是 obs15 加上规划器计入每周天气预报（见 `notes/2026-09-25-obs16.md`）。它们不含当前的遥测与逐步追踪代码 |
| `dist/` | 本地生成的提交包、择优组合和 Playground CSV；均不进入版本库，可分别用 `tools/pack_project.py`、`tools/build_portfolio.py`、`tools/zero_score.py` 重建 |
| `tools/evaluate.py` | 批量评测：多个 agent × 多个场景，结果写入 `results/<agent>/<场景>.json`；默认用加速 runner + 当前 CPython |
| `tools/fast_runner.py` | 本地评测用的加速 runner：缓存和 NumPy 批算官方 kit 的纯计算，可选 orjson 协议编码；不修改 kit |
| `tools/robust_batch.py` | CPython 上对 23 场筛选集或 92 场全集做配对策略实验，精简保存报告 |
| `tools/diagnose.py` | 把一次运行的观测质量损失拆成 airmass、月光、天气三部分 |
| `tools/oracle.py` | 离线估算最优时机能挽回多少质量 |
| `tools/plan_experiment.py` | 离线试验台：按官方几何逐次模拟决策，比较各种选择规则（决赛规模每个变体约 40 秒） |
| `tools/upper_bound.py` | 完美信息上界（拉格朗日对偶）与全知可行排程 |
| `tools/mc_scenarios.py`、`tools/mc_report.py` | 蒙特卡洛：真实决赛天区目录 + 自己的天气种子，汇总得分分布 |
| `tools/pyspy_summary.py` | 汇总 py-spy 原始采样（按进程列出自身 / 累计耗时） |
| `tools/trace_summary.py` | 从平台或本地 `agent.log` 汇总输入等待、策略耗时、响应耗时和动作比例（容忍日志截断） |
| `tools/zero_score.py` | 构造总分约为 0 的结果文件（Playground 上榜用） |
| `tools/read_analysis.py` | 异常读数分析：给 `log_reads` 日志里的每次读数标注真值类别，离线评估标签与故障的上报规则 |
| `tools/build_portfolio.py` | 线上比赛择优提交用的一组程序包（基础版本 + 温和的参数扰动），附 sha256 清单 |
| `tools/summary.py` | 汇总各版本在开发集 / 留出集 / 练习场景上相对基线的提升 |
| `tests/` | 单元测试（与官方估值口径一致、覆盖边际方向、协议往返） |
| `scenarios/` | 本地生成的决赛同规模场景（可重新生成，每个约 20 MB） |
| `results/` | 评测结果（精简 JSON） |
| `runs/` | 临时运行产物（评测脚本会自动删除大文件，可随时清空） |
| `result_temp/` | 平台下载的完整项目结果、`agent.log` 与 workflow 归档；本地忽略，见 `notes/2026-09-26-platform-result-analysis.md` |
| `notes/` | 实验记录 |

## 版本库

git，默认分支 `main`。当前实验在 `codex` 分支；前一轮 `claude` 分支的工作已同步到 `main`。
本地 comp-* 场景、`dist/` 构建导出和临时运行产物不入库（见 `.gitignore`），都可以用对应工具重新生成。

## 环境

用 uv 管理，开发用 Python 3.14（`.python-version`），分析依赖见 `pyproject.toml`。agent 保持 Python 3.9 语法兼容
（平台用 Python 3.12 运行，`ruff` 按 `target-version = "py39"` 检查）。规划器可选 numpy：完整项目包把 wheel 安装到
可写的 `.deps/` 后走向量化路径，安装失败（或本地 PyPy）就用纯 Python；两条路径的结果逐位相同
（`tests/test_observer.py` 与整场 decisions.csv 比对）。完整项目打包默认附带不含密钥的遥测配置和逐步性能追踪；只有
平台注入 `OPENAI_API_KEY` 时才联网，网络失败不会阻塞决策。

```bash
uv sync                                   # 建立 .venv
uv run pytest -q tests                    # 单元测试
uv run ruff check agents tools tests      # 代码检查
```

## 常用命令

```bash
# 生成决赛同规模的本地场景：以公开的决赛配置为模板、换用自己的种子
# （eval-a 从 10-05 开始，eval-b 从 11-01 开始；不要使用决赛配置文件里的 seed 字段）
uv run python kit/make_scenario.py --out scenarios/comp-a-s103 --seed 103 --days 30 \
    --start-date 2026-10-05 --wallclock 3600 --base data/scenarios/eval-a

# 评测（第一个 agent 作为对比基准；已有结果自动复用，--force 强制重跑）
# 默认用 tools/fast_runner.py + 当前 CPython；--official 改用官方 runner，--python 指定解释器
uv run python tools/evaluate.py --agent base=kit/agent/minimal_agent.py --agent obs=agents/observer/main.py \
    --scenario scenarios/comp-a-s101 --scenario scenarios/comp-b-s201 --jobs 3

# kit 更新、改 fast_runner 补丁或换解释器之后：再用官方 runner + CPython 跑一遍，逐字节比对
uv run python tools/evaluate.py --agent chk=agents/observer/main.py --scenario scenarios/comp-b-s202 --verify

# 保留一次完整运行做诊断
uv run python tools/evaluate.py --agent diag=agents/observer/main.py --scenario scenarios/comp-a-s101 --keep
uv run python tools/diagnose.py runs/eval/diag/comp-a-s101 scenarios/comp-a-s101

# 用平台同版本的 Python 3.12 验证 agent
uv run python kit/local_runner.py --scenario scenarios/comp-a-s101 --agent agents/observer/main.py \
    --python /usr/bin/python3.12 --out runs/py312 --wallclock 60 --no-replay --quiet
```

## 资源

本机 4 核、15 GB 内存、无 swap。obs17 快照在 comp-a-s101 上用加速 runner + CPython 3.14.7
约 226 秒，详见 `notes/2026-09-26-cpython-runner.md`；完整项目打包使用带遥测追踪的
`agents/observer` 工作副本。并行度默认 3；批量评测只保留精简结果，
配对实验只保留评分报告和日志。磁盘余量应定期用 `df -h` 检查。
