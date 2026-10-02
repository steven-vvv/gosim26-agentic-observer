# 打包方案核查（2026-09-27）

当前完整项目的权威入口是 `v3/tools/pack_project.py`。它生成根目录带
`observer.project.json` 的 ZIP，源码、模型配置、遥测配置和入口都在包内；密钥只从平台环境变量读取，
不进入源码、manifest 或 ZIP。NumPy wheel 是可选层：同时放入 x86_64/aarch64 wheel，构建阶段安装到
`.deps`；安装失败仍走结果相同的纯 Python 路径。

本次核查的实际产物：

- 无 wheel、关闭遥测：12 个文件，56,942 bytes，SHA-256
  `242e745ac7b8c85abcf873ee72e453ed00bb18d3eb9f4622edf434017e1331e6`。
- 带 `/tmp/wheels` 的双架构包：14 个文件，32,045,179 bytes（约 30.6 MiB），SHA-256
  `57db1f2d0349050839867b0ce75abb11346e04a1e717a73be81facfb930f2a67`。
- 两个包都通过官方 `project_platform.package.read_project_zip` 和 `ProjectManifest.parse`，ZIP 条目可读且按路径排序。

这次对打包器做了几项边界收紧：

- 生成前复核官方 manifest 字段、镜像引用、命令参数、工作目录、环境变量和 64 KiB manifest 上限。
- 复核相对路径、`.git`/`.env`、符号链接、大小写冲突、文件数量（10,000）和展开体积（100 MiB），并保留 50 MiB ZIP 上限。
- 遥测地址只接受不带凭据、查询参数和片段的 HTTP(S) URL，避免把误传的 token 放入配置。
- 所有文件先写入临时目录并完成编译、清单和 ZIP 校验，再替换旧目录与 ZIP；失败时保留上一份可用产物。
- manifest 固定 `PYTHONDONTWRITEBYTECODE=1`，避免平台运行时制造无意义的缓存文件。

仍然保留的有意取舍：`requirements.txt` 不会被隐式安装；正式包依赖 manifest build 和可选 wheel，缺依赖时明确回退纯 Python。
`v3/tools/build_portfolio.py` 仍是旧版扁平变体组合器，不含完整项目 manifest/遥测，不能代替正式完整项目打包；后续若重新启用组合提交，
应先把每个变体转换为完整项目并分别跑本地平台预检。`--public-schedules` 只适用于公开场景指纹命中，正式未知场景不能带入真实排程。

复核命令：

```bash
uv run pytest -q v3/tests/test_pack_project.py
uv run ruff check v3/tools/pack_project.py v3/tests/test_pack_project.py
uv run python v3/tools/pack_project.py --agent v3/agents/observer --wheels /path/to/numpy-wheels
```
