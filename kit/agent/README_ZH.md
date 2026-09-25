> 最省事的改法：只改 `my_strategy.py` 里的 `choose_action`（候选已按公开评分排好序，返回要观测的候选或 `None` 等待），然后把这一个文件上传到网站，平台会自动补齐其余文件。下面是完整版说明。
>
> 入门包用法：在 `agent-observer-starter-kit/` 目录运行 `python3 local_runner.py --scenario scenarios/dev-reference --agent agent/minimal_agent.py`；打包提交用 `python3 pack_agent.py` 与 `python3 sac_submit.py`。下文的 `src/run_challenge.py` 命令来自主办方的原始仓库，在入门包中对应 `local_runner.py`。

# Minimal Example Agent

这是给第一次开发 Agent 的参赛者准备的保底实现。它每次只处理当前
`decision_snapshot`，不做年、月、周或整夜计划。不配置 API key 也可运行。

## 参考实现

`reference_strategy.py` 是一份写完整、讲清楚的示范策略：只用快照里的公开信息和
标准库，复制成 `my_strategy.py` 就能直接跑。先说实测结果：它和默认的贪心基线
**打平**——不是它没写好，而是宽松的练习场景里没有取舍可做。值得学的是它怎么读
决策快照、怎么应答协议、以及三笔到整场结束才结算的罚分账（漏必做天区 −1000/块、
分区缺额 −100/块、请求过期 −190/块）。它没有把「覆盖均匀性」算进决策——正式赛
场景（1600 块天区）里这一项计分，实测会不会抢覆盖能差出约 2% 的总分。想赢，
从读懂它、再改掉它开始。

## 责任边界

Agent 只能：

- 读取 workflow 发布的当前快照；
- 使用公开评分配置计算无副作用 preview；
- 从当前合法候选中选一个 action；
- 返回一条 JSON decision。

Simulator/workflow 负责真值、当前 cursor、天气与 tile 信息发布、action
提交和时间推进。正式 scorer 负责重放曝光分段与结算。Agent 不得读取
未发布的天气、自行推进 slot，或调用 `ChallengeScorer.apply_decision()` 做试探。

## 最小运行

在 `example3/` 目录下：

```bash
python3 -B src/run_challenge.py --wallclock-seconds 2 \
    --agent-command python3 -B participant_agent/minimal_agent.py
```

默认 `MODEL_PROVIDER=deterministic`，不需安装 LangChain，不需网络和 key。Agent
会选择当前公开边际估计最高的合法 action。

## 启用 LLM

本项目已使用 Python 3.12 的 Conda 环境 `survey-agent` 验证。推荐安装方式：

```bash
conda run -n survey-agent python -m pip install \
    -r participant_agent/requirements.txt
cp participant_agent/.env.example participant_agent/.env
```

然后编辑本地 `.env`。该文件已被 `.gitignore` 排除，不得提交。运行时可将命令
中的 `python3` 替换成该环境 Python 的绝对路径，保证 workflow 与 Agent 使用
同一套依赖。

OpenAI 示例：

```dotenv
MODEL_PROVIDER=openai
MODEL_NAME=<your-current-model-id>
MODEL_API_MODE=responses
OPENAI_API_KEY=<your-key>
```

Anthropic 示例：

```dotenv
MODEL_PROVIDER=anthropic
MODEL_NAME=<your-current-model-id>
ANTHROPIC_API_KEY=<your-key>
```

Grok/GLM/DeepSeek/Kimi/Qwen/MiniMax 等 OpenAI-compatible 服务使用：

```dotenv
MODEL_PROVIDER=deepseek
MODEL_NAME=<provider-model-id>
MODEL_BASE_URL=<official-compatible-endpoint>
DEEPSEEK_API_KEY=<your-key>
```

可选 `MODEL_PROVIDER` 值与 key 名：

