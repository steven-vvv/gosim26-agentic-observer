"""Queue and dispatch logic of pool_dispatch with ssh/rsync mocked out."""

from __future__ import annotations

import pytest

from v4.evaluation import pool
from v4.evaluation import pool_dispatch as pd

HOSTS = {"hosts": {"fast": {"jobs": 32}, "slow": {"jobs": 4}}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(pd, "QUEUE_DIR", tmp_path / "pool")
    monkeypatch.setattr(pool, "ROOT", tmp_path)
    monkeypatch.setattr(pool, "dataset_cards", lambda dataset: [f"c{i}" for i in range(10)])
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    monkeypatch.delenv("POOL_REMOTE_PROJECT", raising=False)
    launched = []
    monkeypatch.setattr(pool, "start_remote_batch",
                        lambda host, ds, tag, vc, jobs: launched.append((host, tag, pool.remote_project(), vc)))
    return launched


def fake_remote(monkeypatch, procs, done=(), tag_running=True):
    monkeypatch.setattr(pd, "host_procs", lambda host: procs[host])
    monkeypatch.setattr(pd, "probe_queue", lambda host, queue: (set(done), tag_running))


def test_submit_second_root_needs_r2_tag_and_remembers_root(env, monkeypatch):
    monkeypatch.setenv("POOL_REMOTE_PROJECT", "/root/gosim/GOSIM26-r2")
    with pytest.raises(SystemExit):
        pd.cmd_submit("ds", ["default"], "plain", None, None)
    path = pd.cmd_submit("ds", ["default", "alt"], "r2x", None, ["c1", "c2"])
    queue = pd.load_queue(path)
    assert queue["project"] == "/root/gosim/GOSIM26-r2"
    assert len(queue["tasks"]) == 4
    pd.cmd_submit("ds", ["default", "alt"], "r2x", None, ["c1", "c2", "c3"])  # idempotent top-up
    assert len(pd.load_queue(path)["tasks"]) == 6


def test_submit_default_root_any_tag_and_marks_pulled_results_done(env, tmp_path):
    out = tmp_path / "v4" / "results" / "remote" / "ds-r2" / "t1" / "default" / "c0"
    out.mkdir(parents=True)
    (out / "summary.json").write_text("{}")
    path = pd.cmd_submit("ds", ["default"], "t1", None, ["c0", "c1"])
    states = {t["card"]: t["state"] for t in pd.load_queue(path)["tasks"]}
    assert states == {"c0": "done", "c1": "pending"}


def test_dispatch_fills_free_slots_proportionally_in_chunks(env, monkeypatch):
    pd.cmd_submit("ds", ["default"], "t1", None, None)
    fake_remote(monkeypatch, {"fast": 0, "slow": 3})
    queues = pd.load_queues(None)
    pd.dispatch_round(queues, HOSTS, pd.DEFAULT_TIMEOUT, chunk=8, now=1000.0)
    sent = {host: sum(len(c) for c in vc.values()) for host, _, _, vc in env}
    assert sent == {"fast": 8, "slow": 1}  # slow has one free slot, fast is capped by the chunk
    tasks = queues[0][1]["tasks"]
    assert sum(t["state"] == "running" for t in tasks) == 9

    # next round: launched tasks are reserved (not visible as processes yet), the rest goes to fast
    pd.dispatch_round(pd.load_queues(None), HOSTS, pd.DEFAULT_TIMEOUT, chunk=8, now=1010.0)
    assert sum(len(c) for h, _, _, vc in env[2:] for c in vc.values() if h == "fast") == 1
    assert not any(h == "slow" for h, *_ in env[2:])


def test_done_dead_and_timeout_handling(env, monkeypatch):
    pd.cmd_submit("ds", ["default"], "t1", "fast", ["c0", "c1", "c2"])
    fake_remote(monkeypatch, {"fast": 0})
    pd.dispatch_round(pd.load_queues(None), HOSTS, pd.DEFAULT_TIMEOUT, 8, now=0.0)
    assert len(env) == 1

    # c0 finished, the batch is still alive: c1/c2 keep running
    fake_remote(monkeypatch, {"fast": 30}, done=[("default", "c0")], tag_running=True)
    pd.dispatch_round(pd.load_queues(None), HOSTS, pd.DEFAULT_TIMEOUT, 8, now=500.0)
    queue = pd.load_queue(pd.queue_path("t1", "ds"))
    assert [t["state"] for t in queue["tasks"]] == ["done", "running", "running"]

    # batch is gone with c1/c2 unfinished: requeued and sent again
    fake_remote(monkeypatch, {"fast": 0}, done=[("default", "c0")], tag_running=False)
    pd.dispatch_round(pd.load_queues(None), HOSTS, pd.DEFAULT_TIMEOUT, 8, now=900.0)
    queue = pd.load_queue(pd.queue_path("t1", "ds"))
    assert [t["attempts"] for t in queue["tasks"]] == [0, 1, 1]
    assert len(env) == 2 and env[1][3] == {"default": ["c1", "c2"]}

    # timeout: avoid the slow host that still runs it
    fake_remote(monkeypatch, {"fast": 5}, done=[("default", "c0")], tag_running=True)
    pd.dispatch_round(pd.load_queues(None), HOSTS, timeout=100, chunk=8, now=2000.0)
    queue = pd.load_queue(pd.queue_path("t1", "ds"))
    assert [t["attempts"] for t in queue["tasks"]] == [0, 2, 2]
    assert queue["tasks"][1]["avoid"] == ["fast"]


def test_attempts_exhausted_marks_failed_and_probe_failure_changes_nothing(env, monkeypatch):
    path = pd.cmd_submit("ds", ["default"], "t1", "fast", ["c0"])
    queue = pd.load_queue(path)
    queue["tasks"][0].update(state="running", host="fast", dispatched_at=0.0, attempts=pd.MAX_ATTEMPTS - 1)
    monkeypatch.setattr(pd, "probe_queue", lambda host, q: None)
    assert not pd.reconcile(queue, 10_000.0, 100.0)
    assert queue["tasks"][0]["state"] == "running"
    monkeypatch.setattr(pd, "probe_queue", lambda host, q: (set(), False))
    pd.reconcile(queue, 10_000.0, 100.0)
    assert queue["tasks"][0]["state"] == "failed"
    assert pd.finished(queue)


def test_queue_roots_are_applied_per_queue_and_env_restored(env, monkeypatch):
    monkeypatch.setenv("POOL_REMOTE_PROJECT", "/root/gosim/GOSIM26-r2")
    pd.cmd_submit("ds", ["default"], "r2a", "fast", ["c0"])
    monkeypatch.delenv("POOL_REMOTE_PROJECT")
    pd.cmd_submit("ds", ["default"], "t1", "fast", ["c0"])
    fake_remote(monkeypatch, {"fast": 0})
    pd.dispatch_round(pd.load_queues(None), HOSTS, pd.DEFAULT_TIMEOUT, 8, now=0.0)
    assert {(tag, proj) for _, tag, proj, _ in env} == {("r2a", "/root/gosim/GOSIM26-r2"),
                                                          ("t1", "/root/gosim/GOSIM26")}
    import os
    assert "POOL_REMOTE_PROJECT" not in os.environ


def test_probe_queue_parses_remote_listing(env, monkeypatch):
    class R:
        returncode = 0
        stdout = ("/r/v4/results/ds-r2/t1/default/c0/summary.json\n/r/v4/results/ds-r2/t1/alt/c3/summary.json\n"
                  "@@RUN\n123\n")
    monkeypatch.setattr(pool, "ssh_run", lambda host, cmd, timeout=20: R)
    done, running = pd.probe_queue("fast", {"tag": "t1", "dataset": "ds"})
    assert done == {("default", "c0"), ("alt", "c3")} and running
