# 10-07 傍晚现场（16:40 北京）——压缩/重启后先读这里

- 用户 17:45–21:00 飞行，21:00 后才能上传。默认最终版本 = A–D 均分最高的完整评测（目前 df7f 平台 61ba6d6e：26,419 / 42,543 / 27,805 / 32,999，均分 32,441）。不必手动标记；23:00 核对默认指向。
- claude HEAD f4422e8 = df7f + perf2（9a82763、403c309 逐位一致）+ pace_shrink 代码（f437416）+ revisit_increment 代码（11de206）+ 配置：lean 加 science_exponent 1.8、revisit_increment 1.0；mid 改 48/512/24/6 + pace_shrink 0.5。
- 中间包 v4/dist/observer4-full-locked-6c581ba82dbe.zip，验收中：adl1（trial-a/c）、adl4（trial-b/d），日志 $CLAUDE_JOB_DIR/tmp/v-6c58-*.log。对照 df7f 本地验收 26,367.9 / 42,506.1 / 27,805.0 / 32,974.8；预期 a +96、b +227、c +108、d 0。
- 子代理（Opus）：
  - b-final（GOSIM26-bfinal，分支 b-final）：19:00 前给最终 b 推荐 + formal-stress 中带卡计时。
  - ac-opus（GOSIM26-ac）：19:00 前给 b 上 w48c+x18+ri 叠加结论与 c 日志卡计时余量。
  - algo1（GOSIM26-algo1）：a/c 曝光时长-目标匹配算法，20:30 前结论。
  - algo2（GOSIM26-algo2）：b 类空闲光纤算法，20:30 前结论。
- 20:45 定终包 → 四卡验收（约 15–25 分钟）→ 21:00 交用户上传。
- 全知校准：读真值天气 A–D 为 26,346 / 42,337 / 27,499 / 33,335，差距是规划本身不是信息。
