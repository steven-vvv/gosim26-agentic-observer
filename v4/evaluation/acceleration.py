"""Semantics-preserving local acceleration; the organizer checkout stays unchanged.

The runner only consumes field hits for explicitly assigned targets. Restricting
that internal candidate scan therefore leaves every participant-visible result
unchanged. The ledger caches are rebuilt in original order after invalidation.
Geometry still uses the organizer's formulas and exact 120-second sample times.
"""

from __future__ import annotations

import ast
import functools
import math
import sys
import types
from contextlib import contextmanager
from datetime import timedelta

from .upstream import resolve


class _AssignedTargets(ast.NodeTransformer):
    def __init__(self):
        self.replacements = 0

    def visit_Assign(self, node):
        if any(isinstance(target, ast.Name) and target.id == "candidate_targets" for target in node.targets):
            if not (
                isinstance(node.value, ast.IfExp)
                and isinstance(node.value.body, ast.Attribute)
                and node.value.body.attr == "targets"
                and isinstance(node.value.test, ast.Name)
                and node.value.test.id == "center_in_range"
            ):
                raise RuntimeError("upstream field candidate structure changed; review acceleration")
            node.value.body = ast.parse(
                "[targets_by_id[target_id] for target_id in action['assignments'].values()]", mode="eval"
            ).body
            self.replacements += 1
        return self.generic_visit(node)


class _RunningTotal(ast.NodeTransformer):
    """Replace the snapshot's per-decision sum over the best scores with the ledger's cache."""

    EXPECTED = "round(sum((item[1] for item in ledger.best().values())), 6)"

    def __init__(self):
        self.replacements = 0

    def visit_Dict(self, node):
        for index, key in enumerate(node.keys):
            if isinstance(key, ast.Constant) and key.value == "running_total":
                if ast.unparse(node.values[index]) != self.EXPECTED:
                    raise RuntimeError("upstream running_total expression changed; review acceleration")
                node.values[index] = ast.parse("ledger.running_total()", mode="eval").body
                self.replacements += 1
        return self.generic_visit(node)


def _ledger_type(base):
    class IncrementalLedger(base):
        """Caches the three per-decision scans the runner repeats over the whole ledger.

        ``request_factors`` is called once per live request on every decision; the plain
        implementation walks every entry, so a long season makes the runner quadratic.
        Entries only ever append, and ``invalidate_window`` rebuilds from scratch, so a
        per-window cursor folds each new entry in exactly once.
        """

        def __init__(self):
            super().__init__()
            self._best = {}
            self._factors = {}
            self._windows = {}
            self._total = None

        def running_total(self):
            """The runner's snapshot total, recomputed only after the ledger changed."""
            if self._total is None:
                self._total = round(sum(item[1] for item in self._best.values()), 6)
            return self._total

        def record(self, action_index, target_id, factor, score, start_utc=None, end_utc=None):
            super().record(action_index, target_id, factor, score, start_utc, end_utc)
            self._update(self.entries[-1])

        def _update(self, entry):
            self._total = None
            old = self._best.get(entry.target_id)
            if old is None or entry.score > old[1]:
                self._best[entry.target_id] = (entry.factor, entry.score)
            self._factors[entry.target_id] = max(self._factors.get(entry.target_id, 0.0), entry.factor)

        def invalidate_window(self, first, end, event_id):
            count = super().invalidate_window(first, end, event_id)
            self._best = {}
            self._factors = {}
            self._windows = {}
            self._total = None
            for entry in self.entries:
                if entry.valid:
                    self._update(entry)
            return count

        def request_factors(self, available_from, deadline):
            state = self._windows.get((available_from, deadline))
            if state is None:
                state = ({}, 0)
            factors, seen = state
            entries = self.entries
            for index in range(seen, len(entries)):
                entry = entries[index]
                if (
                    entry.valid
                    and entry.start_utc is not None
                    and entry.end_utc is not None
                    and available_from <= entry.start_utc
                    and entry.end_utc <= deadline
                ):
                    factors[entry.target_id] = max(factors.get(entry.target_id, 0.0), entry.factor)
            self._windows[(available_from, deadline)] = (factors, len(self.entries))
            return factors

        def best(self):
            return self._best

        def max_factors(self):
            return self._factors

    return IncrementalLedger


@functools.lru_cache(maxsize=1)
def fast_runner():
    clone = resolve()
    from challenge import tile_geometry_simulator, v4_fiber_map, v4_runner, v4_scorer

    path = clone / "challenge" / "v4_runner.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    transformer = _AssignedTargets()
    tree = transformer.visit(tree)
    if transformer.replacements != 1:
        raise RuntimeError("upstream field candidate assignment count changed")
    totals = _RunningTotal()
    tree = totals.visit(tree)
    if totals.replacements != 1:
        raise RuntimeError("upstream running_total count changed")
    module = types.ModuleType("challenge._gosim26_v4_fast_runner")
    module.__package__ = "challenge"
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), module.__dict__)  # noqa: S102
    # The workflow raises the original class; the accelerated runner must catch it.
    module.AgentTermination = v4_runner.AgentTermination
    module.BestLedger = _ledger_type(v4_scorer.BestLedger)
    original_altaz = v4_fiber_map.radec_to_altaz
    memo_altaz = functools.lru_cache(maxsize=131072)(original_altaz)

    exact = "step_seconds does not affect" in (v4_fiber_map.min_altitude_during.__doc__ or "") or \
        "Exact minimum altitude" in (v4_fiber_map.min_altitude_during.__doc__ or "")

    def sampled_minimum(ra, dec, start, end, config, step_seconds=60):
        if end <= start or step_seconds <= 0:
            return v4_fiber_map.min_altitude_during(ra, dec, start, end, config, step_seconds)
        latitude = float(config["site"]["latitude_deg"])
        longitude = float(config["site"]["longitude_deg"])
        span = (end - start).total_seconds()
        if span >= 86400:
            return v4_fiber_map.min_altitude_during(ra, dec, start, end, config, step_seconds)
        lst = tile_geometry_simulator._local_sidereal_deg(start, longitude)
        to_bottom = (180.0 - (lst - ra)) % 360.0 / (360.98564736629 / 86400.0)
        samples = {start, end}
        if to_bottom <= span + step_seconds:
            closest = math.floor(to_bottom / step_seconds)
            for k in (closest - 1, closest, closest + 1):
                if 0 <= k * step_seconds < span:
                    samples.add(start + timedelta(seconds=k * step_seconds))
        return min(memo_altaz(ra, dec, sample, latitude, longitude)[0] for sample in samples)

    if not exact:
        # Older engines sampled the track every 120 s; keep their exact sample points.
        module.min_altitude_during = sampled_minimum
    module.radec_to_altaz = memo_altaz
    return module


@contextmanager
def accelerated_engine():
    resolve()
    from challenge import v4_workflow

    original = v4_workflow.run_scenario
    v4_workflow.run_scenario = fast_runner().run_scenario
    try:
        yield
    finally:
        v4_workflow.run_scenario = original
