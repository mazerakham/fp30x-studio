r"""Time alignment: the least-energy homeomorphism between two performances.

Given two performances of one piece, find the increasing homeomorphism
:math:`\varphi:[0,T_1]\to[0,T_2]` fixing the endpoints that minimises

.. math::

    E[\varphi] = \sum_i \bigl(\varphi(t_i)-s_i\bigr)^2
               + \lambda \int_0^{T_1}|\varphi'|^2\,dt ,

over matched onsets :math:`(t_i,s_i)`. :mod:`.warp` carries the derivation, the
Euler-Lagrange equation and the exact discretisation; :mod:`.correspond` finds
the :math:`(t_i,s_i)` and counts what it could not match; :mod:`.events` gets
from a ``.fp30`` take to onset events via the pipeline.

Usage::

    from fp30x_studio.align import load_segments, align_segments

    segs = load_segments("~/Music/FP-30X Studio/takes/2026-08-19-a.fp30")
    al = align_segments(segs[2], segs[3].window(60), lam=1.0)
    al.summary()

The three properties this module is built to keep:

**The discretisation is exact.** The Euler-Lagrange equation forces
:math:`\varphi''=0` off the knots, so the minimiser is piecewise linear on the
mesh of matched onsets. Solving over that mesh is not a numerical scheme, it is
the answer.

**Monotonicity is a constraint, not a hope.** :math:`\varphi'\ge\delta>0` is
imposed as a linear inequality and solved with an exact active-set QP whose KKT
residual is reported.

**Nothing unmatched is discarded quietly.**
:meth:`~.correspond.Correspondence.census` accounts for every onset event on
both sides, matched or not.
"""

from __future__ import annotations

from .correspond import Correspondence, MatchedPair, correspond, event_distance
from .events import (CLUSTER_TOL_S, LATTICE_S, SEGMENT_GAP_S, Event, Segment,
                     cluster_onsets, load_segments)
from .report import (DEFAULT_LAMBDAS, Alignment, align_segments,
                     l_curve_corner)
from .warp import (Kink, SweepPoint, Warp, fit_warp, gcv_lambda,
                   lambda_sweep, straight_line)

__all__ = [
    "Event", "Segment", "load_segments", "cluster_onsets",
    "LATTICE_S", "CLUSTER_TOL_S", "SEGMENT_GAP_S",
    "Correspondence", "MatchedPair", "correspond", "event_distance",
    "Warp", "Kink", "SweepPoint", "fit_warp", "lambda_sweep", "straight_line",
    "gcv_lambda", "l_curve_corner",
    "Alignment", "align_segments", "DEFAULT_LAMBDAS",
]
