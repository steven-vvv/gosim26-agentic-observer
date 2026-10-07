# 10-07 傍晚现场（16:40 北京）——压缩/重启后先读这里

- 用户 17:45–21:00 飞行，21:00 后才能上传。默认最终版本 = A–D 均分最高的完整评测（目前 df7f 平台 61ba6d6e：26,419 / 42,543 / 27,805 / 32,999，均分 32,441）。不必手动标记；23:00 核对默认指向。
- claude HEAD f4422e8 = df7f + perf2（9a82763、403c309 逐位一致）+ pace_shrink 代码（f437416）+ revisit_increment 代码（11de206）+ 配置：lean 加 science_exponent 1.8、revisit_increment 1.0；mid 改 48/512/24/6 + pace_shrink 0.5。
- 中间包 v4/dist/observer4-full-locked-6c581ba82dbe.zip，验收中：adl1（trial-a/c）、adl4（trial-b/d），日志 $CLAUDE_JOB_DIR/tmp/v-6c58-*.log。对照 df7f 本地验收 26,367.9 / 42,506.1 / 27,805.0 / 32,974.8；预期 a +96、b +227、c +108、d 0。
- 子代理（Opus）：
  - b-final（GOSIM26-bfinal，分支 b-final）：19:00 前给最终 b 推荐 + formal-stress 中带卡计时。
  - ac-opus（GOSIM26-ac）：19:00 前给 b 上 w48c+x18+ri 叠加结论与 c 日志卡计时余量。
  - algo1（GOSIM26-algo1）：a/c 曝光时长-目标匹配算法，20:30 前结论。
  - algo2（GOSIM26-algo2）：b 类空闲光纤算法，20:30 前结论。
- 17:00 用户决定：6c58 不急着登机前上传（21:00 前后反正还要再传一版），先继续优化，且性能优化必须做。
- 子代理（Sonnet，17:00 起，20:00 前交付逐位一致提速；基线 worktree GOSIM26-perf3base = detached fea7834）：
  - perf3-log（GOSIM26-perf3l，分支 perf3-log）：值班日志卡每决策 CPU（平台 C1 161 ms 对 C 55 ms）——只动策略/日志层。
  - perf3-core（GOSIM26-perf3c，分支 perf3-core）：规划器核心（mid 48/512、lean 72/768、valuation.science_gains）——只动规划器文件。
  - 合入方式：逐位一致的提交直接 cherry-pick 到 claude，不改变确认集结论；终包前再跑一次单测与四卡验收。
- 超级榜只取 8 卡都完成的那次评测（我们那行仍是 10-06 的 b3fbb73 全负那次）。
- 20:45 定终包 → 四卡验收（约 15–25 分钟）→ 21:00 交用户上传。
- 全知校准：读真值天气 A–D 为 26,346 / 42,337 / 27,499 / 33,335，差距是规划本身不是信息。

## 17:05 更新（用户已登机，约 21:00 落地）

- 用户 17:45 起飞前最后一句：21:00 前后反正要再传一版，6c58 不必赶登机前上传；继续优化；性能优化必须做；并要求做会话保活措施（已建 session-only 定时巡检 13/37/58 分，7 天后自动过期）。
- 6c58 验收（trial-a 已出）：**26,480.7**（df7f 本地 26,367.9，+112.8；平台 26,419.2）；计费 564.9/900（df7f 平台 666.7）；CPU 1,046.5 秒；speed_factor 2.42（本次机器负载轻，属于快机样本）。trial-c 已完成待取，b/d 在 adl4 待出。
- 已开两个 Sonnet 提速子代理（任务 #17 perf3-log、#18 perf3-core），20:00 前交付逐位一致提速。
- 会话保活：主代理不得主动退出；崩溃后从 notes/2026-10-07-evening-plan.md + 子代理 transcript 恢复。

## 17:28 巡检

- 6c58 验收：a 26,480.7（+112.8）、c 27,856.1（+51.1）已出；b/d 在 adl4 上被 1,500 秒超时杀掉（adl4 负载 102/32、epyc 157/32，两个算法子代理压满），已改 gosim 重跑（负载约 5–30/128），约 17:50 出。
- 已要求 algo1b/algo2 把 adl4 降到 ≤12、epyc ≤8 槽位，新批次走 gosim。
- a/c 子代理三方计费（adl1 同机同段，trial-c）：164732 450.6 s、df7f 523.0 s、候选（df7f+x18+ri）513.8 s。**注意：df7f 在 c 卡上比 164732 慢 ×1.16**，与"perf-r2 让 a/c 规划 ×0.80"的预期不符；若平台 C1 仍按 164732 的 878 s 计，候选约 ×1.14 → 可能超 900。19:00 要它的 kind-v6l c 日志卡实测来定，必要时给 very_lean 开 pace_shrink 0.5 做安全阀。
- 子代理：8 个（2 Opus 调参 + 2 Opus 算法 + 2 Sonnet 提速 perf3 + 1 Sonnet d 类 + 1 Sonnet 日志误报）。

