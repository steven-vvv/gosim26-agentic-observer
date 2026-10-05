# 恢复点：v4 正式卡四类研究（2026-10-05 23:10 HKT 暂停）

暂停时所有实验、子代理、定时任务都已停止，各工作树都已提交、没有未提交改动。本文件说明从哪里接着做。
结论的完整证据在 [`2026-10-05-v4-formal-kinds.md`](2026-10-05-v4-formal-kinds.md)。

## 1. 截止时刻与赛程

- 线上期截止 **10-07 23:59:59（UTC+8）**；之后锁定最终版本，主办方在隐藏卡 E–H 各跑 3 次取平均。
- 最终版本须在截止前到平台「参赛」页手动标记，否则自动取线上最高分那次的版本。
- **当前平台上只有 10-04 那一次克制档运行**（`observer4-f7a2a2cf4bf4.zip`，云控返回 `formal_conservative`），
  正式榜第 37/69。用户 10-05 决定**暂不提交**，先离线分析（A–D 就是本地 `trial-*` 卡，只差 CPU 预算换算）。
- 最终提交前必须：烘焙 full（或更新后的）档位、`remote_config.enabled=false`，并在平台跑一次核对启动事件。

## 2. 工作树与分支

| 路径 | 分支 | 内容 |
| --- | --- | --- |
| `GOSIM26` | `claude`（本文件所在提交） | 四类卡生成器 `v4/evaluation/kinds.py`、规格 `kind_specs/`、`fiber_audit.py`、数据集与补丁、笔记 |
| `../GOSIM26-perf` | `perf` @ `1efffc3` | **逐位不变的提速**（10 个提交），CPU 约为原来的 50–60 % |
| `../GOSIM26-algo` | `algo` @ `bedb47b` | 研究参数 `setting_boost` / `rising_weight` / `upgrade_weight` / `upgrade_factor` |
| `../GOSIM26-season` | `season` @ `c1547a6` | 加 `season_weight` |
| `../GOSIM26-integ` | `integ` @ `14ef2ff` | perf + season 合并，再加 `window_weight`、结构切换 `observer4/structure.py`（`structure.enabled`）、阈值 0.34 |
| `../GOSIM26-req` | `req` @ `8c1aabc` | integ（到 `54035cf`）+ `request_anchors` |

- 所有研究参数**默认关闭**，默认参数下 integ / req 回放四张试验卡录制逐位一致，83 项测试与 Ruff 通过。
- `planning.py` 必须保持在 **60,000 字符**以内（主办方源码审查单文件上限，`test_agent_review` 会失败）：
  req 分支为 59,965，余量很小；再加逻辑请放到新模块。
- 研究工作树的 `v4/cards/` 是指向主工作区卡片的符号链接（未跟踪）；`v4/results/` 被忽略规则管理。
- 上游克隆 `../hackathon-survey26` 处于 **detached HEAD = 锁定提交 `d293dea`**（为了实验期间的锁校验）；
  `main` 已快进到 `33f9a50`（只有规则文案与 runner 重试，`challenge/` 无改动）。恢复后可
  `git -C ../hackathon-survey26 checkout main`，并把 `v4/evaluation/upstream.lock.json` 更新到 `33f9a50`
  （引擎无变化，更新前可再跑一张卡做逐位核对）。
- 合并方向尚未决定：建议把 `integ`（或 `req`）快进/合并到 `claude`，再按 git 约定快进 `main`；推送前先问用户。

## 3. 已确认的结论（试验卡 = 正式 A–D，对照 full 档）

- **正式卡 A–D 与撤下的试验卡 a–d 同卡同种子**（观测请求逐条一致；trial-a 克制档本地 23,278.0，平台 23,303.6）。
  正式榜不能代表 E–H 上的前向实力；E–H 按主办方脚本必须是新卡，预计同四类结构。
- full 对克制档：a +2,252、b +2,113、c +1,861、d +148。本地 full 四卡均分 30,663（当前榜约第 14）。
- 平台计费换算（平台计费 / 本机 CPU 秒）：a 0.538、b 0.525、c 0.374、d 0.494；full 四类都在预算一半以内。
- 筛选结果（对照 full）：

| 改动 | a | b | c | d | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| 关落下加成 `setting_boost 0` | −185 | **+2,933** | +216 | −27 | 只对 b 类 |
| 程序档升级 `upgrade_weight 0.5` | | | | **+340** | 只对 d 类 |
| 关规模档 | | +1,061（未跑完季） | | −211 | b 有潜力，预算紧 |
| 关落下加成 + 关规模档 | | +3,308（平台约 824/900） | | | 需提速后再看 |
| 落下加成 0.15 | | +524 | | | |
| 季节稀缺 `season_weight 0.5` | +54 | −904 | | | 否 |
| season 0.5 + 关落下加成 | +278 | +400 | +236 | −321 | 否（全局） |
| 宽搜索 / 科学指数 1.2、2.0 / 视野 200 h / 安全余量 0.75、0 | 全负或持平 | | | | 否 |
| 升起目标折扣 0.3 | | −599 | | −321 | 否 |
| 必观测奖励 100 / 60 / 100+安全 0.6 | +88 / +8 / −112 | | −418 / −187 / −130 | −237 / +217 / −234 | 否，保留 200 / 0.52 |

