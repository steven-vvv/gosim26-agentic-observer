#!/usr/bin/env python3
"""把一个 agent 版本打成正式赛要求的"完整项目"（目录 + ZIP），根目录带 observer.project.json。

    uv run python tools/pack_project.py --agent agents/observer [--wheels /tmp/wheels] [--image python:3.12-slim]

正式赛只收完整项目（公开 GitHub 仓库或 ≤ 50 MB 的私有 ZIP，见 runs/platform-2026-09-25/docs_formal_zh.md）：
进程常驻，stdin/stdout 每行一个 JSON（jsonl-v2）；initialize 不回复，每个 decision_request 回一条同序号的响应。

- 代码：源目录里的 *.py 与 params.json（若有），原样复制；
- numpy：给了 --wheels 时把其中的 *.whl 放进 wheels/，构建阶段安装到可写的 .deps/；装不上再尝试联网安装，仍失败也不中止，
  agent 自动退回纯 Python 规划器（两条路径逐位相同，只是更慢）；
- telemetry.config.json：默认写入不含密钥的遥测配置。模型顾问和遥测可以共用一个 OpenAI-compatible
  Worker；Worker 按 `gosim26.telemetry` tool 标记消费遥测，其余请求代理到真实模型端点。
- ZIP 内文件按名字排序、时间戳固定，同样的输入得到同样的 sha256。

输出 dist/project-<版本>/ 与 dist/observer-<版本>-project.zip，并打印 sha256。这两样都不入库（见 .gitignore），随时可以重建。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_ZIP_BYTES = 50 << 20
FIXED_TIME = (2026, 10, 1, 0, 0, 0)
DEFAULT_TELEMETRY_URL = "https://gosim26-telemetry.wxlsteven.workers.dev/v1"

README = """# Agentic Observer：{name}

GOSIM 2026 巡天智能体参赛项目。确定性规划器生成安全候选；可用模型代理时，模型在计划自适应和
行动决策两个运行时环节选择候选。调用失败或输出非法时回退到确定性首选。

- 入口 `main.py`：JSON-Lines 协议循环（participant-agent-protocol-v2）。协议层容忍未知的消息类型和版本，同一序号的请求重发时原样重放响应。
- `policy.py`：在线决策。先按 (现在拍的收益 − 机会价值) / 曝光秒数粗排，再按分段几何精算并选档位，最后做 60 分钟推演。
  另外包括覆盖边际、REQUIRED 短窗口保护、仪器故障与标签的读数检测，以及寒潮期效率修正。
- `planner.py`：天区 → 未来 1 小时时段分配问题的对偶价格（次梯度法），得到每块天区的机会价值。
  规划时计入每周天气预报（`events.py`）。有 numpy 时走向量化路径，与纯 Python 路径逐位相同。
- `model_advisor.py`：通过平台的 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 调用模型代理；每环节最多四次，
  单次默认最多五秒，连续三次失败后停用。行动次选的优先级差距上限为 5%；非确定性候选还要达到
  0.85 的概率门槛才会被接受，顾问选择和概率分布写入逐步追踪。
- `geometry.py`：与官方公式一致的几何与日历。

构建：离线安装 `wheels/` 里的 numpy 到可写的 `.deps/`；失败时退回纯 Python，结果不变，只是更慢。
遥测默认开启：包内只有 Worker 地址、传输方式和逐步追踪开关等非机密配置。`OPENAI_BASE_URL` /
`OPENAI_API_KEY` 可以同时供模型顾问和带标记的遥测请求使用；显式 `TELEMETRY_URL` /
`TELEMETRY_TOKEN` 仍可覆盖遥测路由。无遥测密钥时逐步追踪仍写入 `agent.log`。网络失败会丢弃事件且不影响策略；密钥不会写入日志。
「参赛」页保存的上游模型应指向真实大模型，不能继续指向遥测 Worker；隐藏评测前还须选择加密保存模型密钥。
需要完全关闭时，用打包器的 `--no-telemetry`。
复现：关闭模型路径时，同一场景和种子下运行确定性；与本地官方 runner 的比对方法见仓库 `notes/`。
"""

PUBLIC_README = """# Public practice telemetry run

One-off participant package for the published dev-reference and dev-fortnight scenes.
The local schedule is used only when the complete public tile catalog fingerprint matches;
other scenes use a simple online fallback. No runtime NumPy dependency or model call is needed.