## 17:45 中间包 6c58 四卡验收完成（gosim b/d、adl1 a/c）

| 卡 | 6c58 | df7f 本地 | 差 | 计费秒 | CPU 秒（df7f） |
|---|---|---|---|---|---|
| a | 26,480.7 | 26,367.9 | +112.8 | 564.9 | 1046.5（676.3） |
| b | 42,762.3 | 42,506.1 | +256.3 | 626.3 | 542.0（565.7） |
| c | 27,856.1 | 27,805.0 | +51.1 | 444.4 | 1093.6（707.0） |
| d | 32,974.8 | 32,974.8 | 0.0 | 139.8 | 122.6（317.6） |

- 均分 32,518.5 对 df7f 32,413.5，**+105.0**；四张计费秒都远低于 900，无 hard cap。
- **CPU 秒跨主机不可比**：d 卡配置未变却从 318 掉到 123，说明两次验收不在同一台机器（CPU 秒含主机主频差）。只用同机同段的成对数字（a/c 子代理那组）。
- 已上传的 df7f 平台成绩（61ba6d6e）是当前默认最终版本；6c58 作为回退候选。

## 17:45 外部风险与缓解

- Cloudflare D1：用户已升级 Workers Paid（此前 D1 到上限）。当前 D1 89.8 MB、24 h 写入 105,870 行，写入量前列均为 observer-v4 的 decision_trace/decision/night_plan/logbook_quiet；付费后配额充裕，暂时不动保留策略（用户："先这样凑合着跑，反正就一天，账单问题应该不大"）。若再逼近上限，二线缓解：TELEMETRY_RETENTION_DAYS 30→3，或裁剪 high-volume event_type（decision_trace 19,479 行/21 MB），或关掉事件类型白名单中最吵的几类。
- 阿里云节点是竞价实例，可能被释放：终包构建与四卡验收不要只依赖该节点；gosim（128 核）与 adl1/adl3/adl4 为主力，验收可随时改投 gosim（b/d 就是这么救回来的）。

## 17:43 巡检

- 主机负载（32 核除 gosim 128）：adl1 46、adl3 47、adl4 75、epyc 52、gosim 56 —— 已回到不超额的水平，adl4/epyc 的降并发要求生效。
- 子代理按 19:00 / 20:00 / 20:30 三个节点推进中；b/d 验收（gosim）已完成并入 6c58 表。
- 无新事项。

## 17:53 b-final 最终结论（Opus，已收工）

- 维持 claude 上 f4422e8 的 mid_overrides（w48c + pace_shrink 0.5，不带 anchor_bound），无需改动。
- 普通 b（big-b24v 24 张）+227 [+188,+264]，charged 均值 634、最大 683；守卫不触发，动作与 w48c 逐位同。
- B1 型（kind-v6l b 日志卡 12 张）：ps0.5 +48（5/12 胜），charged 最大 876，0 expired、2 张应急档；不加守卫 +183 但 3 张应急、最大 883；w64 3/4 expired（禁用）。
- formal-stress 001/003（mid 带）ps0.5 −11，持平；000/002 落 lean 带。
- anchor_bound（w48cxs）在 B1 型 +221 但样本小、需再合 bound.py，终包不带。

## 17:58 巡检

- algo2 中期：b 空闲光纤是结构性的（91% 空闲格内无目标，可分未分仅 1.5%），extend_rate 无稳正，score_marginal 线性 −900 排除；已转向"完成度缺口/时间分配"（20:30 定论）。
- 过载：epyc 121/32（logfp 20 进程）、adl4 73/32（logfp 20 + algo2/algo2b 26）；已分别要求降到 ≤8 / ≤10，新批次派 gosim（负载约 15/128）。各代理各自 dispatch，pool_hosts.json 槽位按单代理设计，多代理并发时会叠加超额——终包验收只用 gosim 并先查负载。
- a/c：trial-c 三方计费在 adl3 同机并跑，19:00 前出。perf3-log 在做 kind-v6/kind-v6l 孪生卡剖析；perf3-core 拿到热点表。

## 18:05 终包候选清单（20:45 决策用）

- 已在 claude：a/c x18+ri（lean）、b w48c+ps0.5（mid）——6c58 验收 +105。
- **候选 B1：b 类叠 x18+ri**（mid_overrides 加 science_exponent 1.8 替换 1.65 + revisit_increment 1.0）。a/c 子代理：big-b24v 前 20/24 +51 [+26,+77] 15/20 胜，CPU ×1.08；kind-v6l b 日志卡 4 张 +13（噪声），CPU ×1.00。等 24/24 与 B1 型计时后决定；**判据：区间下限 > 0 且 B1 型最大 charged 不超 880**。
- 候选 B2：b 类 finish_bonus（algo2，19:00 筛选结果；要在 w48c+x18+ri 之上配对证明可叠加）。
- 候选 D：rich 档参数（Sonnet richd，19:30）。
- 候选 L：值班日志漏报/迟报修复（Sonnet logfp，20:00）。
- 候选 P：perf3-log / perf3-core 逐位一致提速（20:00），只要 action_sha256 全等即可直接合。
- 安全阀：lean 带 pace_shrink 0.5（视 formal-stress 000/002 与 C1 计时结果而定）。

