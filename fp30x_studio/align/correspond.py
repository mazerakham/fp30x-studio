"""Correspondence: which onset in one performance is which onset in the other.

The warp cannot be fitted before this question is answered, and the answer is
never total. Two performances of the same piece differ by dropped notes, added
ornaments, a repeat taken differently, and -- on a false start -- by one of them
giving up and going back to the beginning. **Every event this layer cannot
match is counted and reported**, on both sides. An alignment that quietly
discards a third of the notes and then reports a small residual is worse than
no alignment, because it looks like a result.

Method
------
A **local** alignment of onset events -- Smith-Waterman with a pitch-set
substitution score. Three steps: pair them (diagonal), leave an event of A
unpaired (deletion), leave one of B unpaired (insertion). The alignment is
local at both ends, so it finds the stretch of music the two performances
actually share and stops there, rather than being forced to consume one of them
whole.

Why not classical DTW. DTW's one-to-many steps exist to stretch a *continuous*
signal; applied to note events they let a single onset absorb an arbitrary run
of its neighbour's, which is exactly the silent-discard failure this layer
exists to prevent. Here every event is paired at most once, and everything
unpaired is an insertion or a deletion that gets counted.

Why not a global or free-end alignment. Tried, and it is degenerate on this
data: when the tail of A has no counterpart, terminating early and charging the
whole tail to deletions can cost the same as continuing, so the alignment quits
in the middle of a passage that matches perfectly. Local alignment removes the
choice -- a stretch is in the answer only if pairing it scores better than
stopping.

The scoring
-----------
One criterion, used once. A pairing is admissible only if it shares at least
half its pitch content (:data:`TAU`); it then scores :math:`1-\\text{cost}/\\tau`,
so a note-for-note identity scores 1 and a bare-minimum pairing scores 0.
Unpaired events cost :data:`GAP` each. There is no second threshold applied
afterwards to clean the result up: what the alignment returns is what is
reported.

Gaps are affine (Gotoh 1982)
----------------------------
A linear gap cost charges :math:`k\\,\\text{gap}` for a run of :math:`k`
unpaired events, so one long cut costs exactly what many short ones cost. That
is the wrong model of a performance: a player who skips a whole passage once
has done something *simpler* than one who drops twelve notes scattered through
it, and the alignment should say so. Gotoh's affine cost

.. math::  g(k) \\;=\\; \\alpha + \\beta k

separates the two -- :math:`\\alpha` to open a cut, :math:`\\beta` to extend it,
:math:`\\beta \\ll \\alpha` -- at the price of the standard three-matrix
recurrence (match / gap-in-A / gap-in-B) in place of one matrix.

It is parametrised here by ``gap`` and ``gap_extend`` rather than by
:math:`\\alpha,\\beta` directly, so that the two are the same kind of quantity:
a run of :math:`k` costs :math:`\\text{gap} + (k-1)\\,\\text{gap\\_extend}` --
the first unpaired event costs what it always did, and each further one costs
``gap_extend``. Hence :math:`\\alpha = \\text{gap}-\\text{gap\\_extend}` and
:math:`\\beta = \\text{gap\\_extend}`, and **``gap_extend == gap`` is exactly
the old linear cost**. That is the default, so no existing caller changes
meaning; affine is asked for, not inherited.

The local (Smith-Waterman) boundary conditions are untouched: a cell may still
restart at zero, the alignment still ends where continuing stops paying, and
:meth:`Correspondence.census` still accounts for every event on both sides.

Charging a cut for the mass it destroys
---------------------------------------
``gap`` prices a cut by *how many events* it skips, which makes snipping either
end of a rest cost the same as cutting through a held chord. The fix needs no silence threshold:
charge a cut for what it removes,

.. math::  c\\bigl(\\text{cut over }[s,t]\\bigr) = \\int_s^t \\lVert f(u)\\rVert\\,du ,

where :math:`f:\\mathbb{R}\\to\\mathbb{R}^{88}` is the sounding-state function
that :mod:`fp30x_studio.performance` already builds -- ``f(t)`` is the vector
of momenta being sustained at time ``t``. On silence :math:`f\\equiv 0`, so a
cut at the edge of a rest is **free**, by the same formula that makes cutting
mid-chord expensive. Nothing is thresholded and nothing is special-cased.

:func:`mass_weights` turns that integral into one weight per event, and
``correspond(weight_a=..., weight_b=...)`` uses it as the per-event extend
cost. It is off by default: without it every event weighs 1 and the cost is
the count, as before.

The timing prior
----------------
Pitch content alone cannot break a tie, and in this music ties are common: a
repeated note, a trill, an ornament that states the same pitch twice a second
apart. Both readings score identically, the aligner picks one, and a wrong pick
shows up downstream as a violent kink in :math:`\\varphi` that is an artefact of
the tie, not a fact about the playing. Observed on the Op. 55 pair: two F4s a
second apart in one performance, and the arbitrary choice produced the single
largest corner in the fit.

So ``correspond`` accepts an optional ``prior`` -- a penalty in :math:`[0,1]`
per candidate pair -- weighted at :data:`PRIOR_WEIGHT`, small enough that pitch
evidence always outranks it and large enough to settle a tie. The caller
supplies it from a first-pass :math:`\\varphi`, which makes the whole thing a
two-step alternation, not a circular argument: pass one uses pitch only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .events import Event

__all__ = ["Correspondence", "MatchedPair", "event_distance", "correspond",
           "mass_weights", "timing_prior", "TAU", "GAP", "EXACT_WEIGHT",
           "PRIOR_WEIGHT", "PRIOR_HORIZON"]

#: Weight on exact-pitch agreement; the remainder goes to pitch-class agreement,
#: which keeps an octave-displaced bass from scoring as a total mismatch.
EXACT_WEIGHT = 0.75

#: A pairing is admissible at or below this cost -- **at least half the pitch
#: content in common**. A criterion, not a tuned parameter.
TAU = 0.50

#: Cost of leaving one event unpaired. At 0.25, one note-for-note pairing pays
#: for bridging four unpaired events: an added ornament or a dropped figure is
#: a handful of notes, and the alignment should step over it; a whole phrase
#: taken differently is not, and it should not.
GAP = 0.25

#: Weight on the optional timing prior: half a pairing. A note-for-note pairing
#: at the wrong time (score 0.5) still outranks a bare-minimum pairing at the
#: right time (score 0.0), so the prior settles ties and never overrules pitch.
PRIOR_WEIGHT = 0.5

#: Timing-prior horizon, seconds: a candidate this far from where the first-pass
#: warp puts it takes the full penalty. One second is a bar of this nocturne.
PRIOR_HORIZON = 2.0


def event_distance(a: Event, b: Event) -> float:
    """Cost of calling ``a`` and ``b`` the same event. In :math:`[0,1]`."""
    exact = len(a.notes & b.notes) / len(a.notes | b.notes)
    pa, pb = a.pitch_classes, b.pitch_classes
    pc = len(pa & pb) / len(pa | pb)
    return 1.0 - EXACT_WEIGHT * exact - (1.0 - EXACT_WEIGHT) * pc


@dataclass(frozen=True)
class MatchedPair:
    ia: int
    ib: int
    ta: float
    tb: float
    cost: float


@dataclass
class Correspondence:
    """The matched onsets, and an honest census of everything else."""

    pairs: list[MatchedPair]
    a: tuple[Event, ...]
    b: tuple[Event, ...]
    score: float
    #: events skipped inside the aligned span: real drops and additions
    dropped_a: list[int] = field(default_factory=list)
    added_b: list[int] = field(default_factory=list)

    # -- the arrays the fit consumes --------------------------------------

    @property
    def t(self) -> np.ndarray:
        return np.array([p.ta for p in self.pairs])

    @property
    def s(self) -> np.ndarray:
        return np.array([p.tb for p in self.pairs])

    @property
    def span_a(self) -> tuple[int, int]:
        return self.pairs[0].ia, self.pairs[-1].ia

    @property
    def span_b(self) -> tuple[int, int]:
        return self.pairs[0].ib, self.pairs[-1].ib

    # -- the census --------------------------------------------------------

    def census(self) -> dict:
        """Every event on both sides, accounted for exactly once."""
        a0, a1 = self.span_a
        b0, b1 = self.span_b
        core_a, core_b = self.a[a0:a1 + 1], self.b[b0:b1 + 1]
        notes = lambda seq: sum(e.n for e in seq)
        return {
            "events_a": len(self.a),
            "events_b": len(self.b),
            "notes_a": notes(self.a),
            "notes_b": notes(self.b),
            "matched": len(self.pairs),
            "matched_notes_a": sum(self.a[p.ia].n for p in self.pairs),
            "matched_notes_b": sum(self.b[p.ib].n for p in self.pairs),
            "exact_pairs": sum(1 for p in self.pairs if p.cost == 0.0),
            "mean_pair_cost": float(np.mean([p.cost for p in self.pairs])),
            "core_events_a": len(core_a),
            "core_events_b": len(core_b),
            "core_notes_a": notes(core_a),
            "core_notes_b": notes(core_b),
            "core_span_a": (self.a[a0].t, self.a[a1].t),
            "core_span_b": (self.b[b0].t, self.b[b1].t),
            "dropped_in_core_a": len(self.dropped_a),
            "added_in_core_b": len(self.added_b),
            "head_a": a0,
            "tail_a": len(self.a) - 1 - a1,
            "head_b": b0,
            "tail_b": len(self.b) - 1 - b1,
            "outside_core_a": len(self.a) - len(core_a),
            "outside_core_b": len(self.b) - len(core_b),
            "outside_core_notes_a": notes(self.a) - notes(core_a),
            "outside_core_notes_b": notes(self.b) - notes(core_b),
            "match_rate_a": len(self.pairs) / len(self.a),
            "match_rate_core_a": len(self.pairs) / max(len(core_a), 1),
            "match_rate_core_b": len(self.pairs) / max(len(core_b), 1),
        }


#: Traceback states. ``_M`` is the match matrix, ``_X`` the gap-in-B matrix
#: (an event of A left unpaired) and ``_Y`` the gap-in-A matrix; ``_STOP`` is
#: the local restart.
_STOP, _M, _X, _Y = 0, 1, 2, 3


def _local_align(score: np.ndarray, gap: float, *,
                 gap_extend: float | None = None,
                 weight_a: np.ndarray | None = None,
                 weight_b: np.ndarray | None = None):
    """Smith-Waterman with Gotoh affine gaps.

    ``score`` is ``-inf`` where a pairing is inadmissible. Three matrices, all
    local: ``M`` ends in a pairing, ``X`` in an unpaired event of A, ``Y`` in
    an unpaired event of B. Opening a gap costs ``gap`` for its first event;
    each further event costs ``gap_extend`` times its weight, so a run of
    :math:`k` unweighted events costs ``gap + (k-1)*gap_extend`` and the case
    ``gap_extend == gap`` reproduces the linear cost exactly.

    Only ``M`` is a candidate for the best cell: gap costs are positive, so an
    optimal local alignment never ends on one.

    ``M`` and ``X`` on row ``i`` depend only on row ``i-1``, so they are built
    with array operations; ``Y`` is the one genuine left-to-right recurrence.
    """
    n, m = score.shape
    beta = float(gap if gap_extend is None else gap_extend)
    alpha = float(gap) - beta
    wa = np.ones(n) if weight_a is None else np.asarray(weight_a, float)
    wb = np.ones(m) if weight_b is None else np.asarray(weight_b, float)
    if wa.shape != (n,) or wb.shape != (m,):
        raise ValueError("gap weights must be one per event, on each side")
    if np.any(wa < 0) or np.any(wb < 0):
        raise ValueError("gap weights must be non-negative")

    neg = -np.inf
    M = np.full((n + 1, m + 1), neg)
    X = np.full((n + 1, m + 1), neg)
    Y = np.full((n + 1, m + 1), neg)
    bM = np.full((n + 1, m + 1), _STOP, dtype=np.int8)
    bX = np.full((n + 1, m + 1), _M, dtype=np.int8)
    bY = np.full((n + 1, m + 1), _M, dtype=np.int8)
    cols = np.arange(m)

    for i in range(1, n + 1):
        # -- M: pair a[i-1] with b[j-1], continuing from any state or restarting
        cand = np.stack([np.zeros(m), M[i - 1, :-1], X[i - 1, :-1], Y[i - 1, :-1]])
        arg = np.argmax(cand, axis=0)
        M[i, 1:] = cand[arg, cols] + score[i - 1]
        bM[i, 1:] = arg

        # -- X: leave a[i-1] unpaired. Extend an X, or open a new cut from M
        # or from a Y -- a gap on one side abutting a gap on the other is two
        # cuts, not one, and pays to open twice. (Without that transition an
        # inadmissible pairing could not be stepped over at all.)
        cand = np.stack([M[i - 1] - alpha, X[i - 1], Y[i - 1] - alpha])
        arg = np.argmax(cand, axis=0)
        X[i] = cand[arg, np.arange(m + 1)] - beta * wa[i - 1]
        bX[i] = np.choose(arg, [_M, _X, _Y])

        # -- Y: leave b[j-1] unpaired. The one sequential recurrence.
        Mi, Xi, Yi, bYi = M[i], X[i], Y[i], bY[i]
        for j in range(1, m + 1):
            o, x, y = Mi[j - 1] - alpha, Xi[j - 1] - alpha, Yi[j - 1]
            if y >= o and y >= x:
                Yi[j], bYi[j] = y - beta * wb[j - 1], _Y
            elif o >= x:
                Yi[j], bYi[j] = o - beta * wb[j - 1], _M
            else:
                Yi[j], bYi[j] = x - beta * wb[j - 1], _X

    i, j = np.unravel_index(int(np.argmax(M)), M.shape)
    best = float(M[i, j])
    if not best > 0.0:
        return [], 0.0

    steps = []
    state = _M
    while True:
        if state == _M:
            steps.append(("=", i - 1, j - 1))
            state, i, j = bM[i, j], i - 1, j - 1
            if state == _STOP:
                break
        elif state == _X:
            steps.append(("d", i - 1, j - 1))
            state, i = bX[i, j], i - 1
        else:
            steps.append(("i", i - 1, j - 1))
            state, j = bY[i, j], j - 1
    steps.reverse()
    return steps, best


def mass_weights(perf, events, *, t_end: float | None = None,
                 normalise: bool = True) -> np.ndarray:
    r"""Per-event cut cost: the sounding mass a cut there would destroy.

    ``perf`` is a :class:`fp30x_studio.performance.Performance` and ``events``
    are onset events (or bare times) **in that performance's own clock**. The
    weight of event :math:`i` is

    .. math:: w_i \;\propto\; \int_{t_i}^{t_{i+1}} \lVert f(u)\rVert_1\,du
                            \;=\; F(t_{i+1}) - F(t_i) ,

    the window it occupies running to the next onset (the last runs to
    ``t_end``, by default the end of the performance). Because the windows
    tile, the cost of excising a *run* of consecutive events telescopes to the
    integral over the whole excised span -- which is what makes this a cut cost
    and not merely a per-event weight.

    :math:`\lVert\cdot\rVert_1` because :math:`f\ge 0` componentwise, so the
    :math:`\ell^1` norm of :math:`f(u)` is the sum of the sustained momenta and
    its integral is exactly the cumulative energy
    :meth:`~fp30x_studio.performance.Performance.cumulative` already computes
    in closed form. :math:`F` is piecewise affine with breakpoints at the note
    endpoints, so evaluating it by interpolation through
    :meth:`~fp30x_studio.performance.Performance.cumulative_curve` is exact:
    there is no quadrature here, and none is needed.

    ``normalise`` rescales to mean 1, which is what makes the result
    interchangeable with the default all-ones weighting: ``gap_extend`` keeps
    its calibration and only the *distribution* of cost across events changes.
    An event sitting over silence gets a weight near zero and is nearly free to
    cut; one sitting under a held chord gets several times the mean.

    The **mean**, deliberately, and not a robust scale. This distribution is
    sparse by construction -- a segment with much rest in it has a majority of
    weights at exactly zero -- so its median and its MAD are both legitimately
    zero, and normalising by either would divide by nothing and hand back a
    scale of :math:`10^9`. The mean is zero only when the whole window is
    silent, which is the one case with no information in it at all, and that
    case falls back to counting rather than being rescued with an epsilon.

    Pass the *sounding* performance (``perf.with_sustain()``) if pedal should
    count -- a note held by the pedal is still sounding, and cutting it still
    destroys mass.
    """
    ts, fs = perf.cumulative_curve()
    times = [float(getattr(e, "t", e)) for e in events]
    if not times:
        return np.zeros(0)
    edge = perf.t_end if t_end is None else float(t_end)
    bounds = np.array(times + [max(edge, times[-1])], dtype=float)
    raw = np.diff(np.interp(bounds, ts, fs))
    raw = np.maximum(raw, 0.0)
    if not normalise:
        return raw
    mean = float(raw.mean())
    # An entirely silent stretch has no mass to apportion; fall back to the
    # count rather than dividing by zero, which is the old behaviour and the
    # only honest answer when the integral says nothing.
    return raw / mean if mean > 0.0 else np.ones_like(raw)


def timing_prior(a, b, warp, *, t0: float, s0: float,
                 horizon: float = PRIOR_HORIZON) -> np.ndarray:
    """Penalty in :math:`[0,1]` for pairing ``a[i]`` with ``b[j]``.

    ``warp`` is a first-pass :math:`\\varphi`, and ``t0``/``s0`` the times it
    calls zero. The penalty is the clamped distance from where it says ``a[i]``
    lands. Events outside its domain are extrapolated at the boundary slope,
    which is what ``numpy.interp`` does and is the right behaviour: it means
    the prior fades to a constant rather than inventing structure.
    """
    ta = np.array([e.t for e in a]) - t0
    tb = np.array([e.t for e in b]) - s0
    return np.clip(np.abs(warp(ta)[:, None] - tb[None, :]) / horizon, 0.0, 1.0)


def correspond(a, b, *, gap: float = GAP, gap_extend: float | None = None,
               tau: float = TAU, prior: np.ndarray | None = None,
               prior_weight: float = PRIOR_WEIGHT,
               weight_a: np.ndarray | None = None,
               weight_b: np.ndarray | None = None) -> Correspondence:
    """Match the onset events of ``a`` against those of ``b``.

    ``gap`` is what the first unpaired event of a run costs; ``gap_extend`` is
    what each further one costs, and defaults to ``gap``, which is the linear
    cost this module used before affine gaps existed. Set it below ``gap`` to
    say that one long cut is cheaper than many short ones.

    ``weight_a`` / ``weight_b`` scale the extend cost per event, one weight per
    event on that side -- :func:`mass_weights` builds them from the sounding
    mass a cut would destroy, so that snipping silence is nearly free. Both
    default to all ones, i.e. cost by count.

    Raises if no admissible pairing survives -- two sequences that share no
    music get an exception, not a warp fitted to noise.
    """
    a, b = tuple(a), tuple(b)
    if not a or not b:
        raise ValueError("both event sequences must be non-empty")
    if not 0.0 < tau <= 1.0 or gap <= 0.0:
        raise ValueError("need 0 < tau <= 1 and gap > 0")
    if gap_extend is not None and not 0.0 < gap_extend <= gap:
        raise ValueError("need 0 < gap_extend <= gap: extending a cut cannot "
                         "cost more than opening one")

    cost = np.array([[event_distance(x, y) for y in b] for x in a])
    score = np.where(cost <= tau, 1.0 - cost / tau, -np.inf)
    if prior is not None:
        if prior.shape != cost.shape:
            raise ValueError("prior must match the (len(a), len(b)) grid")
        score = score - prior_weight * prior
    steps, best = _local_align(score, gap, gap_extend=gap_extend,
                               weight_a=weight_a, weight_b=weight_b)

    pairs = [MatchedPair(i, j, a[i].t, b[j].t, float(cost[i, j]))
             for kind, i, j in steps if kind == "="]
    if len(pairs) < 2:
        raise ValueError("no usable correspondence: these are not the same music")

    a0, a1 = pairs[0].ia, pairs[-1].ia
    b0, b1 = pairs[0].ib, pairs[-1].ib
    seen_a = {p.ia for p in pairs}
    seen_b = {p.ib for p in pairs}
    return Correspondence(
        pairs=pairs, a=a, b=b, score=best,
        dropped_a=[i for i in range(a0, a1 + 1) if i not in seen_a],
        added_b=[j for j in range(b0, b1 + 1) if j not in seen_b],
    )
