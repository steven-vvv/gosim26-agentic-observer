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
- 生成前会按平台规则检查 manifest、相对路径、敏感文件、符号链接、文件数量和展开体积；先在临时目录完成并验证，
  最后才替换旧目录与 ZIP，坏输入不会清掉上一个可用包。

输出 dist/project-<版本>/ 与 dist/observer-<版本>-project.zip，并打印 sha256。这两样都不入库（见 .gitignore），随时可以重建。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
MAX_ZIP_BYTES = 50 << 20
MAX_EXPANDED_BYTES = 100 << 20
MAX_FILES = 10_000
MAX_MANIFEST_BYTES = 64 << 10
FIXED_TIME = (2026, 10, 1, 0, 0, 0)
DEFAULT_TELEMETRY_URL = "https://gosim26-telemetry.wxlsteven.workers.dev/v1"
MANIFEST_NAME = "observer.project.json"
MANIFEST_SCHEMA = "observer-project-v1"
MANIFEST_FIELDS = {"schema_version", "image", "run", "build", "working_directory", "environment", "protocol"}
IMAGE_REFERENCE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:-]*(?:@sha256:[0-9a-f]{64})?$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
PRIVATE_ENV = re.compile(r"(SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY)")
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}

README = """# Agentic Observer：{name}

GOSIM 2026 巡天智能体参赛项目。确定性规划器生成安全候选；可用模型代理时，模型在计划自适应和
行动决策两个运行时环节选择候选。调用失败或输出非法时回退到确定性首选。

- 入口 `main.py`：JSON-Lines 协议循环（participant-agent-protocol-v2）。协议层容忍未知的消息类型和版本，同一序号的请求重发时原样重放响应。
- `policy.py`：在线决策。先按 (现在拍的收益 − 机会价值) / 曝光秒数粗排，再按分段几何精算并选档位，最后做 60 分钟推演。
  另外包括覆盖边际、REQUIRED 短窗口保护、仪器故障与标签的读数检测，以及寒潮期效率修正。
- `planner.py`：天区 → 未来 1 小时时段分配问题的对偶价格（次梯度法），得到每块天区的机会价值。
  规划时计入每周天气预报（`events.py`）。有 numpy 时走向量化路径，与纯 Python 路径逐位相同。
- `model_advisor.py`：通过平台的 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 调用模型代理；每环节最多四次，
  单次默认最多十八秒、生成上限 4096 token，连续三次失败后停用。行动次选的优先级差距上限为 5%；非确定性候选还要达到
  0.85 的概率门槛才会被接受，顾问选择和概率分布写入逐步追踪。
- `geometry.py`：与官方公式一致的几何与日历。

构建：离线安装 `wheels/` 里的 numpy 到可写的 `.deps/`；失败时退回纯 Python，结果不变，只是更慢。
遥测默认开启：包内只有 Worker 地址、传输方式和逐步追踪开关等非机密配置。`OPENAI_BASE_URL` /
`OPENAI_API_KEY` 可以同时供模型顾问和带标记的遥测请求使用；显式 `TELEMETRY_URL` /
`TELEMETRY_TOKEN` 仍可覆盖遥测路由。无遥测密钥时逐步追踪仍写入 `agent.log`。网络失败会丢弃事件且不影响策略；密钥不会写入日志。
「参赛」页的模型端点可指向已配置真实推理上游的共享 Worker；隐藏评测前还须选择加密保存 Worker 访问令牌。
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


def _is_argv(value) -> bool:
    return (isinstance(value, list) and 1 <= len(value) <= 128 and
            all(isinstance(item, str) and item and "\x00" not in item and len(item) <= 8192 for item in value) and
            not value[0].startswith("-"))


def _validate_manifest(raw: bytes, project_dir: Path | None = None) -> dict:
    """Apply the platform manifest checks before a package can replace an old build."""
    if len(raw) > MAX_MANIFEST_BYTES:
        raise SystemExit(f"{MANIFEST_NAME} 超过 64 KiB 上限")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise SystemExit(f"{MANIFEST_NAME} 不是有效 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{MANIFEST_NAME} 必须是 JSON object")
    unknown = set(data) - MANIFEST_FIELDS
    if unknown:
        raise SystemExit(f"{MANIFEST_NAME} 含平台不认识的字段: {', '.join(sorted(unknown))}")
    if data.get("schema_version") != MANIFEST_SCHEMA:
        raise SystemExit(f"{MANIFEST_NAME}.schema_version 必须为 {MANIFEST_SCHEMA}")
    image = data.get("image")
    if not isinstance(image, str) or len(image) > 256 or not IMAGE_REFERENCE.fullmatch(image):
        raise SystemExit(f"{MANIFEST_NAME}.image 不是有效的容器镜像引用")
    if data.get("protocol", "jsonl-v2") != "jsonl-v2":
        raise SystemExit(f"{MANIFEST_NAME}.protocol 必须为 jsonl-v2")
    if not _is_argv(data.get("run")):
        raise SystemExit(f"{MANIFEST_NAME}.run 必须是非空命令参数数组")
    build = data.get("build", [])
    if not isinstance(build, list) or len(build) > 16 or not all(_is_argv(command) for command in build):
        raise SystemExit(f"{MANIFEST_NAME}.build 必须是最多 16 个命令参数数组")
    workdir = data.get("working_directory", ".")
    if (not isinstance(workdir, str) or len(workdir) > 512 or workdir.startswith("/") or "\\" in workdir or
            ":" in workdir or (workdir != "." and any(part in ("", ".", "..") for part in workdir.split("/"))) or
            any(ord(char) < 32 or ord(char) == 127 for char in workdir)):
        raise SystemExit(f"{MANIFEST_NAME}.working_directory 必须是项目内的相对目录")
    if project_dir is not None and not (project_dir / workdir).is_dir():
        raise SystemExit(f"{MANIFEST_NAME}.working_directory 不存在: {workdir}")
    environment = data.get("environment", {})
    if not isinstance(environment, dict) or len(environment) > 32:
        raise SystemExit(f"{MANIFEST_NAME}.environment 最多允许 32 个设置")
    for key, value in environment.items():
        if (not isinstance(key, str) or not ENV_NAME.fullmatch(key) or
                key.startswith(("GITHUB_", "SUPABASE_", "OBSERVER_", "ACTIONS_")) or PRIVATE_ENV.search(key)):
            raise SystemExit(f"{MANIFEST_NAME}.environment 含凭据或平台保留变量: {key}")
        if not isinstance(value, str) or "\x00" in value or len(value) > 4096:
            raise SystemExit(f"{MANIFEST_NAME}.environment.{key} 不是有效的短字符串")
    return data


def _validate_telemetry_url(value: str) -> None:
    """Keep credentials out of the packaged non-secret telemetry configuration."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise SystemExit("遥测 URL 必须是短的 HTTP(S) 地址")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise SystemExit(f"遥测 URL 无效: {value!r}") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname or
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise SystemExit("遥测 URL 只能包含不带凭据、查询参数或片段的 HTTP(S) 地址")


