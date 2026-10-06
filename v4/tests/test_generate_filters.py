"""Card selection in generate.main: --shard and --cards must match batch.py's semantics."""

from __future__ import annotations

import concurrent.futures
import sys

from v4.evaluation import generate


def _fake_specs(names):
    return [{"name": name} for name in names]


def _install(monkeypatch, names):
    monkeypatch.setattr(generate, "specifications", lambda dataset, specs=None: _fake_specs(names))
    monkeypatch.setattr(generate.multiprocessing, "get_context", lambda method: None)

    def pool_executor(max_workers=None, mp_context=None):
        return concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    monkeypatch.setattr(generate.concurrent.futures, "ProcessPoolExecutor", pool_executor)
    seen = []

    def fake_generate(spec, out):
        seen.append(spec["name"])
        return {"name": spec["name"], "status": "created"}

    monkeypatch.setattr(generate, "generate", fake_generate)
    return seen


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["generate.py", *argv])
    generate.main()


def test_shard_selects_cards_by_index_modulo(monkeypatch, capsys):
    names = [f"c{i}" for i in range(6)]
    seen = _install(monkeypatch, names)
    _run_main(monkeypatch, ["--dataset", "dataset", "--shard", "1/3"])
    assert sorted(seen) == ["c1", "c4"]


def test_cards_filter_selects_named_cards_only(monkeypatch, capsys):
    names = [f"c{i}" for i in range(6)]
    seen = _install(monkeypatch, names)
    _run_main(monkeypatch, ["--dataset", "dataset", "--cards", "c2,c5"])
    assert sorted(seen) == ["c2", "c5"]


def test_shard_and_cards_combine(monkeypatch, capsys):
    names = [f"c{i}" for i in range(6)]
    seen = _install(monkeypatch, names)
    _run_main(monkeypatch, ["--dataset", "dataset", "--shard", "0/2", "--cards", "c0,c3"])
    assert sorted(seen) == ["c0"]


def test_default_behavior_unchanged(monkeypatch, capsys):
    names = [f"c{i}" for i in range(3)]
    seen = _install(monkeypatch, names)
    _run_main(monkeypatch, ["--dataset", "dataset"])
    assert sorted(seen) == names
