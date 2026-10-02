# 本机模型与遥测 API 配置

本机凭据放在 `/home/steven/.config/gosim26-telemetry.env`，属于仓库外的私有文件，权限应为 `0600`。此文件已经含有遥测地址和令牌变量；后续填写时保留它们。不要在对话、提交、日志或结果报告中粘贴密钥，也不要用 `cat`、`sed`、`env` 等命令向工具输出完整文件或环境变量。

在该文件中填写以下三个已有的空值，保留 shell 单引号；如果值本身包含单引号，需要按 shell 语法转义。

```dotenv
export OBSERVER_MODEL_BASE_URL='https://provider.example/v1'
export OBSERVER_MODEL_API_KEY='填写本机密钥'
export OBSERVER_MODEL_NAME='服务商提供的模型标识'
```

`OBSERVER_MODEL_BASE_URL` 也可填写完整的 `/chat/completions` 路径。当前本机配置用 `OBSERVER_MODEL_*` 独立测试模型；已有的 `TELEMETRY_URL` / `TELEMETRY_TOKEN` 继续走显式遥测路径。`TELEMETRY_DISABLE_OPENAI=1` 防止把遥测标记消息误发到普通模型端点。共享 Worker 的上游模型固定为 `ds/deepseek-flash`，模型 ID 在 `telemetry-worker/wrangler.jsonc` 中，端点与密钥通过远端 `LLM_UPSTREAM_URL` 和 `LLM_UPSTREAM_API_KEY` secret 配置。后续平台注入指向该 Worker 的 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY` 后，模型和遥测可共用它；本机文件不会自动修改远端 Worker 或平台凭据。

完成填写后，只回复“已配置”，不要附上文件内容。测试程序会在本地进程中加载环境变量，只输出是否配置、HTTP/解析结果、耗时和确定性回退结果；原始请求、响应、密钥均不写入报告。正式提交包默认拒绝 `.env`，因此本机配置不会随 ZIP 上传。正式比赛采用哪种平台凭据注入方式，仍须在发布前按当时的平台规则和实际端点核验。

本机直接冒烟测试使用下列命令；它发送两次标为合成样例的模型请求，分别覆盖周方案规划和行动决策，输出选择、归一化概率、可见首 token 延迟、生成首 token 延迟、输出速率、Token 用量及耗时，不输出原始响应。完整场景验证见 `notes/2026-09-29-observability-agent-native.md`。

```bash
set -a
. /home/steven/.config/gosim26-telemetry.env
set +a
uv run python v3/tools/smoke_model_api.py
```

观测配置集中在 `v3/agents/observer/observability.config.json`，打包时生成同名无密钥配置；`local` 控制 `agent.log`，`remote` 控制 Worker 投递，`events` 控制各类事件，`checkpoint_every` 控制周期采样。`OBSERVABILITY_LOCAL_ENABLED` 和 `OBSERVABILITY_CHECKPOINT_EVERY` 可覆盖本地设置；`TELEMETRY_*` 仍覆盖远程入口。`TELEMETRY_DISABLE=1` 只停远程投递，保留本地诊断。模型流式指标由 `llm.config.json` 的 `stream` 控制：非流式响应无法测得真实首 token 延迟，此时字段为 `null`；上游未返回 usage 时 Token 用量与速率也不估造。

v3 收尾时点也由这份配置管理，正式包默认标记运行器支持上游 `finish` 消息：

```json
{
  "termination": {
    "mode": "upstream_finish",
    "grace_drain_timeout_seconds": 20.0
  }
}
```

`upstream_finish` 模式先发送最后决策响应，收到 `finish` 后排空遥测队列、生成运行分析报告并投递最终报告，然后退出；不向 `finish` 回复 stdout。`last_slot` 模式保留旧行为：推断最后时隙后，在最后响应前阻塞执行相同收尾。两种模式都只生成一次最终报告。可用 `OBSERVABILITY_TERMINATION_MODE=last_slot` 临时切换，或修改配置后重新打包。

上游 v3 的正常收尾宽限期为 30 秒，不计入场景墙钟。`grace_drain_timeout_seconds` 的范围为 0–20 秒，亦可由 `OBSERVABILITY_GRACE_DRAIN_TIMEOUT_SECONDS` 覆盖；实际排空时间还会受消息中的宽限期限制，预留最终投递时间及退出余量。最终投递等待上限继续由 `remote.final_timeout_seconds`（默认 5 秒）控制，并按剩余宽限期缩短。无有效宽限期时沿用 `remote.drain_timeout_seconds`（默认 2 秒）的有界收尾。服务异常时保留本地报告和失败/丢弃统计，不无限等待远端成功。

如果 stdin 正常关闭而没有 `finish`，仍会在 EOF 生成报告：推断过末时隙时标记 `termination_source=eof_fallback`，否则为 `stdin_eof`。这不能覆盖运行器直接 SIGTERM/SIGKILL 的情况；没有优雅退出能力的旧运行器应显式选择 `last_slot`。报告的 `termination_reason` 只来自上游消息；本地末时隙推断另记为 `inferred_termination_reason=survey_complete`。`termination_source` 区分 `last_slot`、`upstream_finish` 和 EOF 收尾，`final_response_pending` 指明最终报告是否在最后响应之前生成。详细核验见 `notes/2026-09-29-v3-graceful-shutdown.md`。

正式打包的 `llm.config.json` 将输出上限设为 2048 token，完整 SSE 响应体限制为 1 MiB，上下文上限为 180000 字节。1024 token 配置在完整场景中仍有两次因上游内部推理过长而截断。上下文超限直接回退，不向模型发送删去真实现状的请求；响应膨胀也有界失败。`ttft_ms` 是首个可见输出字符的延迟，`generation_ttft_ms` 是流中首个生成内容（可能是推理内容）的延迟；输出速率用上游报告的全部 completion token 除以首个生成内容到请求结束的时间，是包含网络传输的近似值，不是可见文本的速率。直接构造 `AdvisorConfig` 的开发调用仍保留原默认值，以兼容旧测试和外部调用；打包配置优先级更高。
