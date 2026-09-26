# 完整项目评测与遥测计划（2026-09-26）

## 线上核验

核验时间：2026-09-26 15:44（UTC+08:00）。线上页面为
`https://create.gosim.org/survey26/platform/compete/`，前端和仓库当前提交均为
`60d6ca2f52ef3a14e9de31b9a0a48517d5410373`。

公开 `current_competition` 返回 `practice`，并给出项目阶段
`practice-projects`。公开阶段配置如下：

| 项目 | 当前值 |
| --- | --- |
| 阶段 | Playground · complete projects（练习赛 · 完整项目） |
| 状态 | active，已开始，无结束时间 |
| 项目评测 | enabled |
| 每队每日额度 | 5 个 batch |
| 每个 batch 的场景 | `dev-fortnight`（14 夜）和 `dev-reference`（180 夜） |
| 天气、事件、预报 | 两个场景均公开 |
| 本地会话 | disabled |

因此现在可以使用“提交完整项目”做一次端到端评测，但它是 Playground 的独立项目榜，
不是当前正式 `online` 赛段，也不能当作隐藏正式赛分布的最终成绩。每个 batch 的两个场景
都完成后才产生平均成绩，不能跨 batch 拼接单场最好成绩。

入口支持公开 GitHub 仓库或不超过 50 MiB 的私有 ZIP。项目先经过准备和公开测试，页面展示
源代码指纹、运行 manifest 和适配文件，必须人工确认精确版本后才能评测。完整项目是持久的
JSONL 进程：stdin/stdout 传协议，诊断写 stderr；模型调用可选，确定性项目不需要模型密钥。

依据：

