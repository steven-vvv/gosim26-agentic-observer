# GOSIM 2026 巡天智能体（Agentic Observer）参赛工作区

## 目录

| 路径 | 内容 |
|---|---|
| `docs/` | 平台五个说明页面的 Markdown 存档（中英文），见 `docs/README.md` |
| `data/` | 平台公开数据快照：排行榜、决赛场景公开文件、入门包原件，见 `data/README.md` |
| `kit/` | 官方入门包解压副本（只读参考；本地评测用它的 `local_runner.py` 与评分器） |
| `agents/observer/` | 我们的确定性策略（工作副本）：`main.py` 入口，`policy.py` 决策，`planner.py` 对偶价格规划，`geometry.py` 几何与日历，`protocol.py` 原样取自官方 kit |
| `agents/obs*/` | 冻结的实验快照；`params.json` 只覆盖与默认值不同的参数。**当前推荐版本 `obs16`**（与工作副本相同）：obs15 加上规划器计入每周天气预报（46 场 +0.12%，见 `notes/2026-09-25-obs16.md`）；`obs15` 在 obs14 基础上只加了协议容错，决策与 obs14 逐位相同 |
| `dist/` | 可提交的程序包（`pack_agent.py` 生成）；推荐 `observer-obs14.zip`（含 `requirements.txt`：numpy，装不上自动退回纯 Python；正式用前先在 Playground 验证一次安装）；`observer-obs14-stdlib.zip` 是不带依赖的备用包（结果逐位相同，平台上规划慢）；`portfolio/` 是按旧赛制准备的 30 个择优提交包，新赛制下已废弃（每次评测都是新随机题，见 `runs/fable-2026-09-25-report.md`）。正式赛只收完整项目，项目打包待做 |
| `tools/evaluate.py` | 批量评测：多个 agent × 多个场景，结果写入 `results/<agent>/<场景>.json`；默认用加速 runner + PyPy |
| `tools/fast_runner.py` | 本地评测用的加速 runner：给官方 kit 的纯计算加缓存后调用官方 `local_runner.main()`，输出逐字节相同 |
| `tools/diagnose.py` | 把一次运行的观测质量损失拆成 airmass、月光、天气三部分 |
| `tools/oracle.py` | 离线估算最优时机能挽回多少质量 |
| `tools/plan_experiment.py` | 离线试验台：按官方几何逐次模拟决策，比较各种选择规则（决赛规模每个变体约 40 秒） |
| `tools/upper_bound.py` | 完美信息上界（拉格朗日对偶）与全知可行排程 |
| `tools/mc_scenarios.py`、`tools/mc_report.py` | 蒙特卡洛：真实决赛天区目录 + 自己的天气种子，汇总得分分布 |
| `tools/pyspy_summary.py` | 汇总 py-spy 原始采样（按进程列出自身 / 累计耗时） |
| `tools/zero_score.py` | 构造总分约为 0 的结果文件（Playground 上榜用） |
| `tools/read_analysis.py` | 异常读数分析：给 `log_reads` 日志里的每次读数标注真值类别，离线评估标签与故障的上报规则 |
| `tools/build_portfolio.py` | 线上比赛择优提交用的一组程序包（基础版本 + 温和的参数扰动），附 sha256 清单 |
| `tools/summary.py` | 汇总各版本在开发集 / 留出集 / 练习场景上相对基线的提升 |
| `tests/` | 单元测试（与官方估值口径一致、覆盖边际方向、协议往返） |
| `scenarios/` | 本地生成的决赛同规模场景（可重新生成，每个约 20 MB） |
| `results/` | 评测结果（精简 JSON） |
| `runs/` | 临时运行产物（评测脚本会自动删除大文件，可随时清空） |
| `notes/` | 实验记录 |

## 版本库

git，默认分支 `main`。Agent 在 `claude` 分支上工作，每完成一段就快进同步回 `main`（`git fetch . claude:main`，不切换工作区）。
本地 comp-* 场景和临时运行产物不入库（见 `.gitignore`），场景可以用下文的 `make_scenario.py` 命令按种子重新生成。

## 环境

用 uv 管理，开发用 Python 3.14（`.python-version`），分析依赖见 `pyproject.toml`。agent 保持 Python 3.9 语法兼容
（平台用 Python 3.12 运行，`ruff` 按 `target-version = "py39"` 检查）。规划器可选 numpy（程序包里的 `requirements.txt`，
平台在时钟开始前安装）：装得上就走向量化路径，平台 CPython 上规划快 13 倍；装不上（或本地 PyPy）就用纯 Python，
两条路径的结果逐位相同（`tests/test_observer.py` 与整场 decisions.csv 比对）。

```bash
uv sync                                   # 建立 .venv
uv python install pypy@3.11               # 本地评测用的 PyPy（约 150 MB，入口 ~/.local/bin/pypy3.11）
uv run pytest -q                          # 单元测试
uv run ruff check agents tools tests      # 代码检查
```

## 常用命令

```bash
# 生成决赛同规模的本地场景：以公开的决赛配置为模板、换用自己的种子
# （eval-a 从 10-05 开始，eval-b 从 11-01 开始；不要使用决赛配置文件里的 seed 字段）
uv run python kit/make_scenario.py --out scenarios/comp-a-s103 --seed 103 --days 30 \
    --start-date 2026-10-05 --wallclock 3600 --base data/scenarios/eval-a

# 评测（第一个 agent 作为对比基准；已有结果自动复用，--force 强制重跑）
# 默认用 tools/fast_runner.py + pypy3.11；--official 改用官方 runner，--python 指定解释器
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

本机 4 核、15 GB 内存、无 swap。决赛规模单场评测：obs10 约 115 秒、基线约 100 秒（加速 runner + PyPy；
官方 runner + CPython 分别约 640 / 250 秒），详见 `notes/2026-09-24-performance.md`。PyPy 下每个作业约占
1.5 GB 内存，并行度默认 3，`--jobs 4` 也可行。每次运行的完整产物约 4 MB（开回放 HTML 时约 15 MB），
评测脚本只保留精简结果。
