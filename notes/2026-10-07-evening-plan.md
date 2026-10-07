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

## 19:14 巡检

- 压力卡计时（6c58 × formal-stress-000/002，gosim 串行/两个进程）已跑 16 分钟仍在跑；预计 19:30 前后出，之后再自动串行跑 901b4f 那两个。
- 磁盘 8.2G；子代理：algo1 同步补丁后重跑 trial-d、perf3-log 已提交第一项提速（317 测试）、perf3-core 实现第二批、richd 在 adl1 重排、logfp 继续。

## 19:37 压力卡计时（主代理亲自跑，gosim 串行）—— 决定：lean 不加安全阀

| 卡 | 包 | total | 计费秒 | CPU 秒 | 速度因子 | 结束 |
|---|---|---|---|---|---|---|
| fs-000 | 6c58 (HEAD) | 39,259.1 | **874.9** | 862.2 | 1.079 | survey_complete |
| fs-000 | 901b4f (+lean ps0.5) | 39,119.9 | 854.3 | 720.6 | 0.980 | survey_complete |
| fs-002 | 6c58 (HEAD) | 40,459.2 | **874.0** | 857.5 | 1.076 | survey_complete |
| fs-002 | 901b4f (+lean ps0.5) | 40,456.9 | 844.6 | 730.8 | 0.990 | survey_complete |

- **结论：lean/mid 不带 pace_shrink 的安全阀**。理由：6c58 在最紧的 lean 卡上自己就把计费收在 875/874（<900，survey_complete，无硬上限），说明节奏控制本身就是有效兜底；安全阀只买回 21–29 秒余量（2–3%），代价是 fs-000 掉 139 分（另一张 −2）。杠杆太弱、代价太大。
- 另：超预算属正常结束（拿部分分），不是记 0，所以"超时即灾难"的前提不成立。
- b 类的 ps0.5 保留（b-final 已验证：日志卡最大 876、0 超时、普通卡动作逐位不变）。

## 19:35 d 类（rich）收尾 —— 决定：d 类不改

- richd 子代理：10 张 d 卡成对、同机同时段。up0（去掉升级 0.5）−264.5 [−335,−194] 0/10 → 升级 0.5 确有效（10-05 的 +340 幅度夸大，真值约 +250–290）。up025 +47.1 [+2.8,+104] 但被 2 张卡主导（中位 ~+15）。science_exponent 1.8 / revisit_increment 1.0 及组合在 rich 档全在噪声内。
- 档位边界安全：151+16 张卡的 fiber_hours 只有 0.356/0.504/0.843/9.613 四个离散值，无卡落在 rich 阈值 4.0 ±0.5 内。
- d 卡科学分 32,322/32,760 = 98.7%，已饱和，无大杠杆。
- 净推荐：**d 类什么都不改**（保持 upgrade_weight 0.5）。
- 副产物：richd 修了 generate.py 对已存在 published 卡的复用（否则整批 FileNotFoundError），commit 5503c56。

## 20:28 用户落地；收线与再派
- 正式榜：CCB 32,441.4 第 7（未变）；榜首 Pleiades 33,675.5。
- 收到结论：
  - logfp 39766b6（logrules/logbook）：真日志重放失效时数 325.7h→31.1h（≈+2400 分量级），但合成 kind-v6l 端到端 −234（005 卡由 pacing 抖动引起）；逐位验收只完成一半。
  - perf3-core d11aba6（bound.py/durations/planning）：决策级 sha 全同，CPU 0.72–0.91；整卡对照的 gosim 进程挂了（日志 0 字节）。
  - algo1：只有 ov25（lean overexposure_gamma 0.25）筛查集 +34.3 [+2.9,+63.9]，CPU ×1.17，未确认。
  - perf3-log 关键发现：pacing 读 remaining_seconds = 计费 CPU÷速度因子 → 提速会改变 pacing 时刻，决策级逐位≠整卡逐位。
- 20:25 再派：
  1. Sonnet「终包合并与整卡验收」：claude HEAD + perf3-core + logfp，四臂 × trial-a..d 及 add_duty_log 造的 a1..d1 日志卡，整卡配对。
  2. Sonnet「ov25 确认集」：big-ac48v 48 卡配对。
  3. algo1（Opus）转做 _track/terrain 缓存（C1 型卡 2.8× CPU），只加新文件，50 分钟。
  4. perf3-log 要求收尾报告。
- 预计 21:30 前定终包，22:00 前交用户上传；23:00 查默认最终版本指向。

## 20:35 perf3-log 收尾（8280afa，可合入）
- 24 对整卡、三台主机：a/c/d 三类 action_sha256 **全同**（Intel+AMD 都过）；b 类在 2/3 主机与基线不等。
- b 类差异已归因：**基线自身跨主机就不同**（r3-001 决策 9829/9811/9837），因 pacing 读 remaining_seconds = 计费 CPU÷速度因子，b 卡压在预算边界。new 在固定虚拟钟下逐位可复现（9837/5a967a89b2a8）。
- **判据调整**：凡是省 CPU 的改动都必然改变 b 类 pacing 时刻 → b 类"真实主机逐位一致"门槛不可达，不作为否决条件。改为要求整卡同机同窗口配对 + 每卡计费与终止原因（<900 s、非 wallclock_expired）。
- CPU 倍率 new/base：a 0.962、c 0.964、b 0.938、d 0.854，合计 0.949。无一对 ≥900（最大 859.2 s adl3 base），48 次终止全为 survey_complete。
- 改动文件：terrain.py（新增 mod360/wrapped_offset）、quality.py（4 处方位角回绕）、tests/test_mod360.py、experiments/datasets.json（kind-v6t）。
- **终包合并集共三件（文件互不相交）**：perf3-core d11aba6（bound.py/durations.py/planning.py）+ logfp 39766b6（logrules.py/logbook.py）+ perf3-log 8280afa（terrain.py/quality.py）。terrain.py 归 perf3-log，algo1 只碰 planning.py 的 _track。

