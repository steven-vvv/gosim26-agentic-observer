#!/usr/bin/env python3
"""Run a participant agent against a scenario exactly the way the evaluation platform does, then score it.

    python3 local_runner.py --scenario scenarios/dev-reference --agent agent/minimal_agent.py [--wallclock 600] [--out run_output]

The agent is started as a persistent JSON-Lines subprocess (the platform's `JsonLineAgentProcess` transport):
one `initialize` envelope, then one `decision_request` per decision opportunity, one `decision_response` back.
As on the platform the process runs with cwd = the agent's folder, a scrubbed environment plus the KEY=VALUE
pairs from `<agent folder>/.env`, and its stderr captured in `<out>/agent.log`. The one global wall clock starts
after the initial publication; the process is killed at the cutoff.

Outputs in --out: decisions.csv, workflow_result.json, score_report.json (authoritative replay of decisions.csv)
and decision_replay.html (interactive replay, when the replay renderer is available). The last stdout line is a
JSON summary.

Standard library only. Use --python to run the agent with a different interpreter (for example one where the
optional LangChain extras from agent/requirements.txt are installed).
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 9):
    sys.stderr.write(f"local_runner: Python 3.9 or newer is required (this is {sys.version.split()[0]} at {sys.executable}); "
                     "install Python 3.12 from python.org and run the kit with it.\n")
    sys.exit(3)

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
if str(KIT_ROOT) not in sys.path:
    sys.path.insert(0, str(KIT_ROOT))

from challenge.challenge_workflow import ChallengeWorkflow  # noqa: E402
from challenge.contracts import PARTICIPANT_PROTOCOL_VERSION  # noqa: E402
from challenge.run_challenge import JsonLineAgentProcess  # noqa: E402
from challenge.scoring_core import score_files  # noqa: E402

ENTRY_CANDIDATES = ("minimal_agent.py", "agent.py", "main.py")
SAFE_ENV_KEYS = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
PROTECTED_KEYS = ("PATH", "HOME", "TMPDIR", "LD_PRELOAD", "PYTHONPATH", "PYTHONSTARTUP")


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (no interpolation), the same reader the platform uses."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if SAFE_ENV_KEYS.match(key):
            env[key] = value
    return env


def build_agent_env(agent_dir: Path, scratch: Path, wallclock: float, scenario_slug: str, inherit: bool) -> tuple[dict, list[str]]:
    base = dict(os.environ) if inherit else {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    env = {**base, "HOME": str(scratch), "TMPDIR": str(scratch), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
           "PARTICIPANT_PROTOCOL": PARTICIPANT_PROTOCOL_VERSION, "SAC_SCENARIO": scenario_slug,
           "SAC_WALLCLOCK_SECONDS": str(int(wallclock)), "SAC_LOCAL_RUNNER": "1"}
    dotenv = load_dotenv(agent_dir / ".env")
    for key, value in dotenv.items():
        if key not in PROTECTED_KEYS:
            env[key] = value
    return env, sorted(dotenv)


class LocalAgentProcess(JsonLineAgentProcess):
    """The platform transport with its process isolation: own cwd, explicit env, stderr to a log, process group kill."""

    def __init__(self, command: list[str], *, agent_dir: Path, env: dict, stderr, initialization_timeout_seconds: float = 30.0,
                 protocol_version: str | None = None):
        super().__init__(command, initialization_timeout_seconds,
                         **({"protocol_version": protocol_version} if protocol_version else {}))
        self.agent_dir = agent_dir
        self.env = env
        self.stderr = stderr

    def _start(self):
        if self.process is None:
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
            self.process = subprocess.Popen(self.command, cwd=str(self.agent_dir), env=self.env, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=self.stderr, text=False, bufsize=0, **group)
            self._configure_pipes(self.process)
        return self.process

    def close(self, force: bool = False) -> None:
        proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGTERM)
            except Exception:
                pass
        super().close(force=force)


def find_entry(agent: Path) -> Path:
    if agent.is_file():
        return agent.resolve()
    for name in ENTRY_CANDIDATES:
        if (agent / name).is_file():
            return (agent / name).resolve()
    raise SystemExit(f"no entry script in {agent}: expected one of {', '.join(ENTRY_CANDIDATES)}")


def summarize(result: dict, report: dict, out_dir: Path, elapsed: float) -> dict:
    score = report["score"]
    completion = report["completion"]
    return {
        "termination_reason": result["termination_reason"],
        "total": score["total"],
        "base_science": score["base_science"],
        "program_bonus": score["program_bonus"],
        "request_reward": score["request_reward"],
        "penalties": score["penalties"],
        "completed_tiles": len(completion["completed_tiles"]),
        "required_missing": len(completion["required_missing"]),
        "flexible_shortfall_tiles": sum(completion["flexible_shortfall"].values()),
        "requests": dict(sorted(Counter(row["status"] for row in report["requests"]).items())),
        "committed_actions": result["committed_action_count"],
        "final_cursor": report["final_cursor"]["timestamp_utc"],
        "wall_seconds": round(float(result["accounted_wallclock_seconds"]), 3),
        "global_wallclock_seconds": result["global_wallclock_seconds"],
        "runner_seconds": round(elapsed, 3),
        "outputs": {name: str(out_dir / name) for name in ("decisions.csv", "workflow_result.json", "score_report.json", "agent.log")},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", type=Path, default=KIT_ROOT / "scenarios" / "dev-reference",
                        help="scenario directory holding config/ and outputs/reference/ (default: scenarios/dev-reference)")
    parser.add_argument("--agent", type=Path, default=KIT_ROOT / "agent" / "minimal_agent.py",
                        help="entry script, or a folder containing minimal_agent.py / agent.py / main.py")
    parser.add_argument("--wallclock", type=float, default=None,
                        help="global wall-clock budget in seconds (default: the scenario's workflow_config.json value)")
    parser.add_argument("--out", type=Path, default=KIT_ROOT / "run_output", help="output directory (default: run_output)")
    parser.add_argument("--python", default=sys.executable, help="interpreter used to start the agent (default: this one)")
    parser.add_argument("--init-timeout", type=float, default=30.0, help="seconds allowed for the agent to accept `initialize`")
    parser.add_argument("--inherit-env", action="store_true",
                        help="pass your whole shell environment to the agent (the platform passes only .env; default mirrors that)")
    parser.add_argument("--show-agent-stderr", action="store_true", help="print the agent's stderr to the terminal instead of agent.log")
    parser.add_argument("--keep-initial-publication", action="store_true",
                        help="keep the full `initialize` payload inside workflow_result.json (large; the platform strips it)")
    parser.add_argument("--no-replay", action="store_true", help="skip decision_replay.html")
    parser.add_argument("--quiet", action="store_true", help="print only the final JSON summary")
    args = parser.parse_args(argv)

    scenario = args.scenario.resolve()
    if not (scenario / "config" / "workflow_config.json").is_file():
        raise SystemExit(f"{scenario} is not a scenario directory (missing config/workflow_config.json)")
    entry = find_entry(args.agent)
    agent_dir = entry.parent
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = out_dir / "scratch"
    scratch.mkdir(exist_ok=True)
    log = lambda text: None if args.quiet else print(text, file=sys.stderr, flush=True)  # noqa: E731

    workflow = ChallengeWorkflow(root=scenario)
    wallclock = float(args.wallclock if args.wallclock is not None else workflow.config["global_wallclock_seconds"])
    if wallclock <= 0:
        raise SystemExit("--wallclock must be positive")
    scenario_slug = json.loads((scenario / "config" / "scenario_config.json").read_text(encoding="utf-8")).get("scenario_id", scenario.name)
    env, dotenv_keys = build_agent_env(agent_dir, scratch, wallclock, scenario_slug, args.inherit_env)
    command = [args.python, "-B", str(entry)]
    log(f"[local-runner] scenario={scenario.name} ({workflow.initial_publication()['calendar']['night_count']} nights) "
        f"agent={entry.relative_to(agent_dir.parent) if agent_dir.parent in entry.parents else entry} wallclock={wallclock:g}s dotenv_keys={dotenv_keys}")

    started = time.monotonic()
    with (out_dir / "agent.log").open("w", encoding="utf-8") as agent_log:
        agent_log.write(f"[local-runner] entry={entry.name} python={args.python} dotenv_keys={dotenv_keys} wallclock={wallclock:g}s\n")
        agent_log.flush()
        protocol_version = ("participant-agent-protocol-v2" if workflow.mechanics else "participant-agent-protocol-v1")
        env["PARTICIPANT_PROTOCOL"] = protocol_version
        provider = LocalAgentProcess(command, agent_dir=agent_dir, env=env, stderr=None if args.show_agent_stderr else agent_log,
                                     initialization_timeout_seconds=args.init_timeout, protocol_version=protocol_version)
        try:
            result = workflow.run(provider, wallclock_seconds=wallclock)
        finally:
            provider.close(force=True)
    elapsed = time.monotonic() - started
    workflow.write_outputs(out_dir, result)
    if not args.keep_initial_publication:
        wr = out_dir / "workflow_result.json"
        data = json.loads(wr.read_text(encoding="utf-8"))
        data.pop("initial_publication", None)
        wr.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Authoritative score: replay decisions.csv with the public scorer, exactly as the platform does after a run.
    report = score_files(scenario, out_dir / "decisions.csv", out_dir / "score_report.json", result["termination_reason"])
    live_total = result["score_report"]["score"]["total"]
    if abs(float(report["score"]["total"]) - float(live_total)) > 1e-6:
        log(f"[local-runner] warning: replay total {report['score']['total']} differs from live total {live_total}")
    for entry_row in result["commit_log"]:
        if not entry_row.get("committed"):
            log(f"[local-runner] agent error at decision {entry_row.get('sequence')}: {entry_row.get('error')} (see agent.log)")

    if not args.no_replay:
        try:
            from challenge.replay import write_replay_html
        except Exception as exc:  # noqa: BLE001 - the renderer is optional
            log(f"[local-runner] replay renderer unavailable ({type(exc).__name__}); skipping decision_replay.html")
        else:
            try:
                path = write_replay_html(scenario, report, out_dir / "decision_replay.html", title=f"{scenario.name} decision replay",
                                         agent_label=f"{entry.name} · {result['termination_reason']}", decisions_path=out_dir / "decisions.csv")
                log(f"[local-runner] replay written to {path}")
            except Exception as exc:  # noqa: BLE001
                log(f"[local-runner] replay rendering failed: {type(exc).__name__}: {exc}")

    summary = summarize(result, report, out_dir, elapsed)
    if summary.get("termination_reason") in ("agent_error", "agent_initialization_error") and not args.quiet:
        tail = (out_dir / "agent.log").read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        print("[local-runner] the agent failed; last lines of agent.log:", file=sys.stderr)
        for line in tail:
            print("    " + line, file=sys.stderr)
        print(f"[local-runner] full log: {out_dir / 'agent.log'}", file=sys.stderr, flush=True)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if result["termination_reason"] in ("survey_complete", "global_wallclock_expired") else 2


if __name__ == "__main__":
    raise SystemExit(main())
