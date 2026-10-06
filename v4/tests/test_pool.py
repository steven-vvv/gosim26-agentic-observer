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
