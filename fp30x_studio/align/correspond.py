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
           "timing_prior", "TAU", "GAP", "EXACT_WEIGHT", "PRIOR_WEIGHT",
           "PRIOR_HORIZON"]

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


_DIAG, _DEL, _INS, _STOP = 0, 1, 2, 3


def _local_align(score: np.ndarray, gap: float):
    """Smith-Waterman. ``score`` is ``-inf`` where a pairing is inadmissible."""
    n, m = score.shape
    H = np.zeros((n + 1, m + 1))
    back = np.full((n + 1, m + 1), _STOP, dtype=np.int8)
    for i in range(1, n + 1):
        Hi, Hp, si = H[i], H[i - 1], score[i - 1]
        for j in range(1, m + 1):
            d = Hp[j - 1] + si[j - 1]
            u = Hp[j] - gap
            l = Hi[j - 1] - gap
            best, arg = d, _DIAG
            if u > best:
                best, arg = u, _DEL
            if l > best:
                best, arg = l, _INS
            if best <= 0.0:
                Hi[j], back[i, j] = 0.0, _STOP
            else:
                Hi[j], back[i, j] = best, arg
    i, j = np.unravel_index(int(np.argmax(H)), H.shape)
    best = float(H[i, j])
    steps = []
    while back[i, j] != _STOP:
        k = back[i, j]
        if k == _DIAG:
            steps.append(("=", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif k == _DEL:
            steps.append(("d", i - 1, j - 1))
            i -= 1
        else:
            steps.append(("i", i - 1, j - 1))
            j -= 1
    steps.reverse()
    return steps, best


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


def correspond(a, b, *, gap: float = GAP, tau: float = TAU,
               prior: np.ndarray | None = None,
               prior_weight: float = PRIOR_WEIGHT) -> Correspondence:
    """Match the onset events of ``a`` against those of ``b``.

    Raises if no admissible pairing survives -- two sequences that share no
    music get an exception, not a warp fitted to noise.
    """
    a, b = tuple(a), tuple(b)
    if not a or not b:
        raise ValueError("both event sequences must be non-empty")
    if not 0.0 < tau <= 1.0 or gap <= 0.0:
        raise ValueError("need 0 < tau <= 1 and gap > 0")

    cost = np.array([[event_distance(x, y) for y in b] for x in a])
    score = np.where(cost <= tau, 1.0 - cost / tau, -np.inf)
    if prior is not None:
        if prior.shape != cost.shape:
            raise ValueError("prior must match the (len(a), len(b)) grid")
        score = score - prior_weight * prior
    steps, best = _local_align(score, gap)

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
