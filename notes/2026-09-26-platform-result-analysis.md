# 完整项目首次平台评测归档（2026-09-26）

这是当时提交与诊断的历史记录，其中将平台 `OPENAI_*` 用于遥测的操作建议已过时。当前凭据和模型资格口径见 `notes/2026-09-27-forward-optimizations.md` 与 `notes/2026-09-26-platform-submission-checklist.md`。

## 原始材料

平台下载文件保存在本地 `result_temp/`，目录已加入 `.gitignore`，不作为版本输入。
原始文件保持不改写；以下 SHA-256 用于之后核对下载是否被替换：

| 文件（名称中段省略） | 大小 | SHA-256 |
| --- | ---: | --- |
| `...-809cafc0...zip` | 32,036,875 bytes | `01f13d1abac165f076748e1f57cae931a0b43e77ccb1e7b9e1ddac916c896e13` |
| `...-d178255c...zip` | 276,250 bytes | `0d8e7528c9d34f819758b62886c91df8e2a1467494b1ea2c5b18d759433454fc` |
| `...-fec3b891...zip` | 291,017 bytes | `da84e659aff86398377bc9e41f830d904626966b0033756fef1663d79d1ee48f` |
| `execute.log` | 8,473 bytes | `678a5392786d1f123a1b938ba3fb8ea1494071f9017f018abf22aa3e3ddf9ddc` |

大 ZIP 是提交项目及 NumPy wheels，小 ZIP 是平台生成的 `workflow_result.json`/
`decisions.csv` 结果归档。

## 运行与分数

两个结果都使用 `initial-publication-v2`、2026-10-05 至 2026-10-18 的 14 夜、590 时隙、
每时隙 900 秒场景；评分契约为 `challenge-score-v3`。它们是完整项目通道的公开评测结果，
不是隐藏正式赛分布的证据。

| 结果归档 | 终止原因 | 记账墙钟 | 动作数 | 总分 | 缺失 REQUIRED | FLEXIBLE 缺口 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `d178255c...` | `global_wallclock_expired` | 300.0 s | 148 | 6012.497622 | `T00025,T00033,T00034` | `R04: 3` |
| `fec3b891...` | `survey_complete` | 1235.33046912 s | 591 | **7131.371807** | `T00033,T00034` | `R04: 3` |

完整运行的分数组成为：

```text
base_science        7783.591952
program_bonus       1507.779855
request_reward       140.000000
required_miss      -2000.000000
flexible_shortfall  -300.000000
total               7131.371807
```

没有异常标签或故障报告（`fault_correct_reports=0`、`fault_misreports=0`、
`tag_settlements=[]`），也没有请求缺失惩罚。主要可见损失是两个 REQUIRED 天区和 R04 的
三个 FLEXIBLE 配额缺口；`coverage_bonus` 为 0，不能从这次结果估计覆盖奖励。

## 平台运行环境

`execute.log` 的运行摘要显示：

```text
CPython 3.12.14, x86_64, Linux 6.17.0-1022-azure
cpu_count=2, numpy=null, orjson=null, gil_disabled=null, jit_enabled=null
```

平台在构建阶段先执行离线 NumPy 安装，但目标用户目录 `/.local` 是只读的；随后在线安装
同样在写入 `/.local` 时失败。两条错误均是 `Errno 30 Read-only file system`。因此这次运行
走的是纯 Python 规划器，不能用它证明提交包里的 NumPy wheel 无效，也不能把分数变化归因于
NumPy。纯 Python 路径仍在约 1235 秒内完成，日志中的规划累计约 0.9 秒，当前瓶颈更像是
平台决策节奏而不是规划计算。两个结果的平均记账墙钟约为 2.03 秒/动作（300 秒 ÷ 148）
和 2.09 秒/动作（1235.33 秒 ÷ 591）；agent 自身 CPU 在前 500 次决策只有约 3.5 秒，
不能解释这段长尾。

## 超时假设与本次修正

公开 runner 的全局墙钟包含请求序列化、进程等待、agent 处理和响应解析；公开资料没有
单次决策独立的超时。旧包只在每 100 次决策打印一次统计，无法知道约两秒是在读取下一条
请求、策略计算、stdout 刷新还是平台调度中消耗的。因此当前最高概率顺序是：评测服务的
逐轮往返/调度开销，其次是平台管道或日志等待，最后才是规划器本身。

`codex` 分支已作以下针对性修正，先收集证据再改变策略：

