#!/usr/bin/env python3
"""把一个 agent 版本打成正式赛要求的"完整项目"（目录 + ZIP），根目录带 observer.project.json。

    uv run python tools/pack_project.py --agent agents/observer [--wheels /tmp/wheels] [--image python:3.12-slim]

正式赛只收完整项目（公开 GitHub 仓库或 ≤ 50 MB 的私有 ZIP，见 runs/platform-2026-09-25/docs_formal_zh.md）：
进程常驻，stdin/stdout 每行一个 JSON（jsonl-v2）；initialize 不回复，每个 decision_request 回一条同序号的响应。

- 代码：源目录里的 *.py 与 params.json（若有），原样复制；
- numpy：给了 --wheels 时把其中的 *.whl 放进 wheels/，构建阶段安装到可写的 .deps/；装不上再尝试联网安装，仍失败也不中止，
  agent 自动退回纯 Python 规划器（两条路径逐位相同，只是更慢）；
- telemetry.config.json：默认写入不含密钥的遥测配置；直连时由 OPENAI_* 变量启用 Worker 遥测，比赛平台的
  OPENAI_* 变量则指向平台代理，需在平台保存的团队模型中把上游配置为 Worker。
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

GOSIM 2026 巡天智能体参赛项目。确定性策略，不调用模型；模型密钥不是决策所需，平台若注入
`OPENAI_API_KEY`，它只按下文说明作为遥测写入鉴权密钥使用。

- 入口 `main.py`：JSON-Lines 协议循环（participant-agent-protocol-v2）。协议层容忍未知的消息类型和版本，同一序号的请求重发时原样重放响应。
- `policy.py`：在线决策。先按 (现在拍的收益 − 机会价值) / 曝光秒数粗排，再按分段几何精算并选档位，最后做 60 分钟推演。
  另外包括覆盖边际、REQUIRED 短窗口保护、仪器故障与标签的读数检测，以及寒潮期效率修正。
- `planner.py`：天区 → 未来 1 小时时段分配问题的对偶价格（次梯度法），得到每块天区的机会价值。
  规划时计入每周天气预报（`events.py`）。有 numpy 时走向量化路径，与纯 Python 路径逐位相同。
- `geometry.py`：与官方公式一致的几何与日历。

构建：离线安装 `wheels/` 里的 numpy 到可写的 `.deps/`；失败时退回纯 Python，结果不变，只是更慢。
遥测默认开启：包内只有 Worker 地址、传输方式和逐步追踪开关等非机密配置。直接运行时，
`OPENAI_BASE_URL` 与 `OPENAI_API_KEY` 可以分别指向 Worker 和其写入密钥；比赛平台会把这两个变量改为平台代理
地址与单次运行凭据，此时应在平台保存的团队模型中把上游地址、模型和密钥配置为 Worker。客户端只发送标准
Chat Completions 字段，事件放在 JSON user message 中，失败会丢弃且不影响决策。密钥不会写入日志；没有可用
密钥时客户端保持离线。
需要完全关闭时，用打包器的 `--no-telemetry`。
复现：同一场景、同一种子下运行确定性；与本地官方 runner 的比对方法见仓库 `notes/`。
"""


def build(
    agent: Path,
    out_dir: Path,
    zip_path: Path,
    wheels: Path | None,
    image: str,
    telemetry_enabled: bool = True,
    telemetry_url: str = DEFAULT_TELEMETRY_URL,
) -> str:
    sources = sorted(p for p in agent.glob("*.py")) + ([agent / "params.json"] if (agent / "params.json").exists() else [])
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
        "trace": True,
        "queue_size": 1024,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {"schema_version": "observer-project-v1", "protocol": "jsonl-v2", "image": image,
                "working_directory": ".", "build": build_steps, "run": ["python3", "-u", "main.py"]}
    (out_dir / "observer.project.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "README.md").write_text(README.format(name=agent.name), encoding="utf-8")
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
                        help="遥测 Worker 的非机密默认地址（环境变量 OPENAI_BASE_URL 可覆盖）")
    parser.add_argument("--no-telemetry", action="store_true", help="在生成的完整项目中关闭默认遥测")
    args = parser.parse_args(argv)
    agent = args.agent.resolve()
    name = agent.name
    build(agent, ROOT / "dist" / f"project-{name}", ROOT / "dist" / f"observer-{name}-project.zip", args.wheels, args.image,
          telemetry_enabled=not args.no_telemetry, telemetry_url=args.telemetry_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