At initialization, the same startup event is written to agent.log and sent to the configured
OpenAI-compatible telemetry endpoint. It includes the public scene identity and non-secret
Python, CPU, memory, disk and process metadata. At the final decision, a bounded attempt
sends a run summary before the response; the same report is always written to agent.log.
No per-decision remote telemetry is collected. The scorer's decisions.csv records every action.
The telemetry key comes only from environment variables and is never packaged.
"""


def build(
    agent: Path,
    out_dir: Path,
    zip_path: Path,
    wheels: Path | None,
    image: str,
    telemetry_enabled: bool = True,
    telemetry_url: str = DEFAULT_TELEMETRY_URL,
    public_schedules: Path | None = None,
    telemetry_source: Path | None = None,
) -> str:
    sources = sorted(p for p in agent.glob("*.py")) + ([agent / "params.json"] if (agent / "params.json").exists() else [])
    if (agent / "llm.config.json").exists():
        sources.append(agent / "llm.config.json")
    if telemetry_source is not None and not any(p.name == "telemetry.py" for p in sources):
        sources.append(telemetry_source)
    if not any(p.name == "main.py" for p in sources):
        raise SystemExit(f"{agent} 里没有 main.py")
    if telemetry_enabled and not any(p.name == "telemetry.py" for p in sources):
        raise SystemExit(
            f"{agent} 里没有 telemetry.py；默认遥测需要当前工作副本 agents/observer。"
            "历史快照请显式使用 --no-telemetry。"
        )
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for path in sources:
        shutil.copy2(path, out_dir / path.name)
        if path.suffix == ".py":
            py_compile.compile(str(out_dir / path.name), doraise=True, cfile=str(out_dir / "__pycache__" / "check.pyc"))
    shutil.rmtree(out_dir / "__pycache__", ignore_errors=True)
    if public_schedules is not None:
        shutil.copy2(public_schedules, out_dir / "public_schedules.json")
    build_steps = []
    if wheels is not None:
        found = sorted(wheels.glob("numpy-*.whl"))
        if not found:
            raise SystemExit(f"{wheels} 里没有 numpy wheel")
        (out_dir / "wheels").mkdir()
        for wheel in found:
            shutil.copy2(wheel, out_dir / "wheels" / wheel.name)
        build_command = (
            "python3 -m pip install --disable-pip-version-check --no-index --target .deps --find-links wheels numpy "
            "|| python3 -m pip install --disable-pip-version-check --target .deps 'numpy>=1.26' || true"
        )
        build_steps.append(["sh", "-c", build_command])
    (out_dir / "telemetry.config.json").write_text(json.dumps({
        "enabled": bool(telemetry_enabled),
        "transport": "openai",
        "url": telemetry_url,
        "model": "telemetry-ack",
        "trace": agent.name != "public_once",
        "timeout_seconds": 5.0 if agent.name == "public_once" else 3.0,
        "queue_size": 1024,
        "batch_size": 16,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {"schema_version": "observer-project-v1", "protocol": "jsonl-v2", "image": image,
                "working_directory": ".", "build": build_steps, "run": ["python3", "-u", "main.py"]}
    (out_dir / "observer.project.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "README.md").write_text(PUBLIC_README if agent.name == "public_once" else README.format(name=agent.name),
                                       encoding="utf-8")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in out_dir.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(str(path.relative_to(out_dir)), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    size = zip_path.stat().st_size
    if size > MAX_ZIP_BYTES:
        raise SystemExit(f"{zip_path} 有 {size / 2**20:.1f} MB，超过 50 MB 上限")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"{zip_path}  {size / 2**20:.1f} MB  sha256 {digest}")
    return digest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", type=Path, required=True, help="agent 版本目录，例如 agents/observer")
    parser.add_argument("--wheels", type=Path, default=None, help="放 numpy wheel 的目录（不给则不带 numpy，纯 Python 运行）")
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--telemetry-url", default=DEFAULT_TELEMETRY_URL,
                        help="遥测 Worker 的非机密默认地址（TELEMETRY_URL 可覆盖）")
    parser.add_argument("--no-telemetry", action="store_true", help="在生成的完整项目中关闭默认遥测")
    parser.add_argument("--public-schedules", type=Path, default=None,
                        help="仅在公开场景目录指纹匹配时使用的预排动作 JSON")
    parser.add_argument("--telemetry-source", type=Path, default=None,
                        help="从另一目录复制共用的 telemetry.py（供独立入口使用）")
    args = parser.parse_args(argv)
    agent = args.agent.resolve()
    name = agent.name
    build(agent, ROOT / "dist" / f"project-{name}", ROOT / "dist" / f"observer-{name}-project.zip", args.wheels, args.image,
          telemetry_enabled=not args.no_telemetry, telemetry_url=args.telemetry_url,
          public_schedules=args.public_schedules, telemetry_source=args.telemetry_source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