1. `main.py` 每次决策记录 `input_wait_ms`、`policy_ms`、`response_ms`、`stdout_ms`、
   请求/响应字节数、候选数、规划计数、CPU 与墙钟；重放也有独立 trace。stdout 仍只含协议
   响应，遥测入队始终非阻塞。
2. 启动记录脱敏的 `telemetry_config`，结束记录 `queued/sent/failed/dropped` 和队列状态，
   下一次可以把“未注入变量”和“网络失败”分开。
3. 完整项目的 NumPy build 步骤改为 `python3 -m pip install --target .deps`，入口优先加载
   `.deps`，避免再写只读的 `/.local`；失败仍回退纯 Python。
4. 完整项目打包默认生成非机密遥测配置。只有平台注入 `OPENAI_API_KEY` 时才启动后台线程，
   `OPENAI_BASE_URL` 可覆盖默认 Worker，`--no-telemetry` 可显式关闭；所有请求仍有界超时、
   有限重试和丢弃策略。

逐步 trace 也会增加 stderr 行数，因此下一次运行应同时观察 `stdout_ms` 与 `input_wait_ms`，
确认诊断输出没有成为新的阻塞源；不要仅凭总墙钟判断 NumPy 或策略改动有效。

## 遥测结论

提交包内的 `observer.project.json` 的 `environment` 是空对象，包内也没有 `.env`。旧版客户端
`TelemetryReporter.from_env()` 只有在以下任一条件满足时才会创建发送线程：

1. `TELEMETRY_URL` 与 `TELEMETRY_TOKEN` 同时存在；或
2. `OPENAI_BASE_URL` 与 `OPENAI_API_KEY` 同时存在。

当前下载材料没有证明平台向该进程注入过这些变量；manifest 的空 `environment` 也没有提供
任何变量。`execute.log` 只包含本地 `observer: telemetry ...` 的 stderr 行，而这部分与
远程发送无关。代码当时没有把 `enabled`、`queued/sent/failed/dropped` 写入日志，所以日志
中缺少“未配置”和“网络失败”的区分。

因此当前最高置信度判断是：**这次旧版遥测大概率没有启动，优先原因是变量或 `.env` 未进入运行进程，
不是已经确认的 workers.dev 网络阻断。** 新包会把配置状态写入日志，但仍需要一次明确注入
变量的运行，才能从 `configured/enabled` 和发送统计判断 Cloudflare 出口是否可达。

平台公开说明的可靠配置路径是随项目上传 `.env`，并只把其中的键注入进程；任何 token 都不
应写入 Git。下一次诊断可使用等价于下面的未跟踪配置（token 用 Worker 的写入密钥）：

```dotenv
OPENAI_BASE_URL=https://gosim26-telemetry.wxlsteven.workers.dev/v1
OPENAI_API_KEY=<TELEMETRY_INGEST_TOKEN>
TELEMETRY_MODEL=telemetry-ack
```

也可以使用显式事件传输：

```dotenv
TELEMETRY_URL=https://gosim26-telemetry.wxlsteven.workers.dev
TELEMETRY_TOKEN=<TELEMETRY_INGEST_TOKEN>
TELEMETRY_TRANSPORT=events
```

两种配置都只授予遥测写入权限，不是模型上游密钥。新版本只记录脱敏配置状态和最终发送计数，
并让最终有界 flush 时间足以完成一次短请求；绝不记录 URL 中的凭证、token 或请求正文。这样
下一次结果可以明确归类为“未配置 / HTTP 拒绝 / DNS 或连接超时 / 成功”。

## 第二次旧包评测：180 夜结果

`result_temp/2/` 是上次改动前的完整项目归档，ZIP SHA-256 为
`14a45e7a42fdf682eb4e86096f3c9776c9c34f081d470a27d4b831ab3e3f6c86`，日志 SHA-256 为
`63424d8c0e68eb0bec352e0d26b0d3a3f23de0477d4097466779534ebdab7b1c`。它的结果字段给出：

```text
termination_reason          global_wallclock_expired
global/accounted wallclock  7200.0 s
committed actions           3463
ignored in-flight response  true
score total                 5178.334816
```

