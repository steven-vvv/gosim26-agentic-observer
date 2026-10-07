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
