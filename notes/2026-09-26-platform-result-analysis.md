# 完整项目首次平台评测归档（2026-09-26）

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
平台决策节奏而不是规划计算。

## 遥测结论

提交包内的 `observer.project.json` 的 `environment` 是空对象，包内也没有 `.env`。客户端
`TelemetryReporter.from_env()` 只有在以下任一条件满足时才会创建发送线程：

1. `TELEMETRY_URL` 与 `TELEMETRY_TOKEN` 同时存在；或
2. `OPENAI_BASE_URL` 与 `OPENAI_API_KEY` 同时存在。

当前下载材料没有证明平台向该进程注入过这些变量；manifest 的空 `environment` 也没有提供
任何变量。`execute.log` 只包含本地 `observer: telemetry ...` 的 stderr 行，而这部分与
远程发送无关。代码当时没有把 `enabled`、`queued/sent/failed/dropped` 写入日志，所以日志
中缺少“未配置”和“网络失败”的区分。

因此当前最高置信度判断是：**这次遥测大概率没有启动，优先原因是变量或 `.env` 未进入运行进程，
不是已经确认的 workers.dev 网络阻断。** 在没有一次明确注入变量的运行前，不能从空的 D1
结果反推出 Cloudflare 出口不可达。

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

两种配置都只授予遥测写入权限，不是模型上游密钥。下一版应在 stderr 记录只含布尔值的配置
状态和最终发送计数，并让最终有界 flush 时间足以完成一次短请求；绝不记录 URL 中的凭证、
token 或请求正文。这样下一次结果可以明确归类为“未配置 / HTTP 拒绝 / DNS 或连接超时 /
成功”。

## 后续动作

1. 保留这次结果作为公开通道基线，不能把 `7131.371807` 当作隐藏正式赛的期望分数。
2. 下一次提交前确认 `.env` 或平台环境变量确实附着在项目版本上，并用唯一
   `TELEMETRY_RUN_ID` 便于 D1 查询；不要把密钥加入仓库或 ZIP 审计材料。
3. 先跑一次最小遥测验证，再做策略变体；若仍无事件，收集脱敏状态日志后再判断
   workers.dev 阻断，并考虑备用域名或端点。
4. 单独记录 NumPy 安装失败；在平台允许的情况下改用可写目标目录或不带 wheel 的对照，
   但不要把这次失败误判为评分器或策略错误。
