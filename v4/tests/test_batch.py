"""Card selection in batch.run: --shard and --cards must not change scoring logic."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from v4.evaluation import batch


def _fake_specs(names):
    return [{"name": name} for name in names]


def _install(monkeypatch, tmp_path, names):
    for name in names:
        (tmp_path / "v4" / "cards" / name).mkdir(parents=True)
    monkeypatch.setattr(batch, "ROOT", tmp_path)
    monkeypatch.setattr(batch, "resolve", lambda: None)
    monkeypatch.setattr(batch, "specifications", lambda dataset: _fake_specs(names))
    monkeypatch.setattr(batch, "source_sha256", lambda: "deadbeef")
    # Avoid real multiprocessing (the fake _run below is a test-local closure and
    # cannot be pickled to a child process); threads are enough for this logic test.
    monkeypatch.setattr(batch.multiprocessing, "get_all_start_methods", lambda: ["spawn"])
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", concurrent.futures.ThreadPoolExecutor)
    seen = []

    def fake_run(card: Path, output: Path, variant: str, expected_source: str) -> dict:
        seen.append(card.name)
        return {"total": 1.0, "counts": {"required_missing": 0}, "policy_seconds": 1.0,
                "termination_reason": "survey_complete"}

    monkeypatch.setattr(batch, "_run", fake_run)
    return seen


def test_shard_selects_cards_by_index_modulo(monkeypatch, tmp_path):
    names = [f"c{i}" for i in range(6)]
    seen = _install(monkeypatch, tmp_path, names)
    aggregate = batch.run("dataset", ["default"], jobs=1, tag="t", shard="1/3")
    assert aggregate["cards"] == 2
    assert sorted(set(seen)) == ["c1", "c4"]


def test_cards_filter_selects_named_cards_only(monkeypatch, tmp_path):
    names = [f"c{i}" for i in range(6)]
    seen = _install(monkeypatch, tmp_path, names)
    aggregate = batch.run("dataset", ["default"], jobs=1, tag="t", only_cards=["c2", "c5"])
    assert aggregate["cards"] == 2
    assert sorted(set(seen)) == ["c2", "c5"]


def test_shard_and_cards_combine(monkeypatch, tmp_path):
    names = [f"c{i}" for i in range(6)]
    seen = _install(monkeypatch, tmp_path, names)
    aggregate = batch.run("dataset", ["default"], jobs=1, tag="t", shard="0/2", only_cards=["c0", "c3"])
    assert aggregate["cards"] == 1
    assert sorted(set(seen)) == ["c0"]
