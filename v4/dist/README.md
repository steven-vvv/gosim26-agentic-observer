# 提交产物

本目录保存 v4 的可交付包；除本说明外的构建产物不进入版本库。包中仅包含运行实现、明确允许的配置和固定运行依赖。

当前候选包（2026-10-06）：

- `observer4-full-locked-b3fbb73baeb4.zip`（32,078,588 字节，26 个文件）
- 源码：分支 `rel` 提交 `5a55c4d`，`python -m v4.evaluation.package --profile full --locked-full --wheels /tmp/wheels`
- profile：`full`；云控关闭并锁定（`remote_config.enabled=false`, `locked=true`），启动不会请求远端覆盖
- 相对 10-04 的 full 包：开启结构切换（`structure.enabled`），过期水平取近 3 次中位、长期下限 1.0、年龄上限 36 小时
- 官方本地 runner 验收（Python 3.12、全新 venv、包内 wheel）：试验卡 a/b/c/d（= 正式 A–D 同种子）
  25,586.86 / 41,594.41 / 26,898.47 / 33,039.21，全部 `survey_complete`，计费 175 / 168 / 247 / 169 秒，
  与本地配对验证逐位一致
- SHA-256：`b3fbb73baeb47e4e4446d89e4a8cea2c2dee6ff624d601e2ac6f0e02e19163a5`

保留的上一版包：

- `observer4-f7a2a2cf4bf4.zip`
- profile：`formal_conservative`；10-04 平台正式评测用的就是它（远程切换 full 未发起），不能作为最终版本
- SHA-256：`f7a2a2cf4bf45cd1506a8f11612ed74c2ebfa0cf890796d07fe026bbf7f6d087`

`observer4-full-locked-d2c64f69fd5e.zip`（10-04 的 full 包）已被上面的候选包取代。历史 observer/replay 包不作为当前提交包使用。
