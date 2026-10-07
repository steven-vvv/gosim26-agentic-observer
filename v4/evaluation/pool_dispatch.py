"""Dynamic per-card dispatch on top of the resource pool (``pool submit`` / ``pool dispatch``).

``pool run`` splits a dataset across hosts once, up front, in proportion to each host's ``jobs``; a slow or
loaded host then drags the tail. Here the (card, variant) tasks wait in a local queue file instead and every
dispatch round hands a small chunk to each host that has free slots, so fast hosts naturally take more::

    pool submit --dataset trial-kinds --variants default --tag r2x [--cards ...] [--hosts a,b]
    pool dispatch --loop 30        # all unfinished queues, until they are finished (Ctrl-C is safe)
    pool dispatch --status         # counts only, no ssh

A queue file ``v4/results/remote/pool/queue-<tag>-<dataset>.json`` remembers its code root, so queues of the
original and of the second root (``POOL_REMOTE_PROJECT``) are served by one dispatcher. Launching reuses
``pool.start_remote_batch`` unchanged (generate, then one batch.py per variant group).

Task states: pending -> running (host, dispatched_at) -> done | pending again (attempts+1) | failed.
* done: ``summary.json`` of the task exists on the host it was sent to (or already pulled locally).
* dead: running longer than GRACE seconds, no batch of this tag in that root on the host, no summary.
  Re-queued; batch.py discards the partial output directory and reruns it.
* timeout (``--timeout`` seconds, default 6h): re-queued on another host (never twice onto a live host).
Free slots of a host = jobs - all evaluation processes on it (every root) - tasks launched less than GRACE ago
(their processes may not be visible yet). Not implemented: redundant tail reruns.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

from . import pool

GRACE = 180
MAX_ATTEMPTS = 3
DEFAULT_CHUNK = 8
DEFAULT_TIMEOUT = 6 * 3600
QUEUE_DIR = pool.ROOT / "v4" / "results" / "remote" / "pool"
_TAG = re.compile(r"^[a-z0-9_-]+$")


def queue_dir() -> Path:
    return QUEUE_DIR


def queue_path(tag: str, dataset: str) -> Path:
    return queue_dir() / f"queue-{tag}-{dataset}.json"


def load_queue(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_queue(path: Path, queue: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


@contextlib.contextmanager
def project_env(project: str):
    """Operate on a queue's code root: pool.remote_project() reads POOL_REMOTE_PROJECT at call time."""
    old = os.environ.get("POOL_REMOTE_PROJECT")
    os.environ["POOL_REMOTE_PROJECT"] = project
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("POOL_REMOTE_PROJECT", None)
        else:
            os.environ["POOL_REMOTE_PROJECT"] = old


def local_summary(dataset: str, tag: str, variant: str, card: str) -> Path:
    return pool.ROOT / "v4" / "results" / "remote" / f"{dataset}-r2" / tag / variant / card / "summary.json"


def counts(queue: dict) -> dict[str, int]:
    out = {"pending": 0, "running": 0, "done": 0, "failed": 0}
    for task in queue["tasks"]:
        out[task["state"]] += 1
    return out


def finished(queue: dict) -> bool:
    return all(task["state"] in ("done", "failed") for task in queue["tasks"])


# --- submit ------------------------------------------------------------------

