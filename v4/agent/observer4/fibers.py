"""Best-target-per-fibre reduction of the anchor search (moved out of planning.py)."""

from __future__ import annotations

import numpy as np

# fiber_totals adds fibres through a dense (fibre, centre, duration) table up to this many
# cells (1 MiB); larger tables, mostly empty, cost more than summing nonempty fibres.
DENSE_FIBER_CELLS = 1 << 17


def _rank_order(starts: np.ndarray, sizes: np.ndarray):
    """Rank-major order of contiguous row segments (first rows ``starts``, lengths ``sizes``).

    Rank r lists the r-th row of every segment longer than r, longest segments first (a
    stable order), so the segments present at each rank are a prefix of that order and an
    elementwise reduction over each segment in row order takes one slice per rank.
    Returns the row order, the segment order and the number of segments at each rank.
    """
    segments = np.argsort(-sizes, kind="stable")
    per_rank = len(sizes) - np.cumsum(np.bincount(sizes))[:-1]
    offsets = np.cumsum(per_rank) - per_rank
    slots = np.arange(int(per_rank[-1] + offsets[-1])) - np.repeat(offsets, per_rank)
    return starts[segments][slots] + np.repeat(np.arange(len(per_rank)), per_rank), segments, per_rank.tolist()


def _segments(keys: np.ndarray):
    """First index and length of each run of equal values in ``keys`` (not empty)."""
    boundary = np.empty(len(keys), dtype=bool)
    boundary[0] = True
    np.not_equal(keys[1:], keys[:-1], out=boundary[1:])
    starts = np.flatnonzero(boundary)
    return starts, np.diff(starts, append=len(keys))


def fiber_totals(fibers: np.ndarray, gains: np.ndarray, weight: np.ndarray, count: int) -> np.ndarray:
    """Sum the best target per fiber for each candidate center and duration.

    Only centre-target pairs inside a fibre are reduced; the result is bitwise equal to the
    dense reduction over every pair, including its zero floor and summation order. Each
    (centre, fibre) maximum takes its targets in index order, rank by rank (see
    _rank_order), as np.maximum.reduceat did; each centre then adds its fibres in fibre
    order, through a dense (fibre, centre) table when that is small and otherwise over its
    nonempty fibres only.
    """
    centers, targets = fibers.shape
    flat = np.flatnonzero(fibers >= 0)
    if not len(flat):
        return np.zeros((centers, gains.shape[0]))
    labels = flat // targets * count + fibers.ravel()[flat]
    # A stable sort depends only on the keys; 16-bit keys take numpy's radix sort.
    keys = labels.astype(np.int16) if count * centers <= np.iinfo(np.int16).max else labels
    order = np.argsort(keys, kind="stable")
    labels = labels[order]
    starts, sizes = _segments(labels)
    rows, groups, per_rank = _rank_order(starts, sizes)
    pairs = flat[order[rows]]
    target = pairs % targets
    pair_weight = weight.ravel()[pairs][:, None]
    table = np.ascontiguousarray(gains.T)
    first = per_rank[0]
    maxima = table[target[:first]]
    maxima *= pair_weight[:first]
    buffer = np.empty_like(maxima)
    start = first
    for n in per_rank[1:]:
        part = buffer[:n]
        table.take(target[start:start + n], axis=0, out=part)
        part *= pair_weight[start:start + n]
        np.maximum(maxima[:n], part, out=maxima[:n])
        start += n
    # The dense search included zero for every target outside a fiber; only fibres
    # holding every target (the longest groups, so a prefix) had none.
    full = int(np.count_nonzero(sizes == targets))
    np.maximum(maxima[full:], 0, out=maxima[full:])
    if count * centers * gains.shape[0] <= DENSE_FIBER_CELLS:
        # Sequential sum over fibres, the same order as np.add.accumulate.
        totals = np.zeros((count * centers, gains.shape[0]))
        group_labels = labels[starts][groups]
        totals[group_labels % count * centers + group_labels // count] = maxima
        totals = totals.reshape(count, centers, -1)
        result = totals[0].copy()
        for fiber in range(1, count):
            result += totals[fiber]
        return result
    # A centre's groups are contiguous in label order and ordered by fibre. The dense sum
    # also added +0.0 for each empty fibre, which can only turn a -0.0 running sum into
    # +0.0 for good; centres with an empty fibre therefore add a single zero at the end.
    position = np.empty(len(groups), dtype=np.intp)
    position[groups] = np.arange(len(groups))
    group_centers = labels[starts] // count
    center_starts, center_sizes = _segments(group_centers)
    rows, segments, per_rank = _rank_order(center_starts, center_sizes)
    rows = position[rows]
    first = per_rank[0]
    sums = maxima[rows[:first]]
    start = first
    for n in per_rank[1:]:
        part = buffer[:n]
        maxima.take(rows[start:start + n], axis=0, out=part)
        np.add(sums[:n], part, out=sums[:n])
        start += n
    sums[center_sizes[segments] < count] += 0.0
    result = np.zeros((centers, gains.shape[0]))
    result[group_centers[center_starts[segments]]] = sums
    return result
