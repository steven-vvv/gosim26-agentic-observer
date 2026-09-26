# 完整项目提交清单（2026-09-26）

这份清单只针对当前主办方公开的 `practice-projects` 完整项目通道。它把“平台声明支持”和“本地已经验证”分开，提交新包前逐项勾选；不把凭证放进仓库或 ZIP。

## 通道与项目格式

- [x] 当前阶段允许提交完整项目，完整项目使用持久的 JSONL stdin/stdout 进程。
- [x] 使用根目录 `observer.project.json`，`schema_version=observer-project-v1`、`protocol=jsonl-v2`、`image`、`working_directory`、`build`、`run` 字段。
- [x] `run` 使用 `python3 -u main.py`；stdout 只输出协议响应，诊断和逐步追踪写 stderr。
- [x] ZIP 不超过 50 MiB；当前带 x86_64/aarch64 wheel 的历史包约 30.5 MiB。
- [ ] 每次上传前在登录后的页面确认项目版本指纹、准备测试结果、额度和场景仍未变化。匿名公开页面不能证明队伍状态。

## Python、依赖与 `.venv`

- [x] 提交包不带 `.venv`；标准打包器也会排除 `.venv`/`venv`/缓存。平台不会把本机虚拟环境当作可移植运行时。
- [x] 完整项目依赖通过 manifest 的 `build` 步骤或包内代码处理，不依赖本机路径；平台实测运行时为 CPython 3.12.14 x86_64。
- [x] NumPy wheel 放在 `wheels/`，构建步骤安装到包内可写目录 `.deps/`，入口启动前将 `.deps` 加入 `sys.path`。
- [x] NumPy 安装失败仍可回退纯 Python，策略结果保持逐位一致；安装失败不能中止协议进程。
- [ ] 下一次平台结果必须检查 `runtime` 日志的 `numpy` 字段，确认 `.deps` 是否可写且 wheel 是否被选中；本次旧包写入只读 `/.local`，未完成安装。
- [ ] 不启用 Python 3.13 JIT、自由线程或 PyO3；除非实测显示收益且不改变结果，再单独做实验包。

## 遥测与逐步追踪

- [x] 默认遥测打包源固定为当前工作副本 `agents/observer`；冻结的 `agents/obs17` 没有客户端，误用时打包器会失败，只有显式 `--no-telemetry` 才允许打历史快照。
- [x] 后续完整项目打包默认生成不含密钥的 `telemetry.config.json`，默认指向 Worker 的 OpenAI-compatible `/v1` 地址，逐步追踪开关为真。
- [x] 平台或直连环境注入 `OPENAI_API_KEY` 后才启动后台遥测线程；直连时 `OPENAI_BASE_URL` 覆盖包内地址，比赛平台的这两个变量指向平台代理，需通过保存的团队模型转发到 Worker。没有密钥时完全离线，显式 `--no-telemetry` 可关闭。
- [x] 显式 `TELEMETRY_URL`/`TELEMETRY_TOKEN` 仍可走 `/v1/telemetry`；显式事件端点默认使用 events 传输。
- [x] 每次决策在 stderr 和有界遥测队列记录输入等待、策略耗时、响应编码、stdout 刷新、请求/响应大小、候选数、规划计数、CPU 与墙钟；不记录 tile 内容、快照、prompt 或密钥。
- [x] 启动和结束记录遥测配置状态、队列深度及 `queued/sent/failed/dropped`，状态会剥离 token、查询串和用户信息。
- [ ] 下一次远端运行必须确认 `telemetry_config` 是 `enabled=true` 还是 `configured=false`，并确认平台保存的团队模型已指向 Worker，再用 D1/Worker 日志判断 DNS、连接超时、HTTP 拒绝或成功；不能从空结果直接断言 workers.dev 被阻断。
- [x] OpenAI 传输只发送平台代理允许的标准字段，事件放在 JSON user message；不再发送会被 `observer-model` 拒绝的 `metadata`/`telemetry` 顶层扩展字段。
- [x] Worker 的持久 invocation logs 已在 `telemetry-worker/wrangler.jsonc` 开启，作为 D1 结构化事件的第二信号源。

## 评测、超时与归档

- [x] 上传前保存 ZIP SHA-256、manifest、源提交、wheel 清单和包内文件清单；生成物仍由 `.gitignore` 排除。
- [x] 结果下载原样保留在 `result_temp/`（已忽略），同时记录 `workflow_result.json`、`decisions.csv`、`score_report.json`、`agent.log` 的哈希。
- [x] 每次 batch 记录终止原因、实际墙钟、动作数、分数组成、REQUIRED/FLEXIBLE 缺口和遥测发送统计。
- [ ] 若再次出现超时，先比较逐步追踪中的 `input_wait_ms` 与 `policy_ms`：前者长期约为秒级而后者为毫秒级，说明瓶颈在评测往返/调度；反之才优化策略。
- [ ] 只有确认带宽、额度和版本状态后才使用下一次评测额度；同一包重复运行优先于同时改变策略和运行时。

## 资源管理

- [x] 大型 `dist/`、`result_temp/`、`runs/` 和本地场景不入库。
- [ ] 长批次前后检查 `df -h`，清理可重建的 ZIP、wheel 缓存和临时日志；保留平台原始下载和必要的哈希记录。
- [ ] 远程遥测保持有界队列、短超时、有限重试和失败丢弃，网络不可用时不能延长评分路径。

## 提交前命令

```bash
uv run pytest -q tests
python -m unittest telemetry-worker/client/test_telemetry_client.py
python -m py_compile agents/observer/*.py tools/pack_project.py
uv run python tools/pack_project.py --agent agents/observer --wheels /path/to/numpy-wheels
unzip -l dist/observer-observer-project.zip | rg 'observer.project.json|telemetry.config.json|\.deps|\.venv|\.env'
df -h .
```

最后一条检查应确认有 `telemetry.config.json`、没有 `.venv`/`.env`；再用
`unzip -p ... observer.project.json | jq '.build'` 确认有 wheel 时使用 `.deps` build 步骤。
`.deps` 是平台构建阶段的可写目录，不应在提交 ZIP 中预生成。