## 18:13 巡检

- a/c 子代理已构建 901b4f（= HEAD + lean/mid 均开 pace_shrink 0.5 的候选安全阀包），在 gosim 同机并跑 df7f / 6c58 / 901b4f × formal-stress-000/002 三组计时对比，19:00 前一并交。
- algo2：阈值 0.7 明显优于 0.5，正在试更温和的一档。
- 主机：adl1 44、adl3 42、adl4 78、epyc 55、gosim 76（32/32/32/32/128）。gosim 因为被 a/c 的三组验收 + algo 队列同时使用而升到 76，仍不超额；20:45 终包验收前要再确认 gosim 空闲。

## 18:20 本机磁盘满（ENOSPC）处置

- /dev/vda4 69G 100% 满，logfp 子代理报告 Bash 无法写输出；主代理自己的 Bash 也一度全部失败。
- 清理后 7.7G 可用：删除已收工 worktree（regress、perf、perf-r2、perf2、perf2base、perf2ft、fix2、season、struct、rel、req、bfinal；fix/integ/bopus 有未提交改动或被锁，保留），/tmp 下旧打包与验收目录、/tmp/research、/tmp/xm，以及 /tmp/ac3 下 a/c 子代理的临时 worktree（zp/head/headps）和一个 6a1ce7133042 临时包——已通知 a/c 子代理如缺失告诉我重建。
- algo1、algo2 两个 Opus 子代理因写盘失败停止，已通过 SendMessage 自动恢复。其余子代理已通知重试。
- 防再发：要求所有子代理 pull 只拉 summary/score_report/workflow_result，临时产物用完即删；巡检加查 df（< 3G 即清理 v4/results/research 5.3G 中最旧的批次）。
- 保留：v4/dist 下 6c58 与 df7f 两个包完好。

## 18:53 巡检

- **algo2（b 空闲光纤，Opus）收工**：16 个变体在 scr-b16 上无一区间下限 > 0，全部不合入；证据表 GOSIM26-algo2 分支 558dc50 的 v4/experiments/algo2-final.json。空闲光纤是结构性的（91% 空闲格无目标），完成度缺口上限约 760 分/卡（且需扣时间）。
- **ENOSPC 余波**：本机所有 pool dispatch 进程在 18:20 前后退出，五台主机上已没有我们的 batch/run_local 进程。adl4 93、epyc 131 的负载来自同宿主其他租户，不是我们的。已通知 a/c、algo1、richd、perf3 重启各自 dispatch，优先派 gosim（负载 0）。
- a/c 子代理：6 个 formal-stress 验收在 gosim 并跑撞上验收器 1500 s 超时，全被杀；已要求每台 ≤2 并发、只保留 6c58/901b4f × 000/002，19:00 先交已有数据。
- 磁盘 7.7G 可用，稳定。

## 19:00 a/c 线收尾（Opus，已收工）

- 推荐项 = 已合入的 f4422e8（lean 加 science_exponent 1.8 + revisit_increment 1.0）。确认集 big-ac48v：a +96 [+75,+117] 23/24 胜、c +108 [+84,+135] 24/24 胜，CPU ×1.05/1.06。
- **a 类可选优化**：revisit_increment 对 a 无增益（−13，CI 跨 0）却多花 7% CPU；可把它移到 very_lean_overrides（只作用 c）。按数据换算 a 约 +108、c 约 +108，每卡动作与已测变体相同。**20:45 决策项**：要不要为省 a 类 CPU 做这个拆分。
- b 类叠 x18+ri（在 w48c 上）：big-b24v +54 [+26,+86]，CPU ×1.07；b 日志卡 8 张 +105 [−24,+241]。**候选 B1 仍有正下限**。
- c 加宽 cw72/cw60 不推荐（−64 / −1，CPU ×1.22）。
- trial-c 官方 runner 同机三方：164732 450.6 s、df7f 523.0 s、候选 513.8 s；推荐配置对 df7f 不增计费、对 164732 +14%。
- 两个包在 /tmp/ac3/dist（6c58 与 901b4f），901b4f = HEAD + lean/mid 都开 pace_shrink 0.5。
- **主代理接手压力卡计时**：gosim 空闲，串行跑 6c58 与 901b4f × formal-stress-r3-000/002（脚本 $CLAUDE_JOB_DIR/tmp/wait_fs.sh，日志 v-fs-6c58.log）。