这次没有 agent error、协议解析错误或故障报告。3463 个决策中有 64 个 `observe`、3399 个
`wait`，7 个请求全部完成，`avoidable_wait` 只有 900 秒；最后游标是
`2026-11-28T07:00:00Z`（日历总计 7928 个时隙），所以只推进到完整 180 夜日历的约 44%。
分数的主要损失是 5 个 REQUIRED 缺失（`T00017,T00025,T00033,T00034,T00041`，罚 5000）。
其中 `T00017/T00025/T00033/T00041` 的目录可用日期分别从 12 月 19 日、2 月 1 日、2 月 21
日、2 月 21 日才开始，均晚于本次墙钟截止；`T00034` 虽有较宽的日历窗口，但在截止前没有
进入可观测候选。当前证据不能把这 5 个缺失归因于策略 bug，需跑过相应可见窗口或利用下一版
逐步 trace 才能进一步定性。`flexible_shortfall` 和请求奖励均没有新增损失。

提交间隔的中位数约 1.982 秒、平均约 2.079 秒，分布在整个运行中基本稳定；300 秒旧结果
也是约 2.03 秒/动作，1235 秒完成结果约 2.09 秒/动作。这说明上次超时的直接原因是**固定
的全局墙钟先到，而不是计算卡死**：公开 runner 没有配置单决策超时或人为 sleep，且本次结果
明确标记 `ignored_in_flight_response=true`。按约 2.08 秒/轮估算，完成 7928 个时隙需要约
16500 秒（4.6 小时），不可能在 7200 秒内完成。旧日志中 agent CPU 仅约 14 秒、规划累计约
2.1 秒，剩余时间来自评测进程的逐轮输入/输出、快照和调度往返；旧日志没有更细的阶段计时，
因此不能把其中某一个环节说成已证实的唯一瓶颈。

构建日志还再次确认旧包的 NumPy wheel 被 pip 安装到只读的 `/.local`，运行时为
`numpy:null`。这是旧打包路径的问题，会使规划器退回纯 Python，但不是这次 7200 秒超时的
主因；当前分支的 `.deps` 安装和每决策 trace 正是针对这两个问题的修正。

## 第二版公开测试与遥测路由（`result_temp/3/init`）

第二版公开测试确实执行了参赛进程：归档包含 `decisions.csv`、完整的
`workflow_result.json` 和 137 个已提交动作，终止原因为默认公开测试的
`global_wallclock_expired`（300 秒）。可见日志中有连续的 `[numpy]` 规划记录，说明本次 `.deps`
构建和向量化规划器已生效；日志文件恰好为 32767 bytes，开头被平台截断，因此不能从归档中看到
启动时的 `telemetry_config` 或最终发送统计。

公开测试不是静态结果页：平台会启动项目执行作业，再启动引擎回放。因此只要运行时有可用的
遥测配置，agent 会创建后台线程并尝试发送事件。不过当前平台的实际链路需要区分：执行器把
`OPENAI_BASE_URL`/`OPENAI_API_KEY` 注入为平台自己的 `observer-model` 代理地址和单次运行凭据，
而不是包内 Worker 地址和 Worker 写入密钥。代理的 Chat Completions 校验只允许标准字段，旧客户端
同时发送的 `metadata` 与 `telemetry` 扩展字段会在代理处以 `unsupported_chat_option` 被拒绝，
请求不会到达 Worker。这解释了“公开测试有 agent 日志但 Worker 没有进站”的组合现象，不能归因于
workers.dev 被阻断。

当前分支已将 OpenAI 传输改为只发送 `model`、`messages`、`stream` 三个标准字段，事件完整放在
JSON user message 中；Worker 可从该信封恢复事件。直连 Worker 仍可使用
`OPENAI_BASE_URL`/`OPENAI_API_KEY`，而比赛平台若要把事件转发到 Worker，必须在平台保存的团队模型
中把上游 base URL、模型 `telemetry-ack` 和 Worker 写入密钥配置好。仅依赖平台运行时注入的两个
变量不会绕过平台代理。下一次提交需重建 ZIP 后再以 D1/Workers invocation logs 验证；这次归档本身
只能证明本地 trace 运行，不能证明远程发送成功。

## 后续动作

1. 保留这次结果作为公开通道基线，不能把 `7131.371807` 当作隐藏正式赛的期望分数。
2. 下一次提交前确认 `.env` 或平台环境变量确实附着在项目版本上，并用唯一
   `TELEMETRY_RUN_ID` 便于 D1 查询；不要把密钥加入仓库或 ZIP 审计材料。
3. 先跑一次最小遥测验证，再做策略变体；若仍无事件，收集脱敏状态日志后再判断
   workers.dev 阻断，并考虑备用域名或端点。
4. 单独记录 NumPy 安装失败；先验证 `.deps` 是否出现并比较 `runtime.numpy`，再做带/不带
   wheel 的对照，测量纯 Python 路径的时间代价，不要把这次失败误判为评分器或策略错误。
