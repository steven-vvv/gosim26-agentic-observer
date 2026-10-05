# 提交产物

本目录保存 v4 的可交付包；除本说明外的构建产物不进入版本库。包中仅包含运行实现、明确允许的配置和固定运行依赖。

当前候选包（2026-10-04）：

- `observer4-full-locked-d2c64f69fd5e.zip`
- profile：`full`
- 云控：关闭并锁定（`remote_config.enabled=false`, `locked=true`），启动不会请求远端覆盖
- SHA-256：`d2c64f69fd5e97912adca2e558154ed1da73217d8964b6521b84a7de977c0b0b`

保留的上一版包：

- `observer4-f7a2a2cf4bf4.zip`
- profile：`formal_conservative`；该包使用旧的认证回退路径，不能作为当前 full 切换候选
- SHA-256：`f7a2a2cf4bf45cd1506a8f11612ed74c2ebfa0cf890796d07fe026bbf7f6d087`

历史 observer/replay 包不作为当前提交包使用；本目录只保留上述候选包和上一版包。