- [线上参赛页](https://create.gosim.org/survey26/platform/compete/)
- [当前完整项目协议与结果说明](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f52ef3a14e9de31b9a0a48517d5410373/web/src/content/docs.competition.en.md)
- [当前项目准备实现](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f52ef3a14e9de31b9a0a48517d5410373/supabase/functions/_shared/observer-prepare.ts)
- [当前项目评测阶段配置](https://github.com/gosimfoundation/hackathon-survey26/blob/60d6ca2f52ef3a14e9de31b9a0a48517d5410373/supabase/migrations/20260926000400_practice_projects.sql)

团队登录状态、队伍归属和已消耗额度不在匿名页面中，必须在登录后的页面确认；本次没有代替
用户创建项目、上传文件、确认版本或发起 batch。

## 当前候选包

使用 `agents/observer` 重建了完整项目包：

```text
dist/observer-observer-project.zip
sha256 0310b399d976cec71981292b0361d5c1eed73f0daff8ae0d8bedd9bc0bb33410
size   32,027,425 bytes (约 30.5 MiB)
```

包内 manifest 使用 `observer-project-v1`、`jsonl-v2`、`python:3.12-slim`，入口为
`python3 -u main.py`。同时包含 CPython 3.12 的 x86_64 和 aarch64 NumPy wheel；没有 wheel
时策略会退回纯 Python 路径，决策逻辑保持不变。包内不含 `.env`、API key 或模型调用配置。

上传前的本地门禁已经通过：

- observer/runner 测试 35 项通过；
- Python 3.12 编译通过；
- 完整包的 `initialize` + `decision_request` 协议冒烟通过；
- 包内代码哈希与 `agents/observer` 当前源文件一致；
- ZIP 完整性检查通过，远低于 50 MiB 上限。

ZIP 和展开目录在 `.gitignore` 中，提交时只记录指纹和来源提交，不把生成物塞进 Git。

## 遥测分层

优先使用平台已有的私有记录，不把额外网络请求放进评分路径。

### 提交前

保存候选包 SHA-256、源分支和提交、manifest、镜像标签、wheel 列表、Python/NumPy 版本，
以及两个公开场景的本地 `decisions.csv`、`score_report.json` 和决策文件哈希。远端结果必须
以原文件保存，再生成分析副本，避免分析工具改写审计材料。

### 每次远端 batch

从页面和下载结果记录：

- batch/run ID、场景、排队/准备/启动/结束时间、终止原因和错误分类；
- 原始分数、校准分数、分数组成、完成数、required/flexible 缺口、请求奖励/惩罚；
- 决策条数、observe/wait 比例、请求条数、等待秒数、实际运行墙钟和剩余预算；
- `decisions.csv`、`score_report.json`、`workflow_result.json`、`agent.log` 和回放 HTML；
- 诊断面板中的 build/program 输出，以及公开测试的结果包。

当前 agent 已把参数摘要、规划次数/耗时和总计写到 stderr；这些会进入 `agent.log`。本版还
在启动时记录脱敏的 Python/实现版本、系统/架构、CPU 数、NumPy/orjson 版本、GIL/JIT 状态
和峰值 RSS，在每 100 次决策记录墙钟、CPU、RSS、规划次数/耗时，在正常 EOF 记录最终计数。
这些记录只写 stderr，不改变 JSONL 输出，也不增加外部网络请求。不记录逐快照、候选列表、
未来天气、模型 prompt、完整 action payload、环境变量值、临时凭证或上游模型 key，避免结果
体积和隐私风险。

### 跨 batch 派生指标

仅在本地分析中计算：远端与本地的分数/动作差、决策序列哈希、墙钟倍率、NumPy 是否成功
安装、规划耗时占比、协议重试/回退次数、两个场景对平均成绩的贡献，以及相同包重复运行的
方差。这些指标用于决定下一次实验，不回写参赛包。

平台源码明确将 build/program 日志、结果下载和回放限制在本队与主办方可见范围，并为结果
保留源修订和文件指纹；因此现有通道已经能收集比自建 webhook 更完整的端到端遥测。

## 五次额度的效用顺序

只有在登录页面确认阶段仍开放、当前队伍没有活动 batch 后，才按下面顺序使用当天额度：

1. **基线**：当前带 NumPy wheel 的包，不配置模型 key。收集完整结果包和诊断。
2. **重复基线**：同一 ZIP、同一 manifest 再跑一次，先估计平台/场景方差和可复现性。
3. **运行时对照**：仅当基线有至少约两倍墙钟余量时，上传不带 wheel 的同源包，测量
   CPython 纯 Python 路径的时间代价；若基线接近预算，跳过此项以免浪费额度。
4. **策略实验**：只选择本地样本外配对已经支持、且与基线只有一个开关差异的候选；如果
   前三次显示远端不稳定，则改为第三次同包重复，不引入策略变量。
5. **确认**：重复当前最佳且已审计的包，作为可复现记录；若前一步是失败或超时，优先
   回到带 NumPy 的基线，而不是提交新的复杂变体。

每一步都记录“包哈希、配置哈希、额度前后、失败原因”；任何构建失败、协议错误、异常
日志或远端分数缺失都会停止后续变体，先修复并重新走公开测试。完整项目准备本身还有独立
的每日准备额度，不能把它和五次评测额度混为一谈。

## 模型接口与遥测服务器决策

当前策略是确定性的，不需要 OpenAI 兼容接口。平台的 `OPENAI_BASE_URL`/
`OPENAI_API_KEY` 是给项目进行模型调用的临时运行凭证，不是通用遥测通道；把遥测伪装成模型
请求会污染调用额度、增加延迟，也无法保证项目容器允许任意出站请求。因此第一轮不配置模型
key，也不启动外部遥测服务器。

只有在平台原生结果不足以回答一个明确问题时，才考虑单独的可选收集器。届时必须满足：

- 只发批量聚合计数、版本/配置哈希和本地 run UUID，不发快照、候选、未来信息、prompt、key
  或完整决策；
- 异步、短超时、失败即丢弃，绝不阻塞决策或改变 action；
- 有显式开关、请求限速、HTTPS、访问控制和短期保留策略；
- 先确认比赛规则允许项目出站到该地址，并用独立的非评分实验验证。

在现有阶段，平台已经提供私有日志、workflow、决策、评分和回放，故不满足“没有其他渠道”
这一前提，不实施该服务器。

## 决策门

目前的结论是：**通道可用，候选包可提交，适合先做一次无模型的基线评测；尚未提交。**
等登录页面确认队伍和额度后，下一步只需人工确认准备结果中的 manifest、源指纹和公开测试，
再发起第 1 个 batch。收到原生结果后先完成上面的遥测归档和本地/远端对照，再决定是否用剩余
额度做运行时或策略实验。
