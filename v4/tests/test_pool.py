"""weighted_assign/_split_jobs over (card, variant) tasks, and cmd_run/cmd_requeue wiring."""

from __future__ import annotations

import json

from v4.evaluation import pool


def test_weighted_assign_is_deterministic_and_proportional():
    tasks = [(f"c{i}", variant) for i in range(12) for variant in ("default", "alt")]
    jobs = {"a": 32, "b": 32, "c": 128}
    first = pool.weighted_assign(tasks, jobs)
    second = pool.weighted_assign(tasks, jobs)
    assert first == second

    counts = {host: len(items) for host, items in first.items()}
    total = len(tasks)
    assert sum(counts.values()) == total
    assert counts["c"] > counts["a"] > 0
    assert counts["c"] > counts["b"] > 0
    for host, share in (("a", 32), ("b", 32), ("c", 128)):
        ideal = total * share / sum(jobs.values())
        assert abs(counts[host] - ideal) <= 1


def test_split_jobs_full_concurrency_when_it_already_fits_budget():
    assert pool._split_jobs({"default": 5, "alt": 3}, 32) == {"default": 5, "alt": 3}


def test_split_jobs_proportional_and_capped_when_over_budget():
    sizes = {"default": 90, "alt": 10}
    jobs = pool._split_jobs(sizes, 32)
    assert sum(jobs.values()) <= 32
    assert jobs["default"] > jobs["alt"] > 0
    assert jobs["default"] <= sizes["default"]
    assert jobs["alt"] <= sizes["alt"]


def test_split_jobs_falls_back_to_one_per_group_when_groups_exceed_budget():
    jobs = pool._split_jobs({"v1": 10, "v2": 1, "v3": 1}, 2)
    assert jobs == {"v1": 1, "v2": 1}


def test_run_assigns_card_variant_tasks_across_hosts_and_writes_table(monkeypatch, tmp_path):
    monkeypatch.setattr(pool, "ROOT", tmp_path)
    monkeypatch.setattr(pool, "specifications", lambda dataset: [{"name": f"c{i}"} for i in range(4)])
    monkeypatch.setattr(pool, "load_hosts", lambda: {"hosts": {"h1": {"jobs": 1}, "h2": {"jobs": 1}}})
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    started = {}
    monkeypatch.setattr(
        pool, "start_remote_batch",
        lambda host, dataset, tag, variant_cards, host_jobs: started.setdefault(host, variant_cards),
    )

    pool.cmd_run("ds", ["default", "alt"], "tag2", "h1,h2")

    assert set(started) == {"h1", "h2"}
    all_pairs = {(card, variant) for vc in started.values() for variant, cards in vc.items() for card in cards}
    assert all_pairs == {(f"c{i}", variant) for i in range(4) for variant in ("default", "alt")}
    table = tmp_path / "v4" / "results" / "remote" / "pool" / "tag2-ds.json"
    assert json.loads(table.read_text(encoding="utf-8"))


def test_requeue_noop_when_nothing_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pool, "ROOT", tmp_path)
    monkeypatch.setattr(pool, "specifications", lambda dataset: [{"name": "c0"}])
    result_root = tmp_path / "v4" / "results" / "remote" / "ds-r2" / "tag1"
    (result_root / "default" / "c0").mkdir(parents=True)
    (result_root / "default" / "c0" / "summary.json").write_text("{}", encoding="utf-8")
    started = []
    monkeypatch.setattr(pool, "start_remote_batch", lambda *a, **k: started.append(a))

    pool.cmd_requeue("ds", ["default"], "tag1")

    assert started == []
    assert "nothing missing" in capsys.readouterr().out


