# 模拟与评测性能（2026-09-24）

## 剖析

py-spy 采样（`--subprocesses`，汇总脚本 `tools/pyspy_summary.py`），obs10 × comp-a-s101，官方 runner + CPython 3.14。
runner 与 agent 一问一答、同步交替，墙钟时间 ≈ 两边 CPU 之和，任何一侧省下的时间都直接缩短评测。

| 部分 | CPU | 占比 |
|---|---|---|
| agent 规划器 `replan`（纯 Python 次梯度三重循环：150 次迭代 × 约 1300 块天区 × 约 75 个时段） | 528 秒 | 58% |
| agent 其余（rollout、候选选择、快照 JSON 解析） | 79 秒 | 9% |
| runner 生成决策快照（候选天区几何、有效天气、当夜窗口时刻解析、JSON 编码） | 222 秒 | 24% |
| runner 其余（当夜/每周发布的窗口、等待检查、写文件、最后的重放评分） | 87 秒 | 10% |

runner 的开销里有很多“同一时刻对所有天区都相同”却按天区重算的量：太阳/月亮位置与月亮高度
（每次还用 `dataclasses.replace` 造一个“月亮天区”）、按时间生效的天气事件、时隙结束时刻、时刻格式化；
每次决策还把当夜约 1600 个窗口的起止时刻各 `fromisoformat` 解析一遍（一场约 3800 万次）。

## 改动（全部与原结果逐位 / 逐字节相同）

1. **离线试验台 `tools/plan_experiment.py`**：`dual_prices` 只在 [t0 所在时段, 视野末所在时段] 的窗口内、
   对窗口内有正价值的天区计算；与 t0 无关的逐格价值按信息设定缓存；`np.add.at` 换成 `np.bincount`。
   窗口外价值恒为 0、无正价值天区的约化收益恒 ≤ 0，都不会被选中；λ 仍在全部时段上更新。
   单个变体约 220 秒 → 约 40 秒（**约 5 倍**）。输出加了“精确 = 总分的 repr”，便于以后再做等价性检查。
2. **PyPy 3.11**：官方 kit 与 obs10 都是纯标准库。数学函数走同一个 C 数学库、IEEE 运算规则相同，
   dict 保持插入顺序，输出逐字节相同。规划器累计 528 秒 → 23 秒；但 PyPy 的 datetime 是纯 Python 实现，
   runner 只快 1.7 倍。
3. **`tools/fast_runner.py`**：在进程内给 kit 的六处纯计算加缓存后调用官方 `local_runner.main()`，kit 文件不改。
   每个补丁照抄原函数，只把与天区、与运行状态无关的中间量换成缓存值，浮点运算的内容与结合顺序不变
   （月光因子原式 `1 − a·b·c·d` 从左到右结合，缓存 `(a·b)·c`）。启动时校验 kit 全部源文件的 SHA-256，
   不一致就拒绝运行。CPython 下 runner 本身也快 2.3 倍。

| 每场耗时（决赛规模） | obs10 | 基线 agent（≈ runner 本身） |
|---|---|---|
| 官方 runner + CPython（原来，3 路并行时的中位数） | 639 秒 | 251 秒 |
| 官方 runner + PyPy | 172–204 秒 | 150–156 秒 |
| **加速 runner + PyPy（现在的默认）** | **115 秒** | **99 秒** |

## 验证

- 试验台：dev-fortnight 8 个变体、comp-a-s101 的 price 1h 与原版逐位相同（总分 17 位有效数字）；
  comp-a-s101 另外 3 个变体与此前批次的结果一致。
- PyPy + 官方 runner：dev-fortnight、comp-a-s101 的 decisions.csv 与 score_report.json 逐字节相同；
  基线 / obs10 × comp-b-s201、mc-a-9101、mc-b-9205、dev-reference 共 8 个作业全部指标一致。
- 加速 runner：dev-fortnight（CPython 与 PyPy）、comp-a-s101（PyPy）逐字节相同；
  `evaluate.py --verify` 基线 / obs10 × comp-b-s202、mc-b-9206、finals-preview、dev-reference 共 8 个作业逐字节相同。

## 用法与注意

- 安装 PyPy：`uv python install pypy@3.11`（约 150 MB，入口 `~/.local/bin/pypy3.11`）。
- `tools/evaluate.py` 默认用加速 runner + PATH 里的 pypy3.11；`--official` 改用官方 runner，`--python` 指定解释器，
  `--verify` 再用官方 runner + 当前 CPython 跑一遍并逐字节比对。kit 更新、改补丁或换解释器之后先跑一次 `--verify`，
  再更新 `fast_runner.py` 里的 `KIT_SHA256`。
- 内存：PyPy 下每个作业约 1.4–1.6 GB（runner 约 1 GB、agent 约 0.4 GB），官方 CPython runner 约 0.1 GB；
  本机 `--jobs 4` 可行。
- 平台用 CPython 3.12 运行 agent。PyPy 只用于本地评测：obs10 的时间保护按墙钟推算，在 PyPy 下不会触发；
  要估计平台上的耗时，用 `--official --python <CPython 解释器>`。

## 没做的

- agent 规划器的 numpy 向量化：可以做到逐位一致（逐元素 IEEE 乘减、argmax 取首个最大值、需求累加的是整数），
  但本地评测已由 PyPy 覆盖；平台 CPython 上规划约 530 秒，离 3600 秒时钟很远。需要更多规划预算时再做。
- 剩下的大头是协议本身：每次决策把几百个候选天区的几何与天气编码成 JSON（一场约 GB 量级），以及逐天区的三角计算。
  缓存消不掉，再压收益有限而风险上升。
- `tools/upper_bound.py` 每场约 60 秒，未优化。
