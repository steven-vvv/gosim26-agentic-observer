---
source: https://create.gosim.org/survey26/platform/resources?lang=zh
fetched_utc: 2026-09-26T03:46:59+00:00
method: rendered main; all FAQ entries expanded
---

资源

# 入门包与数据

在本地运行和评分智能体所需的全部内容：入门包，以及平台使用的各场景配置与参考文件。

1

## 第一步 · 拿包就跑

只需要这一个下载：解压、双击、十几秒出分。

01

### 入门包（zip）

agent/（最小智能体，可通过 .env 启用大模型）、challenge/（workflow、评分器、preview、模拟器、回放）、scenarios/dev-reference、local\_runner.py、score\_decisions.py、make\_scenario.py、pack\_agent.py、sac\_submit.py、SKILL.md。

[下载 ↓](/survey26/platform/downloads/agent-observer-starter-kit.zip)

2

## 第二步 · 写你自己的策略

改一个函数就能上榜；这些是抄作业和查手册的地方。

02

### 参考实现（GitHub）

一份写完整、能直接跑通的示范策略 reference\_strategy.py：只用快照里的公开信息和标准库，复制成 my\_strategy.py 就能跑。实测与贪心基线打平，可作为修改的起点。

[查看 →](https://github.com/gosimfoundation/hackathon-survey26/tree/main/challenge/participant_agent)

03

### scoring\_core.py

冻结的 challenge-score-v3 评分器（旁边还有 contracts.py 与 score\_config.json）。仅标准库，Python 3.12。

[下载 ↓](/survey26/platform/downloads/scoring_core.py)

04

### SKILL.md

AI 编程助手可直接照做的单文件说明：构建、测试、提交。

[查看 →](/survey26/platform/skill.md)

05

### 参赛文档

带精确列的数据格式、三种协议消息、评分公式与全部惩罚、打包规则与限制。

[查看 →](/survey26/platform/docs)

3

## 第三步 · 需要数据再来拿

下方列出各场景的公开文件。

## 场景

在下方下载当前比赛的公开配置与数据文件。

### dev-fortnight

Development fortnight (14 nights, seed 2026)

A short public scenario for quick iteration: 14 nights from 2026-10-05, all files public.

天气公开

总时长
:   30 min

夜
:   14

时隙
:   590

天区
:   64

目标
:   16012

请求
:   1

配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

scenario\_config.json ↓calendar\_config.json ↓tile\_config.json ↓weather\_config.json ↓request\_config.json ↓workflow\_config.json ↓score\_config.json ↓

日历 · 目录 · 请求

night\_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile\_windows.csv（窗口示例）、observation\_requests.csv 与 observation\_request\_tiles.csv、manifest 与 metadata。

night\_calendar.csv ↓slots.csv ↓tiles.csv ↓targets.csv ↓tile\_windows.csv ↓observation\_requests.csv ↓observation\_request\_tiles.csv ↓scenario\_manifest.json ↓calendar\_metadata.json ↓catalog\_metadata.json ↓observation\_request\_metadata.json ↓

天气真值

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score\_decisions.py 可复现平台报告。

weather.csv ↓weather\_metadata.json ↓

预报

weather\_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

weather\_forecasts.csv ↓

方向性事件

weather\_events.csv：active\_event\_ids 背后的方向性干扰真值。比赛场景隐藏。

weather\_events.csv ↓

合约: challenge-score-v3

### dev-reference

Development reference (180 nights, seed 20260909)

The published example3 reference scenario. Everything public, including weather events, so local scoring reproduces the platform.

天气公开

总时长
:   2 h

夜
:   180

时隙
:   7928

天区
:   64

目标
:   15833

请求
:   18

配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

scenario\_config.json ↓calendar\_config.json ↓tile\_config.json ↓weather\_config.json ↓request\_config.json ↓workflow\_config.json ↓score\_config.json ↓

日历 · 目录 · 请求

night\_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile\_windows.csv（窗口示例）、observation\_requests.csv 与 observation\_request\_tiles.csv、manifest 与 metadata。

night\_calendar.csv ↓slots.csv ↓tiles.csv ↓targets.csv ↓tile\_windows.csv ↓observation\_requests.csv ↓observation\_request\_tiles.csv ↓scenario\_manifest.json ↓calendar\_metadata.json ↓catalog\_metadata.json ↓observation\_request\_metadata.json ↓

天气真值

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score\_decisions.py 可复现平台报告。

weather.csv ↓weather\_metadata.json ↓

预报

weather\_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

weather\_forecasts.csv ↓

方向性事件

weather\_events.csv：active\_event\_ids 背后的方向性干扰真值。比赛场景隐藏。

weather\_events.csv ↓

合约: challenge-score-v3

### dev-fortnight

Development fortnight (14 nights, seed 2026)

A short public scenario for quick iteration: 14 nights from 2026-10-05, all files public.

天气公开

总时长
:   30 min

夜
:   14

时隙
:   590

天区
:   64

目标
:   16012

请求
:   1

配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

scenario\_config.json ↓calendar\_config.json ↓tile\_config.json ↓weather\_config.json ↓request\_config.json ↓workflow\_config.json ↓score\_config.json ↓

日历 · 目录 · 请求

night\_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile\_windows.csv（窗口示例）、observation\_requests.csv 与 observation\_request\_tiles.csv、manifest 与 metadata。

night\_calendar.csv ↓slots.csv ↓tiles.csv ↓targets.csv ↓tile\_windows.csv ↓observation\_requests.csv ↓observation\_request\_tiles.csv ↓scenario\_manifest.json ↓calendar\_metadata.json ↓catalog\_metadata.json ↓observation\_request\_metadata.json ↓

天气真值

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score\_decisions.py 可复现平台报告。

weather.csv ↓weather\_metadata.json ↓

预报

weather\_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

weather\_forecasts.csv ↓

方向性事件

weather\_events.csv：active\_event\_ids 背后的方向性干扰真值。比赛场景隐藏。

weather\_events.csv ↓

合约: challenge-score-v3

### dev-reference

Development reference (180 nights, seed 20260909)

The published example3 reference scenario. Everything public, including weather events, so local scoring reproduces the platform.

天气公开

总时长
:   2 h

夜
:   180

时隙
:   7928

天区
:   64

目标
:   15833

请求
:   18

配置

七个 JSON 文件：场景、日历、天区、天气、请求、workflow 与评分配置。initialize 消息中也会完整发布。

scenario\_config.json ↓calendar\_config.json ↓tile\_config.json ↓weather\_config.json ↓request\_config.json ↓workflow\_config.json ↓score\_config.json ↓

日历 · 目录 · 请求

night\_calendar.csv 与 slots.csv（时间轴）、tiles.csv 与 targets.csv（目录与科学权重）、tile\_windows.csv（窗口示例）、observation\_requests.csv 与 observation\_request\_tiles.csv、manifest 与 metadata。

night\_calendar.csv ↓slots.csv ↓tiles.csv ↓targets.csv ↓tile\_windows.csv ↓observation\_requests.csv ↓observation\_request\_tiles.csv ↓scenario\_manifest.json ↓calendar\_metadata.json ↓catalog\_metadata.json ↓observation\_request\_metadata.json ↓

天气真值

weather.csv：每个时隙一行的台址基线天气。练习场景公开，本地运行 score\_decisions.py 可复现平台报告。

weather.csv ↓weather\_metadata.json ↓

预报

weather\_forecasts.csv：不确定、每日修订的预报。平台只显示当前游标之前发布的修订。

weather\_forecasts.csv ↓

方向性事件

weather\_events.csv：active\_event\_ids 背后的方向性干扰真值。比赛场景隐藏。

weather\_events.csv ↓

合约: challenge-score-v3

## 命令行

```
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
# submit the decisions.csv your local run produced
python3 sac_submit.py --phase practice --kind results --scenario dev-reference --file run_output/decisions.csv --wait
```