def cmd_submit(dataset: str, variants: list[str], tag: str, hosts: str | None, cards: list[str] | None) -> Path:
    if not _TAG.fullmatch(tag):
        raise SystemExit("tag must use lowercase letters, digits, hyphens or underscores")
    project = pool.remote_project()
    if project != pool.DEFAULT_REMOTE_PROJECT and not tag.startswith("r2"):
        raise SystemExit(f"root {project}: tag must start with 'r2' (got {tag!r})")
    available = pool.dataset_cards(dataset)
    if cards is not None:
        unknown = sorted(set(cards) - set(available))
        if unknown:
            raise SystemExit(f"--cards: not in dataset {dataset!r}: {', '.join(unknown)}")
        available = [card for card in available if card in set(cards)]
    path = queue_path(tag, dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        queue = load_queue(path)
        if queue["project"] != project:
            raise SystemExit(f"{path.name} belongs to root {queue['project']}, not {project}")
        if hosts is not None:
            queue["hosts"] = hosts.split(",")
    else:
        queue = {"dataset": dataset, "tag": tag, "project": project,
                 "hosts": hosts.split(",") if hosts else None, "tasks": []}
    known = {(t["card"], t["variant"]) for t in queue["tasks"]}
    added = 0
    for card in available:
        for variant in variants:
            if (card, variant) in known:
                continue
            done = local_summary(dataset, tag, variant, card).exists()
            queue["tasks"].append({"card": card, "variant": variant, "state": "done" if done else "pending",
                                    "host": None, "hosts": [], "attempts": 0, "dispatched_at": None})
            added += 1
    save_queue(path, queue)
    print(f"{path.name}: root {project}, +{added} tasks, {counts(queue)}")
    return path


# --- probing -----------------------------------------------------------------

def host_procs(host: str) -> int | None:
    """All evaluation processes on the host, every code root (they share its CPUs). None = probe failed."""
    result = pool.ssh_run(host, 'pgrep -fc "[v]4.evaluation"')
    out = result.stdout.strip().splitlines()
    if result.returncode == 1 and not out:  # pgrep: no match
        return 0
    try:
        return int(out[-1])
    except (ValueError, IndexError):
        return None


def probe_queue(host: str, queue: dict) -> tuple[set[tuple[str, str]], bool] | None:
    """(finished (variant, card) pairs of the tag on the host, is a batch of this tag running in the root).
    Must run inside project_env. None = probe failed (never treated as 'dead')."""
    project, tag, dataset = pool.remote_project(), queue["tag"], queue["dataset"]
    base = f"{project}/v4/results/{dataset}-r2/{tag}"
    script = (f'ls {base}/*/*/summary.json 2>/dev/null; echo "@@RUN"; proj=$(readlink -f {project}); '
              f'for p in $(pgrep -f -- "--tag {tag}( |$)"); do c=$(readlink -f /proc/$p/cwd 2>/dev/null); '
              f'case "$c" in "$proj"|"$proj"/*) echo $p;; esac; done')
    result = pool.ssh_run(host, script, timeout=40)
    if result.returncode != 0 or "@@RUN" not in result.stdout:
        return None
    head, _, tail = result.stdout.partition("@@RUN")
    done = set()
    for line in head.splitlines():
        parts = line.strip().split("/")
        if len(parts) >= 3 and parts[-1] == "summary.json":
            done.add((parts[-3], parts[-2]))
    return done, bool(tail.strip())


def reconcile(queue: dict, now: float, timeout: float) -> bool:
    """Update running tasks from the hosts they run on. Returns True if any task became done."""
    changed = False
    for task in queue["tasks"]:
        if task["state"] == "pending" and local_summary(queue["dataset"], queue["tag"], task["variant"],
                                                        task["card"]).exists():
            task["state"], changed = "done", True
    by_host: dict[str, list[dict]] = {}
    for task in queue["tasks"]:
        if task["state"] == "running":
            by_host.setdefault(task["host"], []).append(task)
    for host, tasks in by_host.items():
        probe = probe_queue(host, queue)
        if probe is None:
            print(f"  {host}: probe failed, keeping {len(tasks)} running tasks as they are")
            continue
        done, running = probe
        for task in tasks:
            age = now - task["dispatched_at"]
            if (task["variant"], task["card"]) in done:
                task["state"], changed = "done", True
            elif (not running and age > GRACE) or age > timeout:
                reason = "batch gone" if not running else f"timeout {int(age)}s"
                task["attempts"] += 1
                task["state"] = "failed" if task["attempts"] >= MAX_ATTEMPTS else "pending"
                print(f"  {host}: {task['card']}/{task['variant']} {reason} -> {task['state']} "
                      f"(attempt {task['attempts']})")
                task["host"], task["dispatched_at"] = None, None
                if "timeout" in reason:
                    task["avoid"] = sorted({*task.get("avoid", []), host})
    return changed


# --- dispatch ----------------------------------------------------------------

def free_slots(jobs: int, procs: int, reserved: int) -> int:
    return max(0, jobs - procs - reserved)


def pick_tasks(pending: list[dict], host: str, n: int) -> list[dict]:
    picked = []
    for task in pending:
        if len(picked) >= n:
            break
        if host in task.get("avoid", []):
            continue
        picked.append(task)
    return picked


def dispatch_round(queues: list[tuple[Path, dict]], hosts_cfg: dict, timeout: float, chunk: int,
                   now: float | None = None, max_per_host: int | None = None) -> bool:
    """One round over all queues. Returns True if any task became done (a pull is then worthwhile)."""
    now = time.time() if now is None else now
    any_done = False
    for path, queue in queues:
        with project_env(queue["project"]):
            if reconcile(queue, now, timeout):
                any_done = True
        save_queue(path, queue)
    wanted = [(p, q) for p, q in queues if any(t["state"] == "pending" for t in q["tasks"])]
    if not wanted:
        return any_done
    candidates = sorted({h for _, q in wanted for h in (q["hosts"] or pool.enabled_hosts(hosts_cfg))})
    state: dict[str, int] = {}
    for host in candidates:
        if not pool.is_online(host):
            print(f"  {host}: offline, skip")
            continue
        procs = host_procs(host)
        if procs is None:
            print(f"  {host}: process probe failed, skip")
            continue
        reserved = sum(1 for _, q in queues for t in q["tasks"]
                       if t["state"] == "running" and t["host"] == host and now - t["dispatched_at"] < GRACE)
        state[host] = free_slots(hosts_cfg["hosts"][host]["jobs"], procs, reserved)
        if max_per_host is not None:  # cap on this invocation's own running tasks per host
            mine = sum(1 for _, q in queues for t in q["tasks"] if t["state"] == "running" and t["host"] == host)
            state[host] = min(state[host], max(0, max_per_host - mine))
    for host in sorted(state, key=lambda h: (-state[h], h)):
        for path, queue in wanted:
            if state[host] <= 0:
                break
            if queue["hosts"] and host not in queue["hosts"]:
                continue
            pending = [t for t in queue["tasks"] if t["state"] == "pending"]
            picked = pick_tasks(pending, host, min(state[host], chunk))
            if not picked:
                continue
            variant_cards: dict[str, list[str]] = {}
            for t in picked:
                variant_cards.setdefault(t["variant"], []).append(t["card"])
            with project_env(queue["project"]):
                pool.start_remote_batch(host, queue["dataset"], queue["tag"], variant_cards,
                                         hosts_cfg["hosts"][host]["jobs"])
            for t in picked:
                t.update(state="running", host=host, dispatched_at=now)
                t["hosts"] = [*t["hosts"], host]
            state[host] -= len(picked)
            save_queue(path, queue)
            print(f"  {host}: +{len(picked)} tasks of {queue['tag']} (free slots left {state[host]})")
    return any_done


def load_queues(tags: list[str] | None) -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(queue_dir().glob("queue-*.json")):
        queue = load_queue(path)
        if tags and queue["tag"] not in tags:
            continue
        if not finished(queue):
            out.append((path, queue))
    return out


def cmd_status(tags: list[str] | None) -> None:
    for path in sorted(queue_dir().glob("queue-*.json")):
        queue = load_queue(path)
        if tags and queue["tag"] not in tags:
            continue
        per_host: dict[str, int] = {}
        for t in queue["tasks"]:
            if t["state"] == "running":
                per_host[t["host"]] = per_host.get(t["host"], 0) + 1
        print(f"{path.name}: root {queue['project']} {counts(queue)} running_on={per_host}")


def cmd_dispatch(loop: int | None, tags: list[str] | None, timeout: float, chunk: int, pull: bool = True,
                 max_per_host: int | None = None) -> None:
    while True:
        queues = load_queues(tags)
        if not queues:
            print("dispatch: no unfinished queues")
            return
        hosts_cfg = pool.load_hosts()
        any_done = dispatch_round(queues, hosts_cfg, timeout, chunk, max_per_host=max_per_host)
        for path, queue in queues:
            print(f"{path.name}: {counts(queue)}")
        everything_done = all(finished(q) for _, q in queues)
        if pull and (any_done or everything_done):
            pool.pull_once(hosts_cfg)
        if not loop or everything_done:
            return
        time.sleep(loop)
