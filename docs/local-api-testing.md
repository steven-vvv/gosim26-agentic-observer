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
uv run python tools/smoke_model_api.py
```

观测配置集中在 `agents/observer/observability.config.json`，打包时生成同名无密钥配置；`local` 控制 `agent.log`，`remote` 控制 Worker 投递，`events` 控制各类事件，`checkpoint_every` 控制周期采样。`OBSERVABILITY_LOCAL_ENABLED` 和 `OBSERVABILITY_CHECKPOINT_EVERY` 可覆盖本地设置；`TELEMETRY_*` 仍覆盖远程入口。`TELEMETRY_DISABLE=1` 只停远程投递，保留本地诊断。模型流式指标由 `llm.config.json` 的 `stream` 控制：非流式响应无法测得真实首 token 延迟，此时字段为 `null`；上游未返回 usage 时 Token 用量与速率也不估造。

正式打包的 `llm.config.json` 将输出上限设为 2048 token，完整 SSE 响应体限制为 1 MiB，上下文上限为 180000 字节。1024 token 配置在完整场景中仍有两次因上游内部推理过长而截断。上下文超限直接回退，不向模型发送删去真实现状的请求；响应膨胀也有界失败。`ttft_ms` 是首个可见输出字符的延迟，`generation_ttft_ms` 是流中首个生成内容（可能是推理内容）的延迟；输出速率用上游报告的全部 completion token 除以首个生成内容到请求结束的时间，是包含网络传输的近似值，不是可见文本的速率。直接构造 `AdvisorConfig` 的开发调用仍保留原默认值，以兼容旧测试和外部调用；打包配置优先级更高。
