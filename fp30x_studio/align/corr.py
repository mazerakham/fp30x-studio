r"""Coarse alignment by one matrix multiplication.

The question this answers is *which attempts are the same music*, and it is
asked of every pair of attempts at once.

Represent each onset event by its pitch-class indicator in :math:`\mathbb{R}^{12}`,
L2-normalised, and stack all events of all segments into one matrix
:math:`Q\in\mathbb{R}^{N\times 12}`. Then

.. math::  M = Q Q^{\mathsf T}

is the full cross-similarity matrix: :math:`M_{ij}=\cos\angle(q_i,q_j)`. One
GEMM, :math:`12N^2` flops, and every pair of segments is a block of it.

Two segments that share a passage show that passage as a **diagonal streak**
in their block, because matching music advances both indices together. Summing
:math:`M` along its diagonals,

.. math::  c(\tau) = \sum_i M_{i,\,i+\tau},

collapses each streak into a spike, and :math:`c` is exactly the
cross-correlation of the two pitch-class sequences. That is the whole coarse
stage.

**What it cannot do, and why the next stage exists.** A diagonal is a rigid
translation: it assumes the two performances advance at the same rate. Working
in *event index* rather than in seconds already removes the gross tempo
difference — the i-th chord is the i-th chord however long it was held — but it
cannot absorb an added ornament, a dropped note, or a repeat taken differently,
each of which shears the diagonal. So :math:`c` is used only to propose
candidate offsets. :mod:`.correspond` then finds the actual event pairing by
local alignment, and :mod:`.warp` fits the least-energy homeomorphism through
it. Correlation proposes; the warp disposes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .events import Segment

__all__ = ["chroma", "gram", "diagonal_sums", "Candidate", "candidates",
           "all_pairs"]


def chroma(events, *, weight_velocity: bool = False) -> np.ndarray:
    """(n, 12) L2-normalised pitch-class indicators, one row per event."""
    X = np.zeros((len(events), 12), dtype=np.float32)
    for i, e in enumerate(events):
        for pc in e.pitch_classes:
            X[i, pc] = 1.0
        if weight_velocity:
            X[i] *= (e.velocity / 127.0) ** 0.5
    n = np.linalg.norm(X, axis=1, keepdims=True)
    np.divide(X, n, out=X, where=n > 0)
    return X


def gram(segments: list[Segment]) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """The one GEMM. Returns ``M`` and the (start, stop) row span per segment."""
    blocks, spans, at = [], [], 0
    for s in segments:
        X = chroma(s.events)
        blocks.append(X)
        spans.append((at, at + len(X)))
        at += len(X)
    Q = np.vstack(blocks)
    return Q @ Q.T, spans


def diagonal_sums(B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r"""Sum an (n, m) block along every diagonal.

    Returns ``(taus, c)`` with :math:`\tau` from :math:`-(n-1)` to :math:`m-1`.
    Each :math:`c(\tau)` is divided by the number of terms in that diagonal, so
    the short diagonals at the corners — which can only ever be small — do not
    look like weak matches when they are simply short.
    """
    n, m = B.shape
    taus = np.arange(-(n - 1), m)
    tot = np.array([np.trace(B, offset=int(t)) for t in taus], dtype=np.float64)
    cnt = np.array([min(n, m, n + t, m - t) for t in taus], dtype=np.float64)
    return taus, tot / np.maximum(cnt, 1)


@dataclass(frozen=True)
class Candidate:
    """One proposed offset of segment ``a`` into segment ``b``."""

    a: int
    b: int
    tau: int                 #: event-index offset, b_index = a_index + tau
    score: float             #: mean cosine along that diagonal
    z: float                 #: (score - median) / MAD, over all offsets
    overlap: int             #: events on the diagonal

    @property
    def strong(self) -> bool:
        # 6 MADs and at least four bars' worth of events. Both thresholds are
        # conventions, not measurements, and are stated so they can be argued
        # with rather than discovered in the code.
        return self.z >= 6.0 and self.overlap >= 40


def candidates(B: np.ndarray, a: int, b: int, *, top: int = 3,
               min_overlap: int = 40) -> list[Candidate]:
    taus, c = diagonal_sums(B)
    n, m = B.shape
    cnt = np.array([min(n, m, n + t, m - t) for t in taus])
    ok = cnt >= min_overlap
    if not ok.any():
        return []
    med = float(np.median(c[ok]))
    mad = float(np.median(np.abs(c[ok] - med))) or 1e-9
    order = np.argsort(-np.where(ok, c, -np.inf))[:top]
    return [Candidate(a, b, int(taus[i]), float(c[i]),
                      float((c[i] - med) / (1.4826 * mad)), int(cnt[i]))
            for i in order]


def all_pairs(segments: list[Segment], *, top: int = 1,
              min_overlap: int = 40) -> tuple[np.ndarray, list[Candidate]]:
    """Every ordered pair ``a < b``, from the single Gram matrix."""
    M, spans = gram(segments)
    out = []
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            (i0, i1), (j0, j1) = spans[i], spans[j]
            if i1 - i0 < min_overlap or j1 - j0 < min_overlap:
                continue
            out += candidates(M[i0:i1, j0:j1], segments[i].index,
                              segments[j].index, top=top,
                              min_overlap=min_overlap)
    return M, out