| 配置值 | key |
|---|---|
| `openai` / `chatgpt` | `OPENAI_API_KEY` |
| `anthropic` / `claude` | `ANTHROPIC_API_KEY` |
| `xai` / `grok` | `XAI_API_KEY` |
| `zai` / `glm` | `ZAI_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `moonshot` / `kimi` | `MOONSHOT_API_KEY` |
| `dashscope` / `qwen` | `DASHSCOPE_API_KEY` |
| `minimax` | `MINIMAX_API_KEY` |

除 OpenAI 和 Anthropic 的原生 LangChain adapter 外，其他 profile 均需显式提供
`MODEL_BASE_URL`。实际 endpoint 和 model ID 可能变化，应以各厂商当前官方文档为准。

## JSON-Lines 协议

平台与 Agent 长期保持同一个子进程：

```text
platform -> initialize          # 一次，不回复
platform -> decision_request    # 当前快照
agent    -> decision_response   # 一个 action
platform -> decision_request
agent    -> decision_response
...
```

每条消息是单独一行 JSON。`decision_response.decision_sequence` 必须与请求相同。
反复查询当前快照不推进世界；提交 action 才推进时间。

快照还带两块异常检测相关的信息：`tile_last_finished`（最近一次完成观测的实现分数，
中断得 0，首次完成前为 null）与夜初才出现的 `fault_status`（正确故障上报一天后发布
故障范围/生效乘数/修复完成时间，维修期每晚重发；误报则同期收到一次 `status: normal` 应答）。
`decision_response` 可附 `reports` 数组上报异常：`{"kind":"Instrument_Failure"}` 或
`{"kind":"NOVA"|"Reddening","tile_id":"..."}`，不占时隙、不推进时间；畸形条目被丢弃，
动作照常入账。被接受的上报在 `decisions.csv` 里落为紧随承载决策的
`report_instrument_failure` / `report_nova` / `report_reddening` 动作行（共享递增的
`decision_id` 序列），单个文件即可完整复放。

## 当前评分参数

初始消息会提供完整 `challenge-score-v3` 配置。当前公开值为：

- `BRIGHT/DARK` 组合质量阈值：`0.40 / 0.65`；
- `BACKUP/BRIGHT/DARK` 匹配奖励：`0.08 / 0.15 / 0.25`；
- unsafe observe：`2000`；普通非法 action：`100`；
- avoidable wait：每秒 `0.001`；
- REQUIRED miss：每 tile `1000`；
- FLEXIBLE region shortfall：每 tile `100`，每 region quota 为 `4`。
- 重复观测合法：每个 tile 按历次观测的最高分入账，完成状态仍以首次合法观测为准。
- 隐藏标签乘数：nova `×1.5`、reddening `×0.8`（可叠加）；标签上报对了 `+100`、错了 `−150`
  （每个 tile 每种标签只计首次）；故障误报在每次正确上报之间有一次免费额度，之后每次 `−100`。

`scoring_preview.py` 使用公开当前天气、airmass 和 lunar factor 计算（快照不含
`instrument_efficiency`，preview 基线因此不含效率因子——实现分与基线的偏差正好隔离出
隐藏的仪器侧：效率抖动 × 故障乘数 × 标签乘数）：

```text
atmospheric = transparency * sky_quality / (seeing_arcsec * airmass)
combined = atmospheric * lunar_quality_factor
estimated_science = V_tile * combined * (1 + matched_program_bonus)
```

它还显式列出 REQUIRED/FLEXIBLE 终局惩罚减免和请求策略价值。跨 slot 曝光
的后续天气尚未发布，因此 preview 按当前条件保持不变进行估计；正式
scorer 仍按真实曝光分段结算。

## 回退保障

以下情况均不会让示例 Agent 提交虚构 action：

- 未配置 key 或未安装 provider package；
- 超时、限流或 provider 异常；
- 返回非 JSON；
- 选择不存在的 tile/program/request；
- 返回的候选不在给 LLM 的 Top-K 内。

模型选择会先在本地验证，失败后选择确定性 preview 第一名。当前无任何
可完成观测时才返回 `wait`。
