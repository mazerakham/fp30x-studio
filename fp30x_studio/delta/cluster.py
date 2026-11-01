"""Onset clustering that remembers which notes went into each event.

:func:`fp30x_studio.align.events.cluster_onsets` folds near simultaneous onsets
into chord events and returns the events. The delta needs the inverse image as
well: which rows of the note table became event ``e``. Rather than keep a
second clustering rule, this reproduces that one exactly, and both callers
assert their events against ``cluster_onsets`` so a change there cannot pass
unnoticed here.
"""

from __future__ import annotations

from ..align.events import CLUSTER_TOL_S

__all__ = ["cluster_with_index"]


def cluster_with_index(onsets, *, tol: float = CLUSTER_TOL_S
                       ) -> tuple[tuple[int, ...], ...]:
    """Group indices of ``onsets`` into clusters.

    ``onsets`` is a sequence of ``(t, note, velocity)``. The returned groups
    hold indices into that sequence, ordered by the same ``(t, note)`` sort and
    the same first-member window that ``cluster_onsets`` uses.
    """
    order = sorted(range(len(onsets)), key=lambda i: (onsets[i][0], onsets[i][1]))
    groups: list[tuple[int, ...]] = []
    run: list[int] = []
    for i in order:
        if run and onsets[i][0] - onsets[run[0]][0] > tol:
            groups.append(tuple(run))
            run = []
        run.append(i)
    if run:
        groups.append(tuple(run))
    return tuple(groups)
