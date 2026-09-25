---
title: "入门包与数据"
section: "资源"
source: https://create.gosim.org/survey26/platform/resources?lang=zh
lang: zh-CN
fetched: 2026-09-23
notes:
  - "“场景”列表来自平台数据库，为抓取时的状态；场景文件在页面上通过按钮（JS）下载，这里只列文件名，可用入门包里的 fetch_scenario.py 获取。"
---

# 入门包与数据

在本地运行和评分智能体所需的全部内容：入门包，以及平台使用的各场景配置与参考文件。

## 第一步 · 拿包就跑

只需要这一个下载：解压、双击、十几秒出分。

### 01 入门包（zip）

agent/（最小智能体，可通过 .env 启用大模型）、challenge/（workflow、评分器、preview、模拟器、回放）、scenarios/dev-reference、local_runner.py、score_decisions.py、make_scenario.py、pack_agent.py、sac_submit.py、SKILL.md。

[下载](https://create.gosim.org/survey26/platform/downloads/agent-observer-starter-kit.zip)

## 第二步 · 写你自己的策略

改一个函数就能上榜；这些是抄作业和查手册的地方。

### 02 参考实现（GitHub）

一份写完整、能直接跑通的示范策略 reference_strategy.py：只用快照里的公开信息和标准库，复制成 my_strategy.py 就能跑。实测与贪心基线打平，可作为修改的起点。

[查看](https://github.com/gosimfoundation/hackathon-survey26/tree/main/challenge/participant_agent)

### 03 scoring_core.py

冻结的 challenge-score-v3 评分器（旁边还有 contracts.py 与 score_config.json）。仅标准库，Python 3.12。

[下载](https://create.gosim.org/survey26/platform/downloads/scoring_core.py)

### 04 SKILL.md

AI 编程助手可直接照做的单文件说明：构建、测试、提交。

[查看](https://create.gosim.org/survey26/platform/skill.md)

### 05 参赛文档

带精确列的数据格式、三种协议消息、评分公式与全部惩罚、打包规则与限制。

[查看](https://create.gosim.org/survey26/platform/docs)

## 第三步 · 需要数据再来拿

每个场景哪些文件公开、哪些隐藏，一目了然；正式赛材料永不公开。

## 场景

一个场景是一个目录：config/\*.json 加 outputs/reference/\*.csv。配置、日历、目录与请求始终可下载；天气、预报与事件遵循场景的可见性标记。隐藏文件以删除线显示。比赛场景的随机种子不公开，无法由公开配置重建隐藏天气。

### dev-fortnight

Development fortnight (14 nights, seed 2026)

A short public scenario for quick iteration: 14 nights from 2026-10-05, all files public.

**天气公开**

| 总时长 | 夜 | 时隙 | 天区 | 目标 | 请求 |
|---|---|---|---|---|---|
| 30 min | 14 | 590 | 64 | 16012 | 1 |

#### 配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### 日历 · 目录 · 请求

night_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile_windows.csv（窗口示例）、observation_requests.csv 与 observation_request_tiles.csv、manifest 与 metadata。

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### 天气真值

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score_decisions.py 可复现平台报告。

`weather.csv` · `weather_metadata.json`

#### 预报

weather_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

`weather_forecasts.csv`

#### 方向性事件

weather_events.csv：active_event_ids 背后的方向性干扰真值。比赛场景隐藏。

`weather_events.csv`

合约: challenge-score-v3

### dev-reference

Development reference (180 nights, seed 20260909)

The published example3 reference scenario. Everything public, including weather events, so local scoring reproduces the platform.

**天气公开**

| 总时长 | 夜 | 时隙 | 天区 | 目标 | 请求 |
|---|---|---|---|---|---|
| 2 h | 180 | 7928 | 64 | 15833 | 18 |

#### 配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### 日历 · 目录 · 请求

night_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile_windows.csv（窗口示例）、observation_requests.csv 与 observation_request_tiles.csv、manifest 与 metadata。

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### 天气真值

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score_decisions.py 可复现平台报告。

`weather.csv` · `weather_metadata.json`

#### 预报

weather_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

`weather_forecasts.csv`

#### 方向性事件

weather_events.csv：active_event_ids 背后的方向性干扰真值。比赛场景隐藏。

`weather_events.csv`

合约: challenge-score-v3

### eval-a

Competition scenario A (hidden weather)

Online competition replay A: 30 nights from 2026-10-05, 1600 tiles. Weather, forecasts and events are hidden; coverage evenness is scored.

**天气隐藏**

| 总时长 | 夜 | 时隙 | 天区 | 目标 | 请求 |
|---|---|---|---|---|---|
| 1 h | 30 | 1289 | 1600 | 403975 | 2 |

#### 配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### 日历 · 目录 · 请求

night_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile_windows.csv（窗口示例）、observation_requests.csv 与 observation_request_tiles.csv、manifest 与 metadata。

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### 天气真值

*不公开（仅平台运行）*

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score_decisions.py 可复现平台报告。

~~`weather.csv`~~ · ~~`weather_metadata.json`~~

#### 预报

*不公开（仅平台运行）*

weather_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

~~`weather_forecasts.csv`~~

#### 方向性事件

*不公开（仅平台运行）*

weather_events.csv：active_event_ids 背后的方向性干扰真值。比赛场景隐藏。

~~`weather_events.csv`~~

合约: challenge-score-v3 · 种子不公开（防止重建隐藏天气）

### eval-b

Competition scenario B (hidden weather)

Online competition replay B: 30 nights from 2026-11-01, 1600 tiles, different seed. Weather, forecasts and events are hidden; coverage evenness is scored.

**天气隐藏**

| 总时长 | 夜 | 时隙 | 天区 | 目标 | 请求 |
|---|---|---|---|---|---|
| 1 h | 30 | 1365 | 1600 | 404120 | 1 |

#### 配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

`scenario_config.json` · `calendar_config.json` · `tile_config.json` · `weather_config.json` · `request_config.json` · `workflow_config.json` · `score_config.json`

#### 日历 · 目录 · 请求

night_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile_windows.csv（窗口示例）、observation_requests.csv 与 observation_request_tiles.csv、manifest 与 metadata。

`night_calendar.csv` · `slots.csv` · `tiles.csv` · `targets.csv` · `tile_windows.csv` · `observation_requests.csv` · `observation_request_tiles.csv` · `scenario_manifest.json` · `calendar_metadata.json` · `catalog_metadata.json` · `observation_request_metadata.json`

#### 天气真值

*不公开（仅平台运行）*

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score_decisions.py 可复现平台报告。

~~`weather.csv`~~ · ~~`weather_metadata.json`~~

#### 预报

*不公开（仅平台运行）*

weather_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

~~`weather_forecasts.csv`~~

#### 方向性事件

*不公开（仅平台运行）*

weather_events.csv：active_event_ids 背后的方向性干扰真值。比赛场景隐藏。

~~`weather_events.csv`~~

合约: challenge-score-v3 · 种子不公开（防止重建隐藏天气）

## 命令行

```bash
# environment for sac_submit.py (also printed in SKILL.md inside the kit)
export SAC_URL=https://vdiemcofukuxglqsmlyz.supabase.co
export SAC_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkaWVtY29mdWt1eGdscXNtbHl6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg5NTU3OTcsImV4cCI6MjEwNDUzMTc5N30.qMbZ_C3iKq70SdGDYQPPUD0LMBst-xBspZ-zj_gn75w
export SAC_EMAIL=you@example.org SAC_PASSWORD='...'

unzip agent-observer-starter-kit.zip && cd agent-observer-starter-kit
# run the minimal agent locally on the bundled public scenario, then re-score the trace (details: SKILL.md)
python3 local_runner.py --scenario scenarios/dev-reference --agent agent/minimal_agent.py --wallclock 600 --out run_output
python3 score_decisions.py --scenario scenarios/dev-reference --decisions run_output/decisions.csv
python3 make_scenario.py --out scenarios/mine --seed 7 --days 30      # more practice scenarios
python3 fetch_scenario.py dev-fortnight                                # any published scenario -> scenarios/dev-fortnight/
python3 pack_agent.py --agent agent --out my_agent.zip                 # the package you submit
# submit: a results file for a public-weather practice scenario, or the agent package (zip) for platform runs
python3 sac_submit.py --phase practice --kind results --scenario dev-reference --file run_output/decisions.csv --wait
python3 sac_submit.py --phase online --kind agent --file my_agent.zip --wait
```
