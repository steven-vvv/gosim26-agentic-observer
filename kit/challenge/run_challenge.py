#!/usr/bin/env python3
"""Run the example3 workflow with a deterministic reference agent."""

from __future__ import annotations

import argparse
import json
import os
import queue
import random
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

from .challenge_workflow import ChallengeWorkflow, GlobalDeadlineExpired
from .contracts import LEGACY_PARTICIPANT_PROTOCOL_VERSION, PARTICIPANT_PROTOCOL_VERSION
from .project_paths import EXAMPLE3_ROOT


class ReferenceAgent:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)

    def __call__(self, snapshot, deadline_monotonic):
        candidates = [item for item in snapshot["candidate_tiles"] if item["effective_weather"]["is_observable"] and not item["already_completed"]]
        if not candidates and snapshot.get("schema_version") == "decision-snapshot-v3":
            # Repeat observations are legal and bank the per-tile maximum: an
            # observable completed tile beats an avoidable wait.
            candidates = [item for item in snapshot["candidate_tiles"] if item["effective_weather"]["is_observable"]]
        if not candidates:
            return {"action": "wait", "reason": "no open uncompleted candidate"}
        tile = self.rng.choice(candidates)
        quality = tile["effective_weather"]
        # Snapshot weather omits instrument_efficiency (hidden instrument side).
        atmospheric = float(quality["transparency"]) * float(quality["sky_quality"]) / (float(quality["seeing_arcsec"]) * float(tile["geometry"]["airmass"]))
        combined = atmospheric * float(tile["geometry"]["lunar_quality_factor"])
        program = "DARK" if combined >= .65 else "BRIGHT" if combined >= .40 else "BACKUP"
        request_id = ""
        for request in snapshot["active_requests"]:
            if tile["tile_id"] in {item["tile_id"] for item in request["tile_requirements"]}:
                request_id = request["request_id"]
                break
        return {"action": "observe", "tile_id": tile["tile_id"], "program": program, "request_id": request_id, "reason": "reference feasible random policy"}


class JsonLineAgentProcess:
    """Persistent JSON-Lines agent transport with cutoff cancellation."""

    def __init__(self, command: list[str], initialization_timeout_seconds: float = 30.0, *,
                 protocol_version: str = PARTICIPANT_PROTOCOL_VERSION) -> None:
        if not command:
            raise ValueError("agent command cannot be empty")
        if initialization_timeout_seconds <= 0:
            raise ValueError("initialization timeout must be positive")
        self.command = command
        self.initialization_timeout_seconds = initialization_timeout_seconds
        self.protocol_version = protocol_version
        self.process: subprocess.Popen[bytes] | None = None
        self._stdout_buffer = b""
        # Windows has no select() on pipes and no non-blocking pipe mode: a reader thread feeds a queue
        # and writes run on a helper thread, so the same deadline semantics hold there (SAC_TRANSPORT=threads
        # forces this path on other platforms, which is how it is tested).
        self._threaded = sys.platform == "win32" or os.environ.get("SAC_TRANSPORT") == "threads"
        self._chunks: "queue.Queue[bytes]" = queue.Queue()
        self._reader: threading.Thread | None = None

    def _start(self) -> subprocess.Popen[bytes]:
        if self.process is None:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                text=False,
                bufsize=0,
            )
            self._configure_pipes(self.process)
        return self.process

    def _configure_pipes(self, process: subprocess.Popen[bytes]) -> None:
        if self._threaded:
            if process.stdout is not None:
                self._reader = threading.Thread(target=self._pump_stdout, args=(process.stdout.fileno(),), daemon=True)
                self._reader.start()
            return
        # Non-blocking stdin: a blocking os.write() of a message larger than the pipe buffer (the first
        # decision snapshot can exceed 200 KB) would otherwise hang until the agent reads, defeating the
        # global wall-clock cutoff. With non-blocking writes, select() paces the transfer and the deadline holds.
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)

    def _pump_stdout(self, fd: int) -> None:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            self._chunks.put(chunk)
            if not chunk:
                return

    def _write_threaded(self, process, data: bytes, deadline_monotonic: float) -> None:
        failure: list[BaseException] = []

        def _run() -> None:
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except BaseException as exc:  # noqa: BLE001  (closed pipe when the agent exits)
                failure.append(exc)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(max(0.0, deadline_monotonic - time.monotonic()))
        if worker.is_alive():
            self.close(force=True)
            raise GlobalDeadlineExpired()
        if failure:
            raise RuntimeError(f"agent exited before reading the next message (code={process.poll()})")

    def _write_message(self, message, deadline_monotonic):
        process = self._start()
        if process.stdin is None:
            raise RuntimeError("agent process pipes are unavailable")
        data = (json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        if self._threaded:
            self._write_threaded(process, data, deadline_monotonic)
            return
        pending = memoryview(data)
        while pending:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0 or not select.select([], [process.stdin], [], remaining)[1]:
                self.close(force=True)
                raise GlobalDeadlineExpired()
            try:
                written = os.write(process.stdin.fileno(), pending)
            except BlockingIOError:
                continue
            pending = pending[written:]

    def publish_initial(self, publication):
        """Send the one-time public bootstrap message before the competition clock."""
        self._write_message(
            {
                "protocol_version": self.protocol_version,
                "message_type": "initialize",
                "payload": publication,
            },
            time.monotonic() + self.initialization_timeout_seconds,
        )

    def __call__(self, snapshot, deadline_monotonic):
        process = self._start()
        if process.stdout is None:
            raise RuntimeError("agent process pipes are unavailable")
        self._write_message(
            {
                "protocol_version": self.protocol_version,
                "message_type": "decision_request",
                "decision_sequence": snapshot["decision_sequence"],
                "payload": snapshot,
            },
            deadline_monotonic,
        )
        while b"\n" not in self._stdout_buffer:
            remaining = deadline_monotonic - time.monotonic()
            if self._threaded:
                try:
                    chunk = self._chunks.get(timeout=max(0.0, remaining)) if remaining > 0 else None
                except queue.Empty:
                    chunk = None
                if chunk is None:
                    self.close(force=True)
                    raise GlobalDeadlineExpired()
            else:
                if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                    self.close(force=True)
                    raise GlobalDeadlineExpired()
                chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError(f"agent exited before responding (code={process.poll()})")
            self._stdout_buffer += chunk
        line, self._stdout_buffer = self._stdout_buffer.split(b"\n", 1)
        payload = json.loads(line.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("agent response must be a JSON object")
        return payload

    def close(self, force: bool = False) -> None:
        if self.process is None:
            return
        process = self.process
        if process.poll() is None:
            process.kill() if force else process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        self.process = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallclock-seconds", type=float, default=None)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--output-dir", type=Path, default=EXAMPLE3_ROOT / "outputs" / "workflow_reference")
    parser.add_argument(
        "--agent-command",
        nargs=argparse.REMAINDER,
        help="persistent JSON-Lines agent command; all remaining arguments belong to it",
    )
    args = parser.parse_args()
    workflow = ChallengeWorkflow()
    provider = JsonLineAgentProcess(args.agent_command) if args.agent_command else ReferenceAgent(args.seed)
    try:
        result = workflow.run(provider, args.wallclock_seconds)
    finally:
        if isinstance(provider, JsonLineAgentProcess):
            provider.close()
    workflow.write_outputs(args.output_dir, result)
    print(json.dumps({key: result[key] for key in ("termination_reason", "committed_action_count", "accounted_wallclock_seconds")}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
