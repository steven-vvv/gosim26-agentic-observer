# 提交产物

本目录保存 v4 的可交付包；除本说明外的构建产物不进入版本库。包中仅包含运行实现、明确允许的配置和固定运行依赖。
顶层只放当前候选包，被取代的包按构建日期移入 `archive/<日期>/`。

当前候选包（2026-10-07，用户已上传）：

- `observer4-full-locked-164732f33258.zip`（32,097,603 字节，28 个文件）
- 源码：分支 `claude` 提交 `7f1032b`，`python -m v4.evaluation.package --locked-full`
- profile：`full`；云控关闭并锁定，启动不会请求远端覆盖
- 相对 10-06 的 b3fbb73 包：值班日志 v2（预约/取消/安静窗）+ 规则读取器兜底与双读者分层报修、死机规则、
  默认宽搜索（a/c 类）+ 落下加成 0（规模档 b/d 仍为 0.35）
- 官方本地 runner 验收（gosim，`pool verify`）：试验卡 a/b/c/d（= 正式 A–D 同种子）
  26,204.37 / 41,882.96 / 27,655.72 / 32,974.84，全部 `survey_complete`，计费 441 / 204 / 631 / 180 秒
- 平台在线评测 c66e4c73（10-07）：A–D 26,125.7 / 41,906.9 / 27,655.7 / 32,998.5，
  A1–C1 35,793.3 / 52,880.1 / 27,352.4（C1 计费 877.9 / 900 秒），详见 `v4/results/platform/2026-10-07/attempt-04-final/`
- SHA-256：`164732f33258f4701b7166d2f72f0a1f9fe29804fdfd43592ec3966e19d4a989`

归档（`archive/`，均已被取代，不作为提交包使用）：

- `2026-10-07/observer4-full-locked-6b13acc7e09c.zip`：日志 v2 首版，被 164732 取代
- `2026-10-06/observer4-full-locked-{b3fbb73baeb4,aa787a728feb,829694ca66cd}.zip`：结构切换、模型读值班日志等中间版本
- `2026-10-05/observer4-full-locked-d2c64f69fd5e.zip`：10-04/05 的 full 包
- `2026-10-04/observer4-f7a2a2cf4bf4.zip`：`formal_conservative` 档，10-04 平台正式评测所用，不能作为最终版本
- `2026-10-04/` 其余：更早的 observer/replay 包