## 20:58 perf3-log b 类闭环（8280afa 四类全过）
- 固定虚拟钟（每回合固定计费）跑 kind-v6l-r3-001：gosim(AMD) 与 adl3(Intel) 上 new = base = 5a967a89b2a8382a、9837 决策。b 类差异纯属基线速度非确定。
- 8280afa 结论：a/c/d 真实主机两类 CPU 逐位、b 固定钟两类 CPU 逐位 → **合入**。perf3-log 线结束。
- 已把固定虚拟钟作为判据交给合并验收线，用来给 d11aba6（perf3-core）做代码等价性定性：固定钟下 sha 全同 = 代码等价，真实主机 total 变化即为省 CPU 的节奏收益；不同 = 真行为改变，不合入。
- 时间紧时验收砍到两臂：6c58 旧码 vs 三件全合。

## 21:10 上游规则重大更新（时间线重排）
来源：hackathon-survey26 origin/main #396–#404（10-07 18:57–21:02），rules.zh.md 第 3 节时间线与第 5 条。
- 对外截止仍是 **10-07 23:59:59（北京）**；之后为**截止后送测窗口，至 10-08 09:00（北京；UTC 01:00）**。
- 窗口内每队最多再提交 **15 个版本**；评测照常、每日额度不变（UTC 0 点 = 北京 08:00 重置）。
- 送测版本（最终版本）**可从本队任意已确认版本中选**（含截止前任何版本）；**10-08 09:00 后锁定**。未选则默认正式赛最高分评测的版本。
- 新增「选定冻结」（#402）：队长可提前冻结已选最终版本，冻结后不可改。**我们不用**，保留到 09:00 的调整空间。
- 01:00 UTC 只锁定/冻结/关闭正式赛，**不自动启动 E–H**；决赛时间另行公告。
- 第 13 条明文建议"避免依赖运行速度或机器性能的策略"——对应我们 pacing 读计费 CPU 的速度非确定性；决赛每卡 3 次同时跑在全新托管机上。
- **影响**：从约 3 h 延到约 12 h。21:30 定终包的硬约束解除；改为「先上传已验收的保底包，再在窗口内迭代」。上传配额 15 版，按需使用，不浪费。

## 21:20 用户指令：目标至少前三；利用到 10-08 09:00 的窗口
- 新派 Opus ×2（与 algo1 地形缓存合计 Opus 3/4）：
  - **bx**（worktree GOSIM26-bx）：b 类 lossF/unobs，筛 scr-b16、确认 big-b24v。
  - **acx**（worktree GOSIM26-acx）：a/c 类 superseded+overexposed 与 QSO 欠曝，筛 scr-ac32、确认 big-ac48v + formal-stress 计时。
  - 两者每 60–90 min 进度，最晚 10-08 05:30 交结论。
- 在跑 Sonnet：终包三件合并+整卡验收、ov25 确认集。
- 节奏：先上传「三件合并」验收通过的包作保底（替换 df7f 默认），再按算法线结论迭代；15 版额度留足。09:00 前最后一次上传须留出一次完整在线评测（约 1 h）的时间 → **最后上传 ≤ 07:30**。

## 21:45 保底包 05f4 验收完成；候选 1945 在验
- **05f4**（`v4/dist/observer4-full-locked-05f417bfd8b9.zip`，claude 958ad80 = 6c58 + perf3-core + perf3-log + logfp）：gosim 官方 runner a/b/c/d = 26,480.7 / 42,762.3 / 27,856.1 / 32,974.8，**与 6c58 逐分相同**（A–D 均分 32,518.5，对线上 df7f +77.1）；计费 710.9 / 607.6 / 715.5 / 148.5 s，全部 survey_complete。327 测试过、ruff 干净。
- 合并验收线（Sonnet）结论：perf3-core 八卡逐位一致可合；logfp 在本地合成日志卡上零差异（合成卡不含它修的句式），收益要靠平台 A1–C1 真日志看。A1–D1 不影响默认最终版本的选取，所以一起上传无 A–D 风险。
- **1945**（GOSIM26-final 分支 final-cand 8d74b0d = 05f4 + mid science_exponent 1.8 + revisit_increment 1.0；big-b24v 已确认 +54 [+26,+86]）：gosim 在验 trial-b 与本地造的 trial-b1。
- ov25/cg50（a/c，Sonnet 确认线）28/48 卡中期：ov25 +85.2 [下界 +62]、24/4，CPU ×1.16；cg50 +78.8 [下界 +50]、23/5，CPU ×1.03。在补叠加变体与 C1 型计时（rebase 到 958ad80）。
- 截止前上传不占截止后 15 版额度。
- 资源：gosim 64 物理核、负载 12，放宽到每线 ≤20；adl/epyc 超载，每线 ≤6。
