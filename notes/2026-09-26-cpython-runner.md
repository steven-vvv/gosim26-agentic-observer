# CPython 本地评测提速（2026-09-26）

`tools/evaluate.py` 默认改用当前 CPython。实测为 CPython 3.14.7，JIT 关闭、GIL 开启。
官方 `kit/` 文件不变；`tools/fast_runner.py` 仍在加载前校验 kit SHA-256。

## 改动

- 用 NumPy 批算某时刻全部天区的高度角、方位角、时角、airmass、月角距和月光质量因子。
  窗口生成与决策快照仍执行官方逻辑，只让其几何调用读取批算结果。缺少 NumPy 时退回原缓存路径；
  `FAST_RUNNER_VECTOR=0` 可单独关闭批算。
- 安装 `orjson` 开发依赖，只加速本地 agent 协议的快照 JSON 编码。缺少依赖时退回标准库；
  `FAST_RUNNER_ORJSON=0` 可单独关闭。官方协议写入、读取和超时处理保持原样。
- `evaluate.py` 的结果缓存按 runner 和解释器名称区分，避免直接复用旧 PyPy 结果。
- agent 在安装了 `orjson` 的环境中用它解析大快照；平台没有该包时继续用标准库解析。

## 验证与速度

- 27 项单元测试通过；几何测试覆盖 dev-reference 三个时刻的全部天区，检查数值误差和最低高度角分类。
- dev-reference 使用 `evaluate.py --verify` 对官方 runner + CPython 的 `decisions.csv` 和
  `score_report.json` 逐字节相同。
- comp-a-s101 的完整运行与此前 CPython + 缓存 runner 的两个文件逐字节相同：
  **182721.350854 分、1403 次动作、1166 块完成天区**。
- 同机空闲、同一解释器的单场墙钟：原缓存 runner 约 238 秒；批算 + orjson 约 226 秒
  （约快 5%）。新运行的 agent 规划累计约 112 秒，已是主要耗时。该数值仅代表这台机器和这场景。
- 可选的 agent `orjson` 解析在同一场的得分与决策文件也逐字节相同；并行评测负载下墙钟约
  210 秒，不能单独把与空闲场的差值归因于解析器。

本地加速路径仍属于评测工具，提交包只包含 agent，不依赖其补丁。kit 更新时必须重新核对补丁和验证。
