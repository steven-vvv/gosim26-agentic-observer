# 比赛平台公开数据快照

保存 2026-09-23 从巡天智能体比赛平台抓取的公开数据，防止之后被修改或下线。所有数据都用资源页公开的 anon key（`SAC_KEY`）读取，不需要登录；内容与匿名访问平台页面时看到的一致。

## leaderboard/

按抓取时间（UTC）分目录，每个目录是一次完整快照。

| 文件 | 来源 | 内容 |
|---|---|---|
| `leaderboard_practice.json` | RPC `leaderboard`（`p_phase_slug=practice`） | Playground 榜单 |
| `leaderboard_online.json` | RPC `leaderboard`（`p_phase_slug=online`） | 线上比赛榜单；比赛 2026-10-04 16:00 UTC 才开始，目前为空 |
| `participants_stats.json` | RPC `participants_stats` | 首页展示的报名统计：队伍数、人数、找队友人数等 |
| `phases.json` | 表 `phases`（含各阶段关联的场景） | 阶段配置；只在 `2026-09-23T0808Z` 中保存了 |

解读榜单时注意：`kind=results` 是结果文件，只对应一个场景（这里都是 dev-reference）；`kind=agent` 是智能体程序包，分数取 dev-reference 和 dev-fortnight 两场的平均值。两类分数不能直接比较。

## scenarios/

两个决赛场景（eval-a、eval-b）的全部公开文件：`config/` 下 7 个，`outputs/reference/` 下 11 个。目录结构与入门包里的场景目录相同。

- 天气真值、预报、方向性事件和隐藏异常标签（`weather.csv`、`weather_forecasts.csv`、`weather_events.csv`、`tile_anomalies.csv`）不公开，没有这几个文件。
- 已按 `outputs/reference/scenario_manifest.json` 中的 SHA-256 校验，14 个列入清单且公开的文件全部一致。

## 注意

- `config/scenario_config.json` 和 `outputs/reference/scenario_manifest.json` 里都有 `seed` 字段，但资源页说比赛场景的种子不公开（防止重建隐藏天气）。这里原样保留，仅作快照留存。不要用它还原隐藏天气：这违反规则第 4.5 条，查实会被取消资格。建议告知主办方（hackathon@gosim.org）。
- 主办方修复这个问题时可能会重新生成决赛场景。届时本目录里的天区目录、时间窗、请求可能与线上不一致，需要重新下载。
- 榜单里的 `leader_github` 是队长的 GitHub 用户名，在平台排行榜上本来就公开显示。