def test_requeue_backfills_only_missing_card_variant_pairs(monkeypatch, tmp_path):
    monkeypatch.setattr(pool, "ROOT", tmp_path)
    monkeypatch.setattr(pool, "specifications", lambda dataset: [{"name": f"c{i}"} for i in range(3)])
    monkeypatch.setattr(pool, "load_hosts", lambda: {"hosts": {"h1": {"jobs": 4, "enabled": True}}})
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    monkeypatch.setattr(pool, "host_running_tag", lambda host, tag: False)

    result_root = tmp_path / "v4" / "results" / "remote" / "ds-r2" / "tag1"
    for variant in ("default", "alt"):  # c0: both variants already present, nothing missing
        (result_root / variant / "c0").mkdir(parents=True)
        (result_root / variant / "c0" / "summary.json").write_text("{}", encoding="utf-8")
    (result_root / "default" / "c1").mkdir(parents=True)  # c1: only "default" present, "alt" missing
    (result_root / "default" / "c1" / "summary.json").write_text("{}", encoding="utf-8")
    # c2: neither variant present, both missing

    started = []
    monkeypatch.setattr(
        pool, "start_remote_batch",
        lambda host, dataset, tag, variant_cards, host_jobs: started.append((host, variant_cards)),
    )

    pool.cmd_requeue("ds", ["default", "alt"], "tag1")

    assert len(started) == 1
    host, variant_cards = started[0]
    assert host == "h1"
    assert variant_cards == {"default": ["c2"], "alt": ["c1", "c2"]}

    table = json.loads((tmp_path / "v4" / "results" / "remote" / "pool" / "tag1-ds.json").read_text(encoding="utf-8"))
    assert sorted(tuple(pair) for pair in table["h1"]) == [("c1", "alt"), ("c2", "alt"), ("c2", "default")]


# --- second code root (POOL_REMOTE_PROJECT / POOL_LOCAL_ROOT) ---------------------------------------------

def _captured_launch(monkeypatch):
    sent = []
    monkeypatch.setattr(pool, "ssh_fire_and_forget", lambda host, cmd, timeout=30: sent.append((host, cmd)))
    return sent


def test_default_root_launch_command_is_unchanged(monkeypatch):
    monkeypatch.delenv("POOL_REMOTE_PROJECT", raising=False)
    sent = _captured_launch(monkeypatch)

    pool.start_remote_batch("h1", "ds", "t1", {"default": ["c0", "c1"]}, 8)

    (host, cmd), = sent
    assert host == "h1"
    assert ". /root/gosim/env.sh; cd /root/gosim/GOSIM26; " in cmd
    assert "/root/gosim/runs/t1-ds-h1.log" in cmd
    assert ">> /root/gosim/runs/t1-ds-h1-default.log 2>&1 &" in cmd
    assert "@" not in cmd and "GOSIM26-r2" not in cmd


def test_second_root_launch_runs_from_that_root_and_tags_its_logs(monkeypatch):
    monkeypatch.setenv("POOL_REMOTE_PROJECT", "/root/gosim/GOSIM26-r2/")
    sent = _captured_launch(monkeypatch)

    pool.start_remote_batch("h1", "ds", "r2t", {"default": ["c0"], "alt": ["c0"]}, 8)

    (_, cmd), = sent
    assert "cd /root/gosim/GOSIM26-r2; " in cmd
    assert "cd /root/gosim/GOSIM26;" not in cmd
    # the host-level env.sh and the shared runs/ directory are not per-root; only the log names are
    assert ". /root/gosim/env.sh;" in cmd
    assert "/root/gosim/runs/r2t-ds-h1@GOSIM26-r2.log" in cmd
    assert ">> /root/gosim/runs/r2t-ds-h1-alt@GOSIM26-r2.log 2>&1 &" in cmd


def test_remote_project_rejects_paths_that_would_break_remote_shell_text(monkeypatch):
    import pytest

    for bad in ("relative/GOSIM26", "/root/gosim/a b", "/root/gosim/x'y", "/root/gosim/../etc", "/root/gosim/$(id)"):
        monkeypatch.setenv("POOL_REMOTE_PROJECT", bad)
        with pytest.raises(SystemExit):
            pool.remote_project()


def test_host_running_tag_matches_whole_tag_and_only_inside_the_current_root(monkeypatch):
    sent = []
    monkeypatch.setattr(pool, "ssh_run", lambda host, cmd, timeout=20: (
        sent.append(cmd) or type("R", (), {"returncode": 0, "stdout": "123\n", "stderr": ""})()))
    monkeypatch.setenv("POOL_REMOTE_PROJECT", "/root/gosim/GOSIM26-r2")

    assert pool.host_running_tag("h1", "r2probe") is True

    cmd, = sent
    assert '--tag r2probe( |$)' in cmd  # "w3" must not match "w3ac"
    assert "readlink -f /root/gosim/GOSIM26-r2" in cmd and "/proc/$p/cwd" in cmd