def _validate_archive_name(name: str) -> None:
    """Reject paths the platform archive reader would reject."""
    if (not name or name.startswith("/") or "\\" in name or ":" in name or
            any(part in ("", ".", "..") for part in name.split("/")) or
            any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise SystemExit(f"包内路径不安全: {name!r}")
    parts = name.split("/")
    if any(part.casefold() == ".git" for part in parts):
        raise SystemExit("包内不能包含 .git 历史")
    for part in parts:
        folded = part.casefold()
        if (folded == ".env" or folded.startswith(".env.")) and part not in ENV_TEMPLATES:
            raise SystemExit("包内不能包含 .env 凭据文件")


def _validate_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise SystemExit(f"{label} 不能是符号链接: {path}")
    if not path.is_file():
        raise SystemExit(f"{label} 不存在或不是普通文件: {path}")


def _validate_package_files(project_dir: Path) -> list[Path]:
    files = sorted((path for path in project_dir.rglob("*") if path.is_file() or path.is_symlink()),
                   key=lambda path: path.relative_to(project_dir).as_posix())
    if len(files) > MAX_FILES:
        raise SystemExit(f"包内有 {len(files)} 个文件，超过 {MAX_FILES} 个上限")
    total = 0
    seen: set[str] = set()
    for path in files:
        if path.is_symlink():
            raise SystemExit(f"包内不能包含符号链接: {path.relative_to(project_dir)}")
        relative = path.relative_to(project_dir).as_posix()
        _validate_archive_name(relative)
        folded = relative.casefold()
        if folded in seen:
            raise SystemExit(f"包内存在大小写冲突的重复路径: {relative}")
        seen.add(folded)
        total += path.stat().st_size
        if total > MAX_EXPANDED_BYTES:
            raise SystemExit(f"包展开后超过 {MAX_EXPANDED_BYTES / 2**20:.0f} MiB 上限")
    for relative in seen:
        parts = relative.split("/")
        if any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            raise SystemExit(f"包内文件与目录路径冲突: {relative}")
    if not files:
        raise SystemExit("包内没有文件")
    return files


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
    agent = agent.resolve()
    out_dir = out_dir.absolute()
    zip_path = zip_path.absolute()
    if not agent.is_dir():
        raise SystemExit(f"agent 目录不存在: {agent}")
    if out_dir == agent or agent in out_dir.parents:
        raise SystemExit("输出目录不能位于 agent 目录内")
    if zip_path == agent or agent in zip_path.parents:
        raise SystemExit("ZIP 输出不能位于 agent 目录内")
    if zip_path == out_dir or out_dir in zip_path.parents:
        raise SystemExit("ZIP 输出不能位于项目输出目录内")
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        raise SystemExit(f"项目输出路径不是普通目录: {out_dir}")
    if zip_path.is_symlink() or (zip_path.exists() and not zip_path.is_file()):
        raise SystemExit(f"ZIP 输出路径不是普通文件: {zip_path}")

    sources = sorted(agent.glob("*.py"), key=lambda path: path.name)
    for optional in (agent / "params.json", agent / "llm.config.json"):
        if optional.exists():
            sources.append(optional)
    if telemetry_source is not None and not any(path.name == "telemetry.py" for path in sources):
        sources.append(telemetry_source)
    if not any(path.name == "main.py" for path in sources):
        raise SystemExit(f"{agent} 里没有 main.py")
    if telemetry_enabled and not any(path.name == "telemetry.py" for path in sources):
        raise SystemExit(
            f"{agent} 里没有 telemetry.py；默认遥测需要当前工作副本 agents/observer。"
            "历史快照请显式使用 --no-telemetry。"
        )
    source_names: set[str] = set()
    for path in sources:
        _validate_regular_file(path, "源文件")
        _validate_archive_name(path.name)
        folded = path.name.casefold()
        if folded in source_names:
            raise SystemExit(f"源文件名大小写冲突: {path.name}")
        source_names.add(folded)

    if public_schedules is not None:
        _validate_regular_file(public_schedules, "公开排程")
        if "public_schedules.json".casefold() in source_names:
            raise SystemExit("源文件与 public_schedules.json 重名")

    found_wheels: list[Path] = []
    if wheels is not None:
        wheels = wheels.absolute()
        if wheels.is_symlink() or not wheels.is_dir():
            raise SystemExit(f"wheel 目录不存在或不是普通目录: {wheels}")
        wheels = wheels.resolve()
        found_wheels = sorted(wheels.glob("numpy-*.whl"), key=lambda path: path.name)
        if not found_wheels:
            raise SystemExit(f"{wheels} 里没有 numpy wheel")
        wheel_names: set[str] = set()
        for wheel in found_wheels:
            _validate_regular_file(wheel, "numpy wheel")
            _validate_archive_name(f"wheels/{wheel.name}")
            folded = wheel.name.casefold()
            if folded in wheel_names:
                raise SystemExit(f"wheel 文件名大小写冲突: {wheel.name}")
            wheel_names.add(folded)

    build_steps = []
    if found_wheels:
        build_command = (
            "python3 -m pip install --disable-pip-version-check --no-index --target .deps --find-links wheels numpy "
            "|| python3 -m pip install --disable-pip-version-check --target .deps 'numpy>=1.26' || true"
        )
        build_steps.append(["sh", "-c", build_command])
    _validate_telemetry_url(telemetry_url)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "protocol": "jsonl-v2",
        "image": image,
        "working_directory": ".",
        "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
        "build": build_steps,
        "run": ["python3", "-u", "main.py"],
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode() + b"\n"
    _validate_manifest(manifest_bytes)

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{out_dir.name}.", dir=str(out_dir.parent)) as temp_root:
        staging = Path(temp_root) / "project"
        staging.mkdir()
        for path in sources:
            shutil.copy2(path, staging / path.name)
            if path.suffix == ".py":
                cache = staging / "__pycache__"
                cache.mkdir(exist_ok=True)
                py_compile.compile(str(staging / path.name), doraise=True, cfile=str(cache / "check.pyc"))
        shutil.rmtree(staging / "__pycache__", ignore_errors=True)
        if public_schedules is not None:
            shutil.copy2(public_schedules, staging / "public_schedules.json")
        if found_wheels:
            wheel_dir = staging / "wheels"
            wheel_dir.mkdir()
            for wheel in found_wheels:
                shutil.copy2(wheel, wheel_dir / wheel.name)
        (staging / "telemetry.config.json").write_text(json.dumps({
            "enabled": bool(telemetry_enabled),
            "transport": "openai",
            "url": telemetry_url,
            "model": "telemetry-ack",
            "trace": agent.name != "public_once",
            "timeout_seconds": 5.0 if agent.name == "public_once" else 3.0,
            "queue_size": 1024,
            "batch_size": 16,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
        (staging / "README.md").write_text(PUBLIC_README if agent.name == "public_once" else README.format(name=agent.name),
                                            encoding="utf-8")
        _validate_manifest(manifest_bytes, staging)
        files = _validate_package_files(staging)

        temp_zip = Path(temp_root) / "package.zip"
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info = zipfile.ZipInfo(str(path.relative_to(staging)), FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
        size = temp_zip.stat().st_size
        if size > MAX_ZIP_BYTES:
            raise SystemExit(f"{zip_path} 有 {size / 2**20:.1f} MB，超过 50 MB 上限")

        previous = None
        installed = False
        try:
            if out_dir.exists():
                previous = Path(temp_root) / "previous-project"
                out_dir.rename(previous)
            staging.rename(out_dir)
            installed = True
            temp_zip.replace(zip_path)
        except Exception:
            if installed and out_dir.exists():
                shutil.rmtree(out_dir)
            if previous is not None and previous.exists():
                previous.rename(out_dir)
            raise
        if previous is not None and previous.exists():
            shutil.rmtree(previous)

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
