# 工作区整理约定

当前工作副本按“可复现输入”和“可再生输出”分开管理：

| 目录 | 约定 |
| --- | --- |
| `agents/observer/` | 当前参赛入口和确定性策略；`llm.config.json` 只放非机密模型顾问限制。 |
| `agents/obs*`、`agents/exp-*` | 冻结实验代码和参数快照。保留原路径，避免旧评测命令失效；新增实验优先写入 `notes/` 并在此处登记。 |
| `result_temp/evaluations/` | 按日期保存评测 JSON，保留实验/场景相对路径；源 `results/` 目录可随时重建，不进入 Git。 |
| `runs/` | 运行脚本、分析代码、报告仍可跟踪；JSON、TXT、日志、stderr 和完成标记视为可再生输出，不进入 Git。 |
| `scenarios/` | `comp-*` 被忽略；MC 场景当前仍保留，因为它们是小型、可审计的测试输入。 |
| `result_temp/` | 平台结果按日期/尝试归档，只跟踪本说明；迁移 manifest 保留完整校验信息但不进 Git。 |
| `telemetry-worker/` | Worker 源码、迁移和测试进入 Git；本地 Wrangler 状态、依赖和密钥不进入 Git。 |

`result_temp` 和 `/tmp` 的本轮整理由 `tools/organize_workspace.py` 执行。它默认 dry-run；`--apply` 会逐项复制、校验 SHA-256 后才移除源文件。`/tmp` 只归档明确的 GOSIM26 实验和构建物，系统目录、Node/编辑器缓存以及其他项目工作区不动。归档位于 `/home/steven/.local/share/gosim26/archive/tmp/YYYY-MM-DD/`，不占用仓库历史。

今后的评测建议使用以下边界：

```text
results/<agent>/<scenario>.json       # 临时精简结果，可随时重跑
runs/<experiment>/                    # 脚本、参数、报告；大输出留在被忽略文件
result_temp/submissions/<date>/...    # 平台下载包和原始日志
result_temp/evaluations/<date>/...    # 已归档的评测 JSON
```

这样既保留实验推理链，也避免把数百个输出文件当作代码版本维护。
