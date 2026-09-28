# 本机模型与遥测 API 配置

本机凭据放在 `/home/steven/.config/gosim26-telemetry.env`，属于仓库外的私有文件，权限应为 `0600`。此文件已经含有遥测地址和令牌变量；后续填写时保留它们。不要在对话、提交、日志或结果报告中粘贴密钥，也不要用 `cat`、`sed`、`env` 等命令向工具输出完整文件或环境变量。

在该文件中填写以下三个已有的空值，保留 shell 单引号；如果值本身包含单引号，需要按 shell 语法转义。

```dotenv
export OBSERVER_MODEL_BASE_URL='https://provider.example/v1'
export OBSERVER_MODEL_API_KEY='填写本机密钥'
export OBSERVER_MODEL_NAME='服务商提供的模型标识'
```

`OBSERVER_MODEL_BASE_URL` 也可填写完整的 `/chat/completions` 路径。当前本机配置用 `OBSERVER_MODEL_*` 独立测试模型；已有的 `TELEMETRY_URL` / `TELEMETRY_TOKEN` 继续走显式遥测路径。`TELEMETRY_DISABLE_OPENAI=1` 防止把遥测标记消息误发到普通模型端点。若未来部署了已配置上游的共享 Worker，应改为由平台注入指向该 Worker 的 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`，并让模型和遥测共用它；Worker 自身还必须配置 `LLM_UPSTREAM_URL` 与 `LLM_UPSTREAM_API_KEY`。本机文件不会自动修改远端 Worker 或平台凭据。

完成填写后，只回复“已配置”，不要附上文件内容。测试程序会在本地进程中加载环境变量，只输出是否配置、HTTP/解析结果、耗时和确定性回退结果；原始请求、响应、密钥均不写入报告。正式提交包默认拒绝 `.env`，因此本机配置不会随 ZIP 上传。正式比赛采用哪种平台凭据注入方式，仍须在发布前按当时的平台规则和实际端点核验。

本机直接冒烟测试使用下列命令；它发送两次标为合成样例的模型请求，分别覆盖计划自适应和行动决策，输出选择、归一化概率及耗时，不输出原始响应。随后还需用实际场景运行一次 agent，以核对真实决策上下文、遥测和时间预算。

```bash
set -a
. /home/steven/.config/gosim26-telemetry.env
set +a
uv run python tools/smoke_model_api.py
```