- b 类的机制：两块天区 C1（RA 300–60°，全季）与 C2（RA 60–150°，只在季末 2–3 个月可见）；落下加成在季末把
  时间留给正在落下的 C1，关掉后 11 月 C2 得到 149 h（原 34 h），C2 观测目标 15,637 → 18,465。
- 结构判据（`structure.py`，只用公开目录与夜历）：季末偏重目标比例 b 0.40–0.49、a 0.14–0.27、c 0、d 0.03–0.08；
  每目标季内光纤小时 d 9.6、其余 ≤ 0.84。阈值：≥ 0.34 关落下加成；≥ 4 打开程序档升级 0.5。
- 失分结构：总光纤时间不是瓶颈（a 136 %、b 228 % 于全部做满所需），损失在同一指向共享曝光时长与视场几何；
  a、c 的损失一半来自 QSO，b 的一半来自 ELG。第一个观测请求在 b、c、d 上总失败（8 个目标相距约 50°）。
- 正式榜（13:56 UTC，69 队）：Pleiades 33,518、迷星叫 32,340、starwing 32,236、AstroNJU 32,187、豆包 32,104；
  我们本地 full + b 关落下加成 + d 程序档升级 = 31,481，约第 8。领先者优势：A/C 在每目标科学分，B 在目标数。

## 4. 未完成的队列（恢复时按顺序跑）

队列脚本已拷到 `~/.local/share/gosim26/checkpoint-2026-10-05/run-plan4.sh`（第一段等待 req1 的逻辑已无用，删掉即可）。
每条命令前设 `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 OBSERVER_WALL_CAP_SECONDS=3600`，Python 用
`/home/steven/code/GOSIM26/.venv/bin/python -m v4.evaluation.batch ...`，同时最多 3 个评测进程（4 核）。

1. **决定性验证**（在 `../GOSIM26-integ`）：
   - `--dataset kind-validation --variants default --jobs 3 --tag kv1`（16 张新种子卡，约 3–4 小时）；
   - `--dataset kv-bd --variants struct-on --jobs 3 --tag kv1`（只跑会触发切换的 8 张 b/d 卡；a/c 卡上切换不改变任何参数，
     与 default 逐位相同，不必重跑）。用按卡 bootstrap（种子 20261005）给出均值区间。
2. `--dataset trial-kinds --variants window-05 window-05-noset --tag win1`（integ）。
3. `--dataset trial-bcd --variants reqanchor4 reqanchor8 --tag reqa1`（在 `../GOSIM26-req`）。
4. `--dataset trial-bd --variants scale-mid scale-mid-noset --tag smid1`（integ；提速后放宽规模档：候选池 96、锚点 12）。

对照结果（full 档，主工作区）：trial-a、trial-c → `v4/results/trial-kinds-r2/trial-profile/default/<卡>`；
trial-b、trial-d → `v4/results/trial-bd-r2/trial-scale/default/<卡>`。

## 5. 工具与临时文件（已备份）

`~/.local/share/gosim26/checkpoint-2026-10-05/`：
- `tools/record.py`、`tools/replay.py`（逐决策回放、比对动作并计 CPU）、`rec/{a,b,c,d}/decisions.jsonl.gz`（四张试验卡
  全部决策快照，full 档、关节奏）、`nopace.json`（`{"policy": {"pace_budget": false}}`）。用法：
  `cd <工作树> && OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=<工作树> /home/steven/code/GOSIM26/.venv/bin/python
  ~/.local/share/gosim26/checkpoint-2026-10-05/tools/replay.py ~/.local/share/gosim26/checkpoint-2026-10-05/rec/b --patch
  ~/.local/share/gosim26/checkpoint-2026-10-05/nopace.json --limit 300`，`mismatches` 必须为 0。
- 分析脚本：`loss_split.py`（失分分解）、`exposure_match.py`（曝光时长与所需时长）、`season_audit.py`（按月首拍/重拍）、
  `repeat_audit.py`；`v4/evaluation/fiber_audit.py` 已入库。
- 榜单快照：`formal-board-20261005-final/`、`formal-board-20261005T1356/`。
- `/tmp/perf/runs`、`/tmp/platzip` 等其余临时文件未备份，可丢弃；平台下载包原件在 `v4/results/platform/2026-10-04/`。

## 6. 恢复后的建议顺序

1. 重新建立两个低优先级定时任务（会话级，已删除）：每 3 小时 Sonnet 只读核对上游；每 2 小时 Sonnet 刷新正式榜
   （`v4.evaluation.board_read` 的 `_anon_key` + `observer_card_board`，phase `049d6029-343d-4d16-80d5-94b56b350301`，
   slug `v4-a`..`v4-d`）。
2. 跑第 4 节的队列 1；实验的等待与轮询交给 Sonnet 子代理，主线程只做分析。
3. 若结构切换在新种子 b/d 卡上成立：把 integ（+ 视结果加入 req 的请求锚点、放宽规模档）定为候选，
   打 full 锁定包（`v4/evaluation/package.py`，`remote_config.enabled=false`），用官方 runner 隔离验收四张试验卡，
   再与用户确认是否上传平台验证计时、何时标记最终版本。
4. 性能：perf 分支剖析热点与 PyO3 评估还没有成文报告（第二个性能代理在写报告前被停止）；如需继续，先在
   perf 工作树用回放工具测四张录制的 CPU 秒，再决定是否值得做 PyO3（平台规则：2 核、2 GB、项目解压 ≤ 100 MB、
   构建步骤可联网安装依赖）。
