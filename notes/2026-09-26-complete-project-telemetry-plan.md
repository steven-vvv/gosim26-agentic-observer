# 完整项目评测与遥测计划（2026-09-26）

这是初版方案的历史快照。当前平台 `OPENAI_*` 是真实模型代理凭据，远程遥测须用独立的 `TELEMETRY_TOKEN`；现行配置见 `notes/2026-09-27-forward-optimizations.md`。

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

当前打包器还会写入 `telemetry.config.json`：其中只有默认 Worker 地址、OpenAI-compatible
传输方式、模型名和队列/追踪设置，不含 token。完整项目不携带 `.venv`、`.env` 或用户目录；
后续 NumPy build 会把 wheel 安装到可写的 `.deps/`，入口在导入策略模块前加载该目录。

本轮 `codex` 修改后的本地重建包（含双架构 NumPy wheel）为 32,035,254 bytes，SHA-256
`c3de5e5add58b88426ca507b30e92d5e39a0a7c1a5cb9eac9196ddd8e134e591`。该包已在本地检查
manifest、配置文件和协议冒烟，但尚未替代上面的线上历史结果；提交前仍需在页面确认精确版本。

首次完整项目平台评测已完成，原始下载归档与分析见
[`notes/2026-09-26-platform-result-analysis.md`](2026-09-26-platform-result-analysis.md)。
完整运行得分为 `7131.371807`，终止原因为 `survey_complete`；另一份 300 秒运行因
`global_wallclock_expired` 得分 `6012.497622`。两次都没有把 NumPy 安装成功。

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
和峰值 RSS，在每次决策记录输入等待、策略计算、响应编码、stdout 刷新、请求/响应大小、
候选数、CPU、墙钟和规划累计时间，在每 100 次决策保留聚合 checkpoint，在正常 EOF 记录
最终计数及遥测队列统计。这些记录只写 stderr；远程事件进入有界异步队列，不改变 JSONL
输出，也不让网络请求阻塞决策。不记录逐快照、候选内容、未来天气、模型 prompt、完整 action
payload、环境变量值、临时凭证或上游模型 key，避免结果体积和隐私风险。

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

当前策略仍然是确定性的，不需要模型来作决定；完整项目包默认带上非机密遥测配置，但只有
平台注入鉴权密钥时才启动发送线程。平台若注入 `OPENAI_BASE_URL`/`OPENAI_API_KEY`，这两个变量
也支持作为遥测传输配置：直连 Worker 时 API key 是遥测写入鉴权密钥，base URL 被规范化到
`/v1/chat/completions`；比赛平台注入的这两个变量则属于平台代理，真正的 Worker 凭据由平台保存的团队模型
提供。每个事件放入标准 JSON user message，模型名默认为 `telemetry-ack`，不再依赖会被严格代理拒绝的
顶层扩展字段。
显式 `TELEMETRY_URL`/`TELEMETRY_TOKEN` 仍优先走批量事件接口，可用于不经过 OpenAI 兼容层的
独立端点。两种传输都使用有界队列、短超时、有限重试和失败丢弃，不改变决策。

Worker 不是上游模型代理，而是遥测接收平台：它校验写入密钥、识别结构化事件、去重保存到
D1，并返回合规的非流式或 SSE 响应，便于平台把它当作 OpenAI-compatible API。每条事件还会
加入服务端可见的网络元数据（Cloudflare 提供时包括来源 IP、转发信息、国家/colo、协议和
TLS 字段）；密钥、提示词和消息正文不会落库。D1 默认保留 30 天。

遥测只用于补充平台原生日志和评分结果，仍需遵守比赛允许出站请求的前提；Workers.dev 在
部分网络环境中不可达时，客户端会快速失败并丢弃，不影响评分进程。首次结果包没有遥测配置
和 `.env`，manifest 的 `environment` 为空，且旧客户端没有记录 enabled/stats，因此目前更
可能是遥测未启动，尚不能据此判断 workers.dev 被阻断；详见平台实测记录。新包会记录脱敏
配置状态和逐步 trace，下一次运行再判断实际网络路径。

## 决策门

目前的结论是：**完整项目通道已完成一次公开基线评测，当前最好结果为 `7131.371807`；
遥测链路尚未被验证。** 下一次先确认 `.env` 或平台环境变量确实注入，并加入脱敏的
enabled/stats 状态后再判断网络阻断；随后才使用剩余额度做运行时或策略实验。
