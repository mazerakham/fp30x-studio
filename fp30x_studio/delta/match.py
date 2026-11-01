r"""The categorical half of the operation set: which played note is which note
on the page.

The delta splits in two. This module produces the first part, an injective
partial map

.. math::

   \mu : \{0,\dots,N-1\} \supseteq D \;\longrightarrow\; \{0,\dots,M-1\},

from score notes to performed notes, together with the two complements: score
notes with no image (deletions, notes on the page that were not played) and
performed notes outside the image (insertions, notes played that are not on the
page). Nothing else in the module is real valued; :mod:`.fit` supplies that.

Two stages, and the second exists because the first is coarse.

**Stage one is the existing aligner.** :func:`fp30x_studio.align.correspond`
runs a Smith-Waterman local alignment with a pitch set substitution score over
*onset events*, which are chords. It is the right tool for the global question,
because it is local at both ends and so survives a false start, a skipped
repeat or a take that begins in the middle, and because it already counts
everything it fails to match. What it does not do is look inside a chord: it
answers at the resolution of the event, so its matched pairs are chord to
chord. A delta indexed by score note needs finer, and a chord that the player
rolled across two of our clustering windows is matched as a whole or not at
all.

**Stage two is a note level Needleman-Wunsch banded on stage one.** Sort both
note tables by ``(onset, pitch)``. The event correspondence gives, for each
matched score event, the performed event it became, hence an increasing partial
map on note indices; interpolating it linearly and extrapolating at slope one
gives an anchor :math:`j_0(i)` for every score note. The DP is then run only on
cells with :math:`|j - j_0(i)| \le W`, which is what makes a global alignment
over a few thousand notes cost :math:`O(NW)` rather than :math:`O(NM)`.

The substitution rule is a criterion rather than a parameter: **a performed
note may be matched to a score note only if it is the same pitch.** Enharmonics
are the same key on a piano and octave errors are wrong notes, so equality of
MIDI number is the whole test. What remains is which of several equal pitches
goes with which, and that is settled by the anchor, at a weight small enough
that it can never create a pairing and only ever choose between pairings.

So the band is where the alignment is allowed to look and the anchor is the
tie break inside it. Widen ``band`` if a take contains a structural jump the
event alignment did not catch; the run reports how many matches landed against
the band edge, which is the diagnostic for that.

**Stage three undoes the monotonicity assumption where it is false.** A
Needleman-Wunsch matching is monotone in onset order, because that is what a
dynamic program over two sequences can express. Piano playing is not. A pianist
delays the melody note behind the bass under it, and when the delay is longer
than the gap to the next note of the accompaniment the two voices swap places in
onset order without either note being wrong. Op. 72 No. 1 does this constantly,
and the monotone stage reports both notes as errors: the score note as missed,
the played note as inserted, at the same bar and the same pitch.

Monotonicity is a device of the dynamic program, not a property the answer has
to have. The matching only has to be injective. So after the DP, unmatched score
notes and unmatched played notes of the **same pitch**, lying within one beat of
each other once the matched pairs are used to say where a beat is, are paired,
nearest first. The count of pairs made this way is reported separately, since it
is exactly the count of places where the two voices crossed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..align.correspond import correspond

__all__ = ["Matching", "match_notes", "BAND", "GAP_NOTE", "ANCHOR_WEIGHT"]

#: Half width of the DP band, in notes. Twenty four is about two bars of the
#: densest texture in these nocturnes, so an ornament realised as a dozen extra
#: notes stays inside it while the cost stays linear.
BAND = 24

#: What one unmatched note costs, against a matched pair worth 1. Below one
#: half, so two notes of the same pitch always prefer to be matched to each
#: other rather than both left unmatched.
GAP_NOTE = 0.4

#: Weight on the anchor tie break. Small: a pairing at the wrong place still
#: scores above no pairing at all, so this chooses among pairings and never
#: creates or destroys one.
ANCHOR_WEIGHT = 0.15

#: How far apart, in beats, a leftover score note and a leftover played note of
#: the same pitch may be and still be called the same note. One beat: wide
#: enough for a melody note delayed behind its bass, narrow enough that a
#: repeated pitch in the next bar is out of reach.
REPAIR_BEATS = 1.0


@dataclass
class Matching:
    """The partial map, its two complements, and how it was arrived at."""

    pairs: list[tuple[int, int]]        #: (score note index, performed index)
    deletions: list[int]                #: score notes never played
    insertions: list[int]               #: performed notes not on the page
    n_score: int
    n_perf: int
    event_census: dict = field(default_factory=dict)
    at_band_edge: int = 0
    anchored: bool = True
    note: str = ""
    crossings: int = 0

    def __post_init__(self):
        seen_i = {i for i, _ in self.pairs}
        seen_j = {j for _, j in self.pairs}
        if len(seen_i) != len(self.pairs) or len(seen_j) != len(self.pairs):
            raise ValueError("the matching is not injective")
        if len(seen_i) + len(self.deletions) != self.n_score:
            raise ValueError("score notes are not accounted for exactly once")
        if len(seen_j) + len(self.insertions) != self.n_perf:
            raise ValueError("performed notes are not accounted for exactly once")

    @property
    def counts(self) -> dict:
        return {
            "score_notes": self.n_score,
            "performed_notes": self.n_perf,
            "matched": len(self.pairs),
            "deletions": len(self.deletions),
            "insertions": len(self.insertions),
            "match_rate_score": len(self.pairs) / max(self.n_score, 1),
            "match_rate_performed": len(self.pairs) / max(self.n_perf, 1),
            "at_band_edge": self.at_band_edge,
            "voice_crossings_repaired": self.crossings,
        }


def _anchor(score, perf, band: int) -> tuple[np.ndarray, dict, bool, str]:
    """Increasing map from score note index to performed note index.

    Built from the event level correspondence, extrapolated at slope one
    outside the span it covers. Falls back to the straight diagonal if the
    event aligner refuses the pair, which happens when the two really are not
    the same music; the caller still gets an answer and the reason is carried.
    """
    n, m = len(score.notes), len(perf.notes)
    try:
        corr = correspond(score.events, perf.events)
    except ValueError as exc:
        diag = np.linspace(0.0, max(m - 1, 0), max(n, 1))
        return diag, {}, False, f"event alignment declined ({exc}); diagonal anchor"

    xs = [score.event_notes[p.ia][0] for p in corr.pairs]
    ys = [perf.event_notes[p.ib][0] for p in corr.pairs]
    grid = np.arange(n, dtype=float)
    anchor = np.interp(grid, xs, ys)
    # numpy.interp clamps outside the data; a head or tail the local alignment
    # declined should keep moving, so extend at slope one instead of flat.
    lo, hi = xs[0], xs[-1]
    anchor[:lo] = ys[0] - (lo - grid[:lo])
    anchor[hi + 1:] = ys[-1] + (grid[hi + 1:] - hi)
    return anchor, corr.census(), True, ""


def match_notes(score, perf, *, band: int = BAND, gap: float = GAP_NOTE,
                anchor_weight: float = ANCHOR_WEIGHT) -> Matching:
    """Match the notes of ``score`` (a :class:`~.score.ScoreDoc`) to those of
    ``perf`` (a :class:`~.perform.PerfDoc`)."""
    sp = np.array([x.pitch for x in score.notes], dtype=np.int16)
    pp = np.array([x.pitch for x in perf.notes], dtype=np.int16)
    n, m = len(sp), len(pp)
    if n == 0 or m == 0:
        raise ValueError("both note tables must be non-empty")

    anchor, census, anchored, note = _anchor(score, perf, band)

    # Banded Needleman-Wunsch. Row i keeps cells j in [lo_i, hi_i]. Two
    # constraints make the recurrence total, so that no cell in the band is
    # unreachable and the traceback cannot walk out of it:
    #
    #   lo is non decreasing, and lo_i <= hi_{i-1}, so the vertical step into
    #   the leftmost cell of a row always exists;
    #   the last row runs to m, so a performance whose tail runs past the end
    #   of the score is charged as insertions rather than refused.
    los, his = [], []
    prev_lo, prev_hi = 0, 0
    for i in range(n + 1):
        a = float(anchor[min(i, n - 1)])
        if i == 0:
            lo, hi = 0, max(int(np.ceil(a)) + band, 0)
        else:
            lo = max(int(np.floor(a)) - band, prev_lo)
            lo = min(lo, prev_hi)
            hi = int(np.ceil(a)) + band
        lo = max(0, min(lo, m))
        hi = m if i == n else max(min(hi, m), lo)
        los.append(lo)
        his.append(hi)
        prev_lo, prev_hi = lo, hi

    NEG = -1e18
    rows: list[np.ndarray] = []
    backs: list[np.ndarray] = []
    # 0 = diagonal (match), 1 = up (score note unmatched, a deletion), 2 = left
    # (performed note unmatched, an insertion), 3 = origin.
    prev = -gap * (los[0] + np.arange(his[0] - los[0] + 1, dtype=float))
    pb = np.full(len(prev), 2, dtype=np.int8)
    pb[0] = 3
    rows.append(prev)
    backs.append(pb)
    plo = los[0]

    for i in range(1, n + 1):
        lo, hi = los[i], his[i]
        cur = np.full(hi - lo + 1, NEG)
        cb = np.full(hi - lo + 1, 2, dtype=np.int8)
        pitch = int(sp[i - 1])
        a_i = float(anchor[i - 1])

        for j in range(lo, hi + 1):
            k = j - lo
            best, bk = NEG, 2
            kp = j - plo
            if j > 0 and pitch == pp[j - 1]:
                kd = kp - 1
                if 0 <= kd < len(prev):
                    pen = anchor_weight * min(1.0, abs((j - 1) - a_i) / band)
                    v = prev[kd] + 1.0 - pen
                    if v > best:
                        best, bk = v, 0
            if 0 <= kp < len(prev) and prev[kp] - gap > best:
                best, bk = prev[kp] - gap, 1
            if k > 0 and cur[k - 1] - gap > best:
                best, bk = cur[k - 1] - gap, 2
            cur[k], cb[k] = best, bk
        rows.append(cur)
        backs.append(cb)
        prev, plo = cur, lo

    # Traceback from (n, m).
    pairs: list[tuple[int, int]] = []
    edge = 0
    i, j = n, m
    if not (los[n] <= m <= his[n]):
        raise ValueError("the band does not reach the end of the performance; "
                         "widen band")
    while i > 0 or j > 0:
        k = j - los[i]
        b = backs[i][k]
        if b == 3:
            break
        if b == 0:
            pairs.append((i - 1, j - 1))
            if abs((j - 1) - anchor[i - 1]) >= band - 1:
                edge += 1
            i, j = i - 1, j - 1
        elif b == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()

    pairs, crossed = _repair_crossings(score, perf, pairs)

    seen_i = {i for i, _ in pairs}
    seen_j = {j for _, j in pairs}
    return Matching(
        pairs=pairs,
        deletions=[i for i in range(n) if i not in seen_i],
        insertions=[j for j in range(m) if j not in seen_j],
        n_score=n, n_perf=m, event_census=census, at_band_edge=edge,
        anchored=anchored, note=note, crossings=crossed,
    )


def _repair_crossings(score, perf, pairs, *, window_beats: float = REPAIR_BEATS
                      ) -> tuple[list[tuple[int, int]], int]:
    """Pair leftovers of equal pitch that the monotone stage had to refuse."""
    if len(pairs) < 2:
        return pairs, 0
    bs = np.array([score.notes[i].beat for i, _ in pairs], float)
    ts = np.array([perf.notes[j].t_on for _, j in pairs], float)
    span_b, span_t = bs[-1] - bs[0], ts[-1] - ts[0]
    if span_b <= 0 or span_t <= 0:
        return pairs, 0
    window = window_beats * span_t / span_b

    free_i = sorted(set(range(len(score.notes))) - {i for i, _ in pairs})
    free_j = sorted(set(range(len(perf.notes))) - {j for _, j in pairs})
    if not free_i or not free_j:
        return pairs, 0

    by_pitch: dict[int, list[int]] = {}
    for j in free_j:
        by_pitch.setdefault(perf.notes[j].pitch, []).append(j)

    want = np.interp(np.array([score.notes[i].beat for i in free_i], float), bs, ts)
    cands = []
    for k, i in enumerate(free_i):
        for j in by_pitch.get(score.notes[i].pitch, ()):
            gap = abs(perf.notes[j].t_on - want[k])
            if gap <= window:
                cands.append((gap, i, j))
    cands.sort()

    used_i: set[int] = set()
    used_j: set[int] = set()
    added = []
    for _, i, j in cands:
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        added.append((i, j))
    if not added:
        return pairs, 0
    return sorted(pairs + added), len(added)
