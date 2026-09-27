# 结果归档

这里保存平台下载物和完整项目运行物，不把它们当作版本输入。新的归档按日期和尝试编号放置：

```text
result_temp/
  submissions/YYYY-MM-DD/attempt-NN/  # zip、配置和该次尝试的原始文件
  logs/YYYY-MM-DD/                    # execute.log、telemetry.log 等独立日志
  evaluations/YYYY-MM-DD/<experiment>/ # 可再生评测 JSON，保留原实验层次
```

`attempt-01` 是迁移前根目录下的第一组文件，原来的数字目录按数字保留为对应尝试编号。每次迁移会生成被忽略的 `manifest.json` 和 `migration-plan.json`，其中记录源路径、大小、修改时间和 SHA-256；新增文件直接放入下一个 `attempt-NN`，不要覆盖已有尝试。迁移工具是 `tools/organize_workspace.py`，默认只预览，确认后使用 `--apply`。

原仓库 `results/` 下的评测 JSON 也按日期迁移到 `evaluations/`，实验名和场景文件名保持不变。运行脚本仍可把新输出写入被忽略的 `results/`，确认后再用迁移工具归档。
