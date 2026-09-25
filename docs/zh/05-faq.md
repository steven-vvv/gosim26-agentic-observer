---
title: "报名前的问题"
section: "常见问题"
source: https://create.gosim.org/survey26/platform/faq?lang=zh
lang: zh-CN
fetched: 2026-09-23
notes:
  - "共 17 条问答，其中 2 条（正式赛异常机制相关）受站点开关 mechanics_public 控制，抓取时未公开，故未收录。"
---

# 报名前的问题

## 我编程基础一般，最少要做什么？

下载入门包，双击 run_baseline，然后只改 agent/my_strategy.py 里的一个函数：它收到「现在能观测的候选」（已排好序），返回要观测的那个或 None 等待。把这一个文件拖到「提交」页即可，平台会补齐其余文件。macOS 自带的 Python 就够用，Windows 从 python.org 装 Python 3.12。

## 需要天文背景吗？

不需要。智能体收到的是 JSON 快照：当前时隙天气、带高度、airmass、月光因子、有效条件与 tile_science_value 的候选天区、迄今发布的预报与请求，以及进度。文档解释了会出现的少数术语，scoring_preview.py 也已经能把快照变成候选排序。

## 可以个人参赛吗？

可以。创建人数上限为 1 的队伍即可。队伍最多 3 人。

## 可以用什么语言？

本地不限语言：自己运行智能体并上传 decisions.csv，两个阶段都接受。平台运行智能体程序包时，入口脚本须为通过 stdin/stdout 使用 participant-agent-protocol-v1/v2 的 Python 3.12（minimal_agent.py、agent.py 或 main.py）；它可以按 requirements.txt 安装依赖，并在沙箱内启动所需的子进程。

## 可以使用大模型或商业 API 吗？

可以，决策时也可以。平台运行允许联网：把服务商密钥写进程序包的 .env（MODEL_PROVIDER、MODEL_NAME、\<PROVIDER>\_API_KEY），最小智能体会通过 LangChain 调用模型。注意全局时钟——180 晚场景约 8,000 次决策每次都调用模型是放不下的；把显而易见的等待交给确定性代码。

## 什么是全局时钟？

每个场景公布 global_wallclock_seconds（参考场景 7200 秒，短场景更少）。它在 initialize 消息后开始并覆盖全部决策——包括快照序列化、你的思考时间与解析。没有单次决策限制。到期时进程被停止，已提交动作照常评分，并适用未完成 必做天区、可选天区缺额与到期请求的终局惩罚。

## 比赛场景隐藏了什么？

weather.csv、weather_forecasts.csv 与 weather_events.csv。智能体仍能看到当前时隙天气、每个候选的有效条件（已应用方向性事件）、迄今发布的预报与活动请求——快照发布的一切——但永远看不到未来天气。练习场景三份文件全部公开，本地运行 score_decisions.py 可复现平台报告。

## 可以提交多少次？

Playground每队每天 50 次；线上比赛每队每天 10 次。每队取最佳已评分成绩。

## 排行榜分数就是最终成绩吗？

线上比赛榜（10 月 5–7 日）决定名次。主办方在 10 月 17 日确认奖项前会核验前列提交的合规性。

## 必须到深圳现场吗？

不需要。比赛在线进行。获奖队伍受邀参加 10 月 17 日 GOSIM 深圳颁奖日，也可以远程领奖。

## 平台是否免费？

是。报名、数据、入门包与平台运行均免费。

## 必须开源吗？

不强制。主办方可能要求前列队伍提供智能体程序包以在确认奖项前复现结果。

## 智能体崩溃或超时会怎样？

未能在 30 秒内初始化、退出或回复无效消息的运行以 agent_error 或 agent_initialization_error 结束。此前提交的动作照常评分并适用终局惩罚，智能体的 stderr 末尾显示在提交页。全局时钟到期时同样只对已提交动作评分（global_wallclock_expired）。修复后在每日限额内重新提交即可。

## 奖金是否含税？

奖金为税前金额。支付细节在颁奖日后与获奖队伍确认。

## 开发阶段有 API 额度吗？

已注册队伍可在控制台领取赞助商 API 兑换码，每队每个服务商一个。平台运行允许联网，同一个密钥既可用于本地开发，也可写进程序包的 .env 用于隐藏天气运行。

其他问题： [hackathon@gosim.org](mailto:hackathon@gosim.org)