def _fake_ssh(responses, log):
    def fake(host, cmd, timeout=20):
        log.append((host, cmd))
        for needle, (code, out) in responses.items():
            if needle in cmd:
                return type("R", (), {"returncode": code, "stdout": out, "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    return fake


def test_sync_refuses_code_push_into_a_root_with_running_batches(monkeypatch):
    monkeypatch.delenv("POOL_REMOTE_PROJECT", raising=False)
    monkeypatch.setattr(pool, "source_sha_local", lambda: "abc")
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    ssh_log, rsyncs = [], []
    monkeypatch.setattr(pool, "ssh_run", _fake_ssh({"pgrep": (0, "38\n")}, ssh_log))
    monkeypatch.setattr(pool.subprocess, "run", lambda args, **kw: rsyncs.append(args))

    pool.cmd_sync(["h1"], experiments_only=False)

    assert rsyncs == []  # nothing was pushed under the running batches


def test_sync_force_and_experiments_only_bypass_the_guard_and_target_the_chosen_root(monkeypatch, tmp_path):
    monkeypatch.setenv("POOL_REMOTE_PROJECT", "/root/gosim/GOSIM26-r2")
    monkeypatch.setattr(pool, "source_sha_local", lambda: "abc")
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    monkeypatch.setattr(pool, "local_root", lambda: tmp_path)
    ssh_log, rsyncs = [], []
    monkeypatch.setattr(pool, "ssh_run", _fake_ssh({"pgrep": (0, "5\n"), "source_sha256": (0, "abc\n")}, ssh_log))
    monkeypatch.setattr(pool.subprocess, "run", lambda args, **kw: rsyncs.append(args))

    pool.cmd_sync(["h1"], experiments_only=False, force=True)
    assert [a[-1] for a in rsyncs] == [f"h1:/root/gosim/GOSIM26-r2/{d}/" for d in pool.SYNC_DIRS]
    assert [a[-2] for a in rsyncs] == [f"{tmp_path / d}/" for d in pool.SYNC_DIRS]
    assert any("cd /root/gosim/GOSIM26-r2 &&" in cmd for _, cmd in ssh_log)

    rsyncs.clear()
    pool.cmd_sync(["h1"], experiments_only=True)
    assert [a[-1] for a in rsyncs] == ["h1:/root/gosim/GOSIM26-r2/v4/experiments/"]


def test_sync_pushes_when_the_root_is_idle(monkeypatch, tmp_path):
    monkeypatch.setenv("POOL_REMOTE_PROJECT", "/root/gosim/GOSIM26-r2")
    monkeypatch.setattr(pool, "source_sha_local", lambda: "abc")
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    monkeypatch.setattr(pool, "local_root", lambda: tmp_path)
    rsyncs = []
    monkeypatch.setattr(pool, "ssh_run", _fake_ssh({"pgrep": (0, "0\n"), "source_sha256": (0, "abc\n")}, []))
    monkeypatch.setattr(pool.subprocess, "run", lambda args, **kw: rsyncs.append(args))

    pool.cmd_sync(["h1"], experiments_only=False)

    assert len(rsyncs) == len(pool.SYNC_DIRS)


def test_init_root_refuses_the_default_root(monkeypatch):
    import pytest

    monkeypatch.setenv("POOL_REMOTE_PROJECT", pool.DEFAULT_REMOTE_PROJECT)
    with pytest.raises(SystemExit):
        pool.cmd_init_root(["h1"])


def test_init_root_defaults_to_the_second_root_and_shares_venv_and_cards(monkeypatch, tmp_path):
    monkeypatch.delenv("POOL_REMOTE_PROJECT", raising=False)
    (tmp_path / "v4" / "replay").mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock", ".python-version", "v4/__init__.py"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(pool, "local_root", lambda: tmp_path)
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    ssh_log, rsyncs = [], []
    monkeypatch.setattr(pool, "ssh_run", _fake_ssh({}, ssh_log))
    monkeypatch.setattr(pool.subprocess, "run",
                        lambda args, **kw: (rsyncs.append((args, kw)) or type("R", (), {"returncode": 0, "stderr": ""})()))

    pool.cmd_init_root(["h1"])

    (_, script), = ssh_log
    assert "P=/root/gosim/GOSIM26-r2; D=/root/gosim/GOSIM26" in script
    assert 'link "$D/.venv" "$P/.venv"' in script and 'link "$D/v4/cards" "$P/v4/cards"' in script
    (args, kw), = rsyncs
    assert args[-1] == "h1:/root/gosim/GOSIM26-r2/" and kw["cwd"] == tmp_path
    assert set(args[3:-1]) >= {"pyproject.toml", "uv.lock", ".python-version", "v4/__init__.py", "v4/replay"}
    assert "v4/README.md" not in args  # absent locally, so not offered to rsync


def test_pull_reads_every_existing_root_into_the_same_local_tree(monkeypatch, tmp_path):
    monkeypatch.delenv("POOL_REMOTE_PROJECT", raising=False)
    monkeypatch.setattr(pool, "ROOT", tmp_path)
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    monkeypatch.setattr(pool, "ssh_run", _fake_ssh({"for d in": (0, "/root/gosim/GOSIM26\n/root/gosim/GOSIM26-r2\n")}, []))
    rsyncs = []
    monkeypatch.setattr(pool.subprocess, "run", lambda args, **kw: rsyncs.append(args))

    pool.pull_once({"hosts": {"h1": {"jobs": 1, "enabled": True}}})

    sources = [a[-2] for a in rsyncs]
    assert "h1:/root/gosim/GOSIM26/v4/results/" in sources
    assert "h1:/root/gosim/GOSIM26-r2/v4/results/" in sources
    dest = f"{tmp_path / 'v4' / 'results' / 'remote'}/"
    assert all(a[-1] == dest for a in rsyncs if a[-2].endswith("/v4/results/"))
    assert "h1:/root/gosim/runs/*.log" in sources  # logs are host-level, pulled once


def test_pull_falls_back_to_the_original_root_when_the_probe_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(pool, "ROOT", tmp_path)
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    monkeypatch.setattr(pool, "ssh_run", _fake_ssh({"for d in": (255, "")}, []))
    rsyncs = []
    monkeypatch.setattr(pool.subprocess, "run", lambda args, **kw: rsyncs.append(args))

    pool.pull_once({"hosts": {"h1": {"jobs": 1, "enabled": True}}})

    assert [a[-2] for a in rsyncs if a[-2].endswith("/v4/results/")] == ["h1:/root/gosim/GOSIM26/v4/results/"]


def test_run_can_be_restricted_to_chosen_cards(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setattr(pool, "ROOT", tmp_path)
    monkeypatch.setattr(pool, "specifications", lambda dataset: [{"name": f"c{i}"} for i in range(4)])
    monkeypatch.setattr(pool, "load_hosts", lambda: {"hosts": {"h1": {"jobs": 4}}})
    monkeypatch.setattr(pool, "is_online", lambda host, timeout=8: True)
    started = []
    monkeypatch.setattr(pool, "start_remote_batch",
                        lambda host, dataset, tag, variant_cards, host_jobs: started.append(variant_cards))

    pool.cmd_run("ds", ["default"], "probe", "h1", ["c2"])
    assert started == [{"default": ["c2"]}]
    with pytest.raises(SystemExit):
        pool.cmd_run("ds", ["default"], "probe", "h1", ["nope"])


def test_local_root_selects_the_tree_that_is_hashed_and_listed(monkeypatch, tmp_path):
    monkeypatch.setattr(pool, "ROOT", tmp_path / "main")
    monkeypatch.setenv("POOL_LOCAL_ROOT", str(tmp_path / "worktree"))
    (tmp_path / "worktree").mkdir()
    calls = []

    def fake_run(args, **kw):
        calls.append((args, kw))
        return type("R", (), {"returncode": 0, "stdout": '["c0", "c1"]\n', "stderr": ""})()

    monkeypatch.setattr(pool.subprocess, "run", fake_run)

    pool.source_sha_local()
    assert pool.dataset_cards("ds") == ["c0", "c1"]
    # both run python inside the selected tree, so `v4` is imported from there and ROOT resolves to it
    assert [kw["cwd"] for _, kw in calls] == [(tmp_path / "worktree").resolve()] * 2
    assert "source_sha256" in calls[0][0][2]


def test_unset_local_root_keeps_using_this_repository(monkeypatch, tmp_path):
    monkeypatch.delenv("POOL_LOCAL_ROOT", raising=False)
    monkeypatch.setattr(pool, "ROOT", tmp_path)
    assert pool.local_root() == tmp_path
