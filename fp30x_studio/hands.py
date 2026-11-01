r"""Which hand played this note.

The FP-30X sends one undifferentiated MIDI stream. Nothing in it says *left* or
*right*, and every downstream idea that wants hands -- rendering the two on
different instruments, scoring a take against a two-staff edition, telling a
student their left hand is rushing -- has to reconstruct the labelling.

The seed of this module is :class:`fp30x_studio.identify.features.RegisterLine`,
which keeps a running median of recently admitted pitches and rejects a skyline
note that falls more than :data:`~fp30x_studio.identify.features.REGISTER_DROP`
semitones below it. That is a one-sided filter: it answers *is this note part of
the melodic register?* and throws away everything else. Here the same running
statistic becomes a **two-way boundary** -- every note is assigned, and the
boundary itself is the object of interest.

Three strategies, so the extra machinery can be held to account:

``fixed``
    One constant split point. Middle C by default, because that is the obvious
    guess and the baseline this module exists to test. It is also the wrong
    constant: measured over 47 Joplin rags the best one is **E4, MIDI 64**, and
    the four semitones between them are worth six points of accuracy. Pass
    ``--split 64`` before concluding that a fixed split cannot work.

``median``
    A causal running boundary: the midpoint between the median of the recent
    left-hand pitches and the median of the recent right-hand pitches. This is
    the direct two-way generalisation of ``RegisterLine`` -- same statistic,
    same window, no lookahead.

``viterbi``
    The boundary as a hidden state. One boundary pitch per onset cluster, chosen
    by dynamic programming over the whole take: an emission cost that punishes a
    hand for spanning more than it can reach and for splitting a cluster in the
    middle of a tight chord, and a transition cost that punishes the boundary
    for moving faster than a hand can travel. Globally optimal, not causal.

**The modelling assumption, stated because it is the thing that fails.** All
three strategies split each onset cluster *contiguously*: within one struck
simultaneity, every left-hand note is below every right-hand note. Hands do
cross in real piano writing, and when they do no boundary of any kind can be
right. :func:`oracle_accuracy` measures that ceiling directly against a score,
so the gap between a strategy and the ceiling can be separated from the gap
between the ceiling and 100%.

Ground truth comes from Humdrum ``**kern``: a two-staff piano score has the
hands as separate spines, so :func:`notes_from_kern` returns labels that nobody
guessed. The performance side has no labels at all; :mod:`fp30x_studio.align`
territory is where those would come from.

Command line::

    python -m fp30x_studio.hands assign TAKE.fp30 --strategy viterbi --midi out.mid
    python -m fp30x_studio.hands plot  TAKE.fp30 --strategy viterbi --out plot.png
    python -m fp30x_studio.hands eval  SCORE.krn [SCORE.krn ...]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

__all__ = [
    "LEFT", "RIGHT", "Note", "Cluster",
    "CLUSTER_TOL_S", "MAX_SPAN", "REACH_WINDOW_S", "MIN_PITCH", "MAX_PITCH",
    "notes_from_take", "notes_from_kern", "notes_from_midi", "clusters_of",
    "Assignment", "fixed_split", "median_boundary", "viterbi_boundary",
    "STRATEGIES", "assign", "oracle_accuracy", "violations", "write_midi",
]

LEFT, RIGHT = 0, 1

#: Onsets no further apart than this are one struck simultaneity. Same constant,
#: and the same reason, as :data:`fp30x_studio.align.events.CLUSTER_TOL_S`: the
#: BLE link serialises a chord into a ~5 ms spray.
CLUSTER_TOL_S = 0.055

#: How far one hand can span at once, in semitones. A tenth is 16 and is played;
#: a twelfth is not, without an arpeggiation that arrives as separate clusters.
#: 15 is deliberately generous -- the penalty is quadratic past it, so the cost
#: of being generous is softness, not licence.
MAX_SPAN = 15

#: Notes struck this close together are treated as one reach for the span test:
#: a rolled chord, a grace note, the arpeggiated spread of a tenth. Wide enough
#: to gather those, narrow enough to leave a stride bass note and the chord that
#: answers it on separate beats (0.3 s at 100 bpm in 2/4) as separate reaches.
REACH_WINDOW_S = 0.12

#: The boundary lives on the keyboard, one semitone per state. A boundary of
#: ``b`` means *pitch < b is left*.
MIN_PITCH, MAX_PITCH = 21, 109


@dataclass(frozen=True, slots=True)
class Note:
    """One struck note, with an optional hand label."""

    t_on: float
    t_off: float
    pitch: int
    velocity: int = 64
    hand: int | None = None          #: assigned, or ground truth from a score

    @property
    def duration(self) -> float:
        return max(0.0, self.t_off - self.t_on)


@dataclass(frozen=True, slots=True)
class Cluster:
    """One onset simultaneity: the note indices struck together."""

    t: float
    idx: tuple[int, ...]             #: indices into the note list
    pitches: tuple[int, ...]         #: sorted, parallel to nothing -- see note
    reach: tuple[int, ...]           #: pitches struck within REACH_WINDOW_S


# ---------------------------------------------------------------- ingestion

def notes_from_take(path: str | Path, *, segment: int | None = None,
                    gap: float = 6.0) -> list[Note]:
    """Read a ``.fp30`` capture through the pipeline, never by hand.

    A CoreMIDI packet carries several MIDI messages and every hand-rolled reader
    on this machine has at some point split one wrongly, so the framing and the
    note-on/note-off pairing come from :class:`fp30x_studio.pipeline.TakeStore`
    and nothing here re-derives them.

    ``segment`` selects one attempt, counted in the order they were played and
    separated by silences longer than ``gap`` seconds.
    """
    from .pipeline import TakeStore

    store = TakeStore(Path(path).expanduser())
    store.ingest()
    rows = sorted(
        (store.seconds(r["ns_on"]), store.seconds(r["ns_off"]),
         int(r["note"]), int(r["velocity_on"]))
        for r in store.intervals()
    )
    notes = [Note(a, b, p, v) for a, b, p, v in rows
             if MIN_PITCH <= p < MAX_PITCH]
    if segment is None:
        return notes
    cuts = [0] + [i for i in range(1, len(notes))
                  if notes[i].t_on - notes[i - 1].t_on > gap] + [len(notes)]
    lo, hi = cuts[segment], cuts[segment + 1]
    return notes[lo:hi]


def notes_from_midi(path: str | Path) -> list[Note]:
    """Read a plain ``.mid`` file as one undifferentiated note stream."""
    import mido

    mid = mido.MidiFile(str(Path(path).expanduser()))
    open_: dict[int, tuple[float, int]] = {}
    out: list[Note] = []
    t = 0.0
    for msg in mid:                                   # merged, already in seconds
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            if msg.note in open_:                     # a re-strike closes the old
                t0, v = open_.pop(msg.note)
                out.append(Note(t0, t, msg.note, v))
            open_[msg.note] = (t, msg.velocity)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            if msg.note in open_:
                t0, v = open_.pop(msg.note)
                out.append(Note(t0, t, msg.note, v))
    for p, (t0, v) in open_.items():
        out.append(Note(t0, t0 + 0.5, p, v))
    out.sort(key=lambda n: (n.t_on, n.pitch))
    return [n for n in out if MIN_PITCH <= n.pitch < MAX_PITCH]


def notes_from_kern(path: str | Path, *, qpm: float = 100.0,
                    expand: bool = False) -> list[Note]:
    """A two-staff Humdrum score as a labelled note stream.

    The ``hand`` of every note is filled in from the spine it came from: the
    spine with the lower median pitch is the left hand. That is the ground
    truth this module is measured against, and it was written down by an editor
    rather than inferred by anything here.

    Timings are **notated, not played** -- score offsets in quarter notes turned
    into seconds at one constant tempo. That is a unit of measurement. It does
    make the score an easier problem than a performance (no rubato, no wrong
    notes, no pedal), which is why the score number is an upper bound on what a
    take will do.
    """
    from music21 import converter

    s = converter.parse(str(Path(path).expanduser()))
    if expand:
        try:
            s = s.expandRepeats()
        except Exception:                             # noqa: BLE001 - best effort
            pass
    parts = list(s.parts)
    if len(parts) < 2:
        raise ValueError(f"{path}: need two staves, found {len(parts)}")
    med = [statistics.median([p.midi for el in part.recurse().notes
                              for p in el.pitches] or [0])
           for part in parts]
    order = sorted(range(len(parts)), key=lambda i: med[i])
    hand_of = {order[0]: LEFT, order[-1]: RIGHT}

    spq = 60.0 / qpm
    out: list[Note] = []
    for i, part in enumerate(parts):
        if i not in hand_of:                          # a third staff: ignore it
            continue
        for el in part.recurse().notes:
            off = float(el.getOffsetInHierarchy(s))
            dur = float(el.duration.quarterLength) or 0.125
            for p in el.pitches:
                if MIN_PITCH <= p.midi < MAX_PITCH:
                    out.append(Note(off * spq, (off + dur) * spq,
                                    int(p.midi), 64, hand_of[i]))
    out.sort(key=lambda n: (n.t_on, n.pitch))
    return out


# ---------------------------------------------------------------- clustering

def clusters_of(notes: Sequence[Note], *, tol: float = CLUSTER_TOL_S,
                reach: float = REACH_WINDOW_S) -> list[Cluster]:
    """Group notes into onset simultaneities.

    The window is measured from the **first** member of a run, not the previous
    one, so a fast scale cannot chain-collapse into one event.

    ``reach`` gathers the pitches struck near this cluster in time -- not to
    assign them, but so the span test knows what the hand is already holding
    when it takes this cluster.
    """
    if not notes:
        return []
    runs: list[list[int]] = []
    for i, n in enumerate(notes):
        if runs and n.t_on - notes[runs[-1][0]].t_on <= tol:
            runs[-1].append(i)
        else:
            runs.append([i])

    out: list[Cluster] = []
    j0 = 0
    j1 = 0
    for run in runs:
        t = notes[run[0]].t_on
        while j0 < len(notes) and notes[j0].t_on < t - reach:
            j0 += 1
        while j1 < len(notes) and notes[j1].t_on <= t + reach:
            j1 += 1
        out.append(Cluster(
            t=t,
            idx=tuple(run),
            pitches=tuple(sorted(notes[i].pitch for i in run)),
            reach=tuple(sorted(notes[i].pitch for i in range(j0, j1))),
        ))
    return out


# ---------------------------------------------------------------- strategies

@dataclass
class Assignment:
    """The answer: a hand per note, and the boundary that produced it."""

    strategy: str
    hands: list[int]                              #: parallel to the note list
    boundary: list[tuple[float, float]]           #: (time, boundary pitch)
    notes: list[Note]

    def labelled(self) -> list[Note]:
        return [replace(n, hand=h) for n, h in zip(self.notes, self.hands)]

    def counts(self) -> tuple[int, int]:
        return self.hands.count(LEFT), self.hands.count(RIGHT)


def _apply(notes, clusters, bounds) -> list[int]:
    """Split each cluster at its boundary. The contiguity assumption, in code."""
    hands = [RIGHT] * len(notes)
    for c, b in zip(clusters, bounds):
        for i in c.idx:
            hands[i] = LEFT if notes[i].pitch < b else RIGHT
    return hands


def fixed_split(notes: Sequence[Note], *, split: int = 60, **_) -> Assignment:
    """One constant boundary. Middle C unless told otherwise."""
    clusters = clusters_of(notes)
    bounds = [float(split)] * len(clusters)
    return Assignment("fixed", _apply(notes, clusters, bounds),
                      [(c.t, b) for c, b in zip(clusters, bounds)], list(notes))


def median_boundary(notes: Sequence[Note], *, window: int = 8,
                    seed: int = 60, **_) -> Assignment:
    """``RegisterLine``'s running median, made two-sided and causal.

    Two windows of recently assigned pitches, one per hand. The boundary is the
    midpoint of their medians, so it tracks the texture up and down at melodic
    speed and cannot jump an octave in one event -- exactly the property that
    made the one-sided version work. When a hand has been silent the window
    empties of information and the boundary falls back to the other hand's
    median offset by half a maximum span, which is the best guess available
    without lookahead.
    """
    clusters = clusters_of(notes)
    lo: deque[int] = deque(maxlen=window)
    hi: deque[int] = deque(maxlen=window)
    bounds: list[float] = []
    for c in clusters:
        if lo and hi:
            b = (statistics.median(lo) + statistics.median(hi)) / 2.0
        elif hi:
            b = statistics.median(hi) - MAX_SPAN / 2.0
        elif lo:
            b = statistics.median(lo) + MAX_SPAN / 2.0
        else:
            b = float(seed)
        b = min(max(b, MIN_PITCH), MAX_PITCH)
        bounds.append(b)
        for p in c.pitches:
            (lo if p < b else hi).append(p)
    return Assignment("median", _apply(notes, clusters, bounds),
                      [(c.t, b) for c, b in zip(clusters, bounds)], list(notes))


#: Half-width of the window the boundary is asked to look at when deciding
#: where the two registers are, in seconds. About two bars of a rag.
LOCAL_WINDOW_S = 0.6

#: How far apart the two register centroids are expected to be, in semitones.
#: The guard against splitting a single register down the middle when only one
#: hand is playing.
MIN_SEPARATION = 14.0


def viterbi_boundary(notes: Sequence[Note], *,
                     w_span: float = 0.6, w_gap: float = 1.2,
                     w_km: float = 0.35, w_sep: float = 0.2,
                     w_move: float = 3.0, free_move: float = 0.0,
                     move_rate: float = 0.0, max_span: int = MAX_SPAN,
                     local: float = LOCAL_WINDOW_S,
                     min_sep: float = MIN_SEPARATION,
                     **_) -> Assignment:
    r"""The boundary as a hidden state, solved by dynamic programming.

    State at cluster :math:`t` is the boundary pitch :math:`b_t \in [21, 109)`.
    The path minimises

    .. math::

        \sum_t E(b_t, C_t) + \sum_t M(b_t - b_{t-1}, \Delta t_t)

    **Emission** :math:`E` charges a cluster for the split it is being given:

    * *Registers.* Over a window of :data:`LOCAL_WINDOW_S` either side, the
      boundary is scored as a one-dimensional two-means split of the pitches
      sounding there: the within-cluster sum of squares, normalised by the note
      count. This is the term that finds the valley between the two hands'
      registers and follows it when the music moves. On its own it is wrong in
      exactly one situation, which is why the next term exists.
    * *Separation.* Two-means will happily cut a single register in half. The
      split is therefore charged whenever the two centroids come closer than
      ``min_sep`` semitones, which is what happens when one hand is resting. The
      emission then goes flat and the transition term carries the boundary
      through the rest, rather than a spurious split being invented.
    * *Reach.* Each hand is charged :math:`(\text{span} - \text{max\_span})^2`
      for the pitches it would be holding inside :data:`REACH_WINDOW_S`. A
      boundary below the keyboard puts a stride bass and a melody two octaves
      up into the same hand, and that hand does not exist.
    * *Gap.* A boundary dropped between two notes a whole tone apart is charged
      for it, and one dropped into a wide gap is not.

    **Transition** :math:`M` charges the boundary ``w_move`` per semitone it
    moves. ``free_move`` and ``move_rate`` would grant an allowance -- a fixed
    number of semitones, plus a rate per second of elapsed time -- before the
    charge begins, on the argument that a hand travels further when it has
    longer to do it. Measured on the 47-rag corpus, that allowance is a net
    loss: any free movement at all costs about a point of accuracy, because the
    boundary spends it on noise. Both default to zero and the plain L1 charge
    stands alone.

    ``w_move`` is the interesting knob, and the one that answers the question
    this module exists to ask. At ``w_move`` large the boundary freezes into the
    best constant the emission costs can find, and the answer is a fixed split.
    At ``w_move`` zero it follows the emission wherever it goes. Accuracy on the
    held-out rags is unimodal in it: 93.5% frozen, 95.8% at ``w_move = 3``,
    94.7% free. The boundary should move, and not much -- about three semitones
    of standard deviation within a piece.

    Globally optimal over the whole take, and therefore not causal: it is a
    rendering and analysis tool, not something the live identifier could run.
    """
    import numpy as np

    clusters = clusters_of(notes)
    if not clusters:
        return Assignment("viterbi", [], [], list(notes))

    B = np.arange(MIN_PITCH, MAX_PITCH, dtype=np.int32)       # boundary states
    nB = len(B)

    # Transition cost matrix, less the time-dependent free allowance.
    delta = np.abs(B[:, None] - B[None, :]).astype(np.float64)

    onsets = np.asarray([n.t_on for n in notes], dtype=np.float64)
    pitches = np.asarray([n.pitch for n in notes], dtype=np.float64)

    emit = np.empty((len(clusters), nB), dtype=np.float64)
    j0 = j1 = 0
    for k, c in enumerate(clusters):
        reach = np.asarray(c.reach, dtype=np.int32)
        below = reach[None, :] < B[:, None]                   # (nB, nreach)
        cost = np.zeros(nB)
        for mask in (below, ~below):
            n = mask.sum(1)
            big = np.where(mask, reach[None, :], -1).max(1)
            small = np.where(mask, reach[None, :], 999).min(1)
            span = np.where(n > 0, big - small, 0)
            excess = np.maximum(0, span - max_span)
            cost += w_span * excess.astype(np.float64) ** 2
        # Gap: how much room the boundary was given inside this cluster.
        p = np.asarray(c.pitches, dtype=np.int32)
        lo = np.where(p[None, :] < B[:, None], p[None, :], -99).max(1)
        hi = np.where(p[None, :] >= B[:, None], p[None, :], 999).min(1)
        both = (lo > -99) & (hi < 999)
        gap = np.where(both, hi - lo, 99)
        cost += w_gap * np.maximum(0, 4 - gap)

        # Two-means over the local window, every boundary at once, by prefix sum.
        while j0 < len(notes) and onsets[j0] < c.t - local:
            j0 += 1
        while j1 < len(notes) and onsets[j1] <= c.t + local:
            j1 += 1
        loc = np.sort(pitches[j0:j1])
        m = len(loc)
        if m >= 2:
            cs = np.concatenate(([0.0], np.cumsum(loc)))
            cs2 = np.concatenate(([0.0], np.cumsum(loc * loc)))
            kk = np.searchsorted(loc, B.astype(np.float64))   # notes strictly below b
            nl = kk.astype(np.float64)
            nr = float(m) - nl
            sl, sr = cs[kk], cs[m] - cs[kk]
            ql, qr = cs2[kk], cs2[m] - cs2[kk]
            with np.errstate(invalid="ignore", divide="ignore"):
                sse_l = np.where(nl > 0, ql - sl * sl / np.maximum(nl, 1), 0.0)
                sse_r = np.where(nr > 0, qr - sr * sr / np.maximum(nr, 1), 0.0)
                mu_l = np.where(nl > 0, sl / np.maximum(nl, 1), 0.0)
                mu_r = np.where(nr > 0, sr / np.maximum(nr, 1), 0.0)
            cost += w_km * (sse_l + sse_r) / m
            sep = np.where((nl > 0) & (nr > 0), mu_r - mu_l, 0.0)
            cost += w_sep * np.maximum(0.0, min_sep - sep)
        emit[k] = cost

    dp = emit[0].copy()
    back = np.zeros((len(clusters), nB), dtype=np.int32)
    for k in range(1, len(clusters)):
        dt = clusters[k].t - clusters[k - 1].t
        allow = free_move + move_rate * dt
        trans = w_move * np.maximum(0.0, delta - allow)
        tot = dp[None, :] + trans                              # to-state, from-state
        back[k] = np.argmin(tot, axis=1)
        dp = tot.min(axis=1) + emit[k]

    path = np.zeros(len(clusters), dtype=np.int32)
    path[-1] = int(np.argmin(dp))
    for k in range(len(clusters) - 1, 0, -1):
        path[k - 1] = back[k, path[k]]
    bounds = [float(B[i]) for i in path]
    return Assignment("viterbi", _apply(notes, clusters, bounds),
                      [(c.t, b) for c, b in zip(clusters, bounds)], list(notes))


STRATEGIES = {
    "fixed": fixed_split,
    "median": median_boundary,
    "viterbi": viterbi_boundary,
}


def assign(notes: Sequence[Note], strategy: str = "viterbi", **kw) -> Assignment:
    try:
        fn = STRATEGIES[strategy]
    except KeyError:
        raise SystemExit(f"unknown strategy {strategy!r}; "
                         f"pick one of {', '.join(STRATEGIES)}") from None
    return fn(notes, **kw)


# ---------------------------------------------------------------- evaluation

def accuracy(hands: Sequence[int], truth: Sequence[int | None]) -> tuple[int, int]:
    """``(correct, scored)`` over the notes that carry a label."""
    ok = n = 0
    for h, t in zip(hands, truth):
        if t is None:
            continue
        n += 1
        ok += int(h == t)
    return ok, n


def oracle_accuracy(notes: Sequence[Note], *, tol: float = CLUSTER_TOL_S
                    ) -> tuple[int, int]:
    """The ceiling the contiguity assumption puts on every strategy here.

    For each cluster, the best split point *knowing the true labels*. Whatever a
    strategy scores, the distance to this number is the part a better boundary
    could still recover; the distance from this number to 100% is hand crossing,
    and no boundary can recover any of it.
    """
    ok = n = 0
    for c in clusters_of(notes, tol=tol):
        labels = [(notes[i].pitch, notes[i].hand) for i in c.idx
                  if notes[i].hand is not None]
        if not labels:
            continue
        n += len(labels)
        labels.sort()
        best = 0
        for k in range(len(labels) + 1):             # first k are LEFT
            got = sum(1 for j, (_, h) in enumerate(labels)
                      if h == (LEFT if j < k else RIGHT))
            best = max(best, got)
        ok += best
    return ok, n


def violations(assignment: Assignment, *, max_span: int = MAX_SPAN,
               reach: float = REACH_WINDOW_S) -> dict:
    """Constraint violations, for material with no ground truth at all.

    Two proxies. ``span`` counts the moments a hand is asked to hold more than
    it can reach; ``crossing`` counts the clusters where the assignment puts a
    left-hand note above a right-hand one. Neither proves an assignment right --
    the all-one-hand answer has zero crossings -- so they are only ever read
    next to each other and next to the note counts.
    """
    notes, hands = assignment.notes, assignment.hands
    clusters = clusters_of(notes, reach=reach)
    span_bad = 0
    cross = 0
    worst = 0
    for c in clusters:
        lo_hi: dict[int, list[int]] = {LEFT: [], RIGHT: []}
        for i in c.idx:
            lo_hi[hands[i]].append(notes[i].pitch)
        if lo_hi[LEFT] and lo_hi[RIGHT] and max(lo_hi[LEFT]) > min(lo_hi[RIGHT]):
            cross += 1
        # span measured over the reach window, using each note's own label
        window = [(notes[i].pitch, hands[i]) for i in range(len(notes))
                  if abs(notes[i].t_on - c.t) <= reach]
        for h in (LEFT, RIGHT):
            ps = [p for p, hh in window if hh == h]
            if ps:
                s = max(ps) - min(ps)
                worst = max(worst, s)
                if s > max_span:
                    span_bad += 1
    dur = (notes[-1].t_on - notes[0].t_on) if len(notes) > 1 else 1.0
    return {
        "clusters": len(clusters),
        "span_violations": span_bad,
        "span_violations_per_min": round(span_bad / (dur / 60.0), 2),
        "widest_reach": worst,
        "crossings": cross,
        "crossings_per_min": round(cross / (dur / 60.0), 2),
        "left_notes": assignment.counts()[0],
        "right_notes": assignment.counts()[1],
    }


# ---------------------------------------------------------------- output

def pedal_from_take(path: str | Path) -> list[tuple[float, int]]:
    """Every sustain-pedal (CC64) change in a take, as ``(seconds, value)``.

    The FP-30X sends a **continuous** pedal: one take carries all 128 values,
    not two. Most synths, fluidsynth included, still threshold it at 64, so
    half-pedalling is flattened on playback. The data is here regardless, and
    dropping it is a different and much larger error: a render without CC64
    damps every note the instant the key is released, so a pedalled legato
    passage renders detached.

    Times come from :class:`~fp30x_studio.pipeline.TakeStore` and NOT from
    ``ns / 1e9``, so that they share an origin with :func:`notes_from_take`.
    A first version of this divided the raw nanosecond stamps and put the pedal
    eight days away from the notes; the conservation check in
    :mod:`fp30x_studio.qa_conserve` caught it as a 150,000% duration change.
    """
    from .pipeline import TakeStore

    store = TakeStore(Path(path).expanduser())
    store.ingest()

    out: list[tuple[float, int]] = []
    with open(Path(path).expanduser(), encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            f = line.split()
            if len(f) < 4:
                continue
            try:
                ns = int(f[0])
            except ValueError:
                continue
            i = 1
            # Valid indices are 0..len(f)-1, so the last readable triple starts
            # at len(f)-3. Same off-by-one that trapped the Swift reader.
            while i + 2 < len(f):
                try:
                    st = int(f[i], 16); d1 = int(f[i + 1], 16); d2 = int(f[i + 2], 16)
                except ValueError:
                    break
                if st & 0xF0 == 0xB0 and d1 == 64:
                    out.append((float(store.seconds(ns)), d2))
                i += 3
    return out


def write_midi(assignment: Assignment, path: str | Path, *,
               left_program: int = 33, right_program: int = 7,
               left_channel: int = 1, right_channel: int = 0,
               left_transpose: int = 0, right_transpose: int = 0,
               ticks_per_beat: int = 960, tempo_bpm: float = 120.0,
               pedal: Sequence[tuple[float, int]] | None = None) -> Path:
    """Write the assignment as a two-channel MIDI file.

    One channel per hand, each with its own program change, so a General MIDI
    synth plays the take on two instruments. Note-offs are taken from the
    capture's own pairing; nothing is re-derived.

    ``pedal`` is the CC64 stream from :func:`pedal_from_take`, and leaving it
    out is not a neutral simplification. The note-offs here are **key
    releases**, not the moments the strings stopped sounding. Under the pedal
    those are different events, often seconds apart, so a file written without
    CC64 damps every note the instant the finger lifts and a legato passage
    renders detached. The pedal is emitted on BOTH channels: controllers are
    per-channel in MIDI, so sending it only to one hand would sustain that hand
    alone.
    """
    import mido

    path = Path(path).expanduser()
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    us_per_beat = int(60_000_000 / tempo_bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=us_per_beat, time=0))
    track.append(mido.Message("program_change", channel=right_channel,
                              program=right_program, time=0))
    track.append(mido.Message("program_change", channel=left_channel,
                              program=left_program, time=0))

    sec_per_tick = (us_per_beat / 1e6) / ticks_per_beat
    ev: list[tuple[float, int, int, int, int]] = []       # t, kind, chan, pitch, vel
    t0 = assignment.notes[0].t_on if assignment.notes else 0.0
    for n, h in zip(assignment.notes, assignment.hands):
        ch = left_channel if h == LEFT else right_channel
        tr = left_transpose if h == LEFT else right_transpose
        p = min(127, max(0, n.pitch + tr))
        ev.append((n.t_on - t0, 1, ch, p, n.velocity))
        ev.append((max(n.t_off - t0, n.t_on - t0 + 0.02), 0, ch, p, 0))
    ev.sort(key=lambda e: (e[0], e[1]))

    # CC64 on both channels, merged into the same time-ordered stream. kind=2
    # sorts after note-offs at equal time so a pedal-down cannot arrive before
    # the release it is meant to catch.
    if pedal:
        for t, val in pedal:
            for ch in (right_channel, left_channel):
                ev.append((t - t0, 2, ch, 64, val))
        ev.sort(key=lambda e: (e[0], e[1]))

    prev = 0
    for t, kind, ch, p, v in ev:
        tick = int(round(t / sec_per_tick))
        dt = max(0, tick - prev)
        if kind == 2:
            track.append(mido.Message("control_change", channel=ch,
                                      control=64, value=v, time=dt))
        else:
            track.append(mido.Message("note_on" if kind else "note_off",
                                      channel=ch, note=p, velocity=v if kind else 64,
                                      time=dt))
        prev = tick
    mid.save(str(path))
    return path


def plot(assignments: Sequence[Assignment], path: str | Path, *,
         title: str = "", t0: float | None = None, t1: float | None = None,
         truth: Sequence[int | None] | None = None):
    """Pitch against time, one panel per strategy, boundary drawn over it."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(assignments)
    fig, axes = plt.subplots(n, 1, figsize=(16, 4.2 * n), sharex=True, sharey=True)
    if n == 1:
        axes = [axes]
    C = {LEFT: "#2b6cb0", RIGHT: "#c53030"}
    for ax, a in zip(axes, assignments):
        for note, h in zip(a.notes, a.hands):
            if t0 is not None and note.t_off < t0:
                continue
            if t1 is not None and note.t_on > t1:
                continue
            wrong = (truth is not None and note.hand is not None
                     and note.hand != h)
            ax.plot([note.t_on, max(note.t_off, note.t_on + 0.04)],
                    [note.pitch, note.pitch],
                    color=C[h], linewidth=2.6, solid_capstyle="butt",
                    alpha=0.95 if not wrong else 1.0, zorder=2)
            if wrong:
                ax.plot(note.t_on, note.pitch, marker="x", color="black",
                        markersize=5, markeredgewidth=1.2, zorder=3)
        bt = [t for t, _ in a.boundary]
        bb = [b for _, b in a.boundary]
        ax.step(bt, bb, where="post", color="#111111", linewidth=1.3,
                zorder=4, label="boundary")
        lc, rc = a.counts()
        ax.set_title(f"{a.strategy}   left {lc}  right {rc}", loc="left",
                     fontsize=11)
        ax.set_ylabel("MIDI pitch")
        ax.grid(alpha=0.18, linewidth=0.5)
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("seconds")
    if t0 is not None or t1 is not None:
        axes[-1].set_xlim(t0, t1)
    if title:
        fig.suptitle(title, fontsize=13)
    fig.tight_layout()
    fig.savefig(str(path), dpi=140)
    plt.close(fig)
    return Path(path)


# ---------------------------------------------------------------- CLI

def _load(src: str, segment: int | None) -> list[Note]:
    p = Path(src).expanduser()
    if p.suffix == ".fp30":
        return notes_from_take(p, segment=segment)
    if p.suffix in (".mid", ".midi"):
        return notes_from_midi(p)
    if p.suffix in (".krn", ".xml", ".musicxml", ".mxl"):
        return notes_from_kern(p)
    raise SystemExit(f"do not know how to read {p.suffix}")


def _window(notes: list[Note], t0: float | None, t1: float | None) -> list[Note]:
    if t0 is None and t1 is None:
        return notes
    z = notes[0].t_on
    lo = z + (t0 or 0.0)
    hi = z + t1 if t1 is not None else float("inf")
    return [n for n in notes if lo <= n.t_on <= hi]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fp30x_studio.hands", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("assign", "plot"):
        s = sub.add_parser(name)
        s.add_argument("source")
        s.add_argument("--strategy", default="viterbi",
                       choices=list(STRATEGIES) + ["all"])
        s.add_argument("--split", type=int, default=60)
        s.add_argument("--segment", type=int, default=None)
        s.add_argument("--from", dest="t0", type=float, default=None)
        s.add_argument("--to", dest="t1", type=float, default=None)
        s.add_argument("--out", default=None)
        if name == "assign":
            s.add_argument("--midi", default=None)
            s.add_argument("--left-program", type=int, default=33)
            s.add_argument("--right-program", type=int, default=7)
        else:
            s.add_argument("--title", default="")

    e = sub.add_parser("eval")
    e.add_argument("scores", nargs="+")
    e.add_argument("--split", type=int, default=60)
    e.add_argument("--json", default=None)

    a = ap.parse_args(argv)

    if a.cmd == "eval":
        rows = []
        for src in a.scores:
            try:
                notes = notes_from_kern(src)
            except Exception as exc:                      # noqa: BLE001
                print(f"skip {src}: {exc}", file=sys.stderr)
                continue
            truth = [n.hand for n in notes]
            row = {"score": Path(src).name, "notes": len(notes)}
            ok, n = oracle_accuracy(notes)
            row["oracle"] = round(100 * ok / n, 2) if n else 0.0
            for name in STRATEGIES:
                kw = {"split": a.split} if name == "fixed" else {}
                asg = assign(notes, name, **kw)
                ok, n = accuracy(asg.hands, truth)
                row[name] = round(100 * ok / n, 2) if n else 0.0
            rows.append(row)
            print(f"{row['score']:<22} n={row['notes']:<6} "
                  + "  ".join(f"{k}={row[k]:6.2f}"
                              for k in ("fixed", "median", "viterbi", "oracle")))
        if rows:
            tot = sum(r["notes"] for r in rows)
            print("-" * 72)
            print(f"{'weighted mean':<22} n={tot:<6} " + "  ".join(
                f"{k}={sum(r[k] * r['notes'] for r in rows) / tot:6.2f}"
                for k in ("fixed", "median", "viterbi", "oracle")))
        if a.json:
            Path(a.json).write_text(json.dumps(rows, indent=1))
        return 0

    notes = _window(_load(a.source, a.segment), a.t0, a.t1)
    if not notes:
        raise SystemExit("no notes in that window")
    names = list(STRATEGIES) if a.strategy == "all" else [a.strategy]
    asgs = [assign(notes, nm, **({"split": a.split} if nm == "fixed" else {}))
            for nm in names]

    if a.cmd == "plot":
        out = a.out or "hands.png"
        truth = [n.hand for n in notes] if any(n.hand is not None for n in notes) else None
        plot(asgs, out, title=a.title or Path(a.source).name, truth=truth)
        print(out)
        return 0

    for asg in asgs:
        lc, rc = asg.counts()
        print(f"{asg.strategy}: {len(notes)} notes, left {lc}, right {rc}")
        print("  " + json.dumps(violations(asg)))
        if any(n.hand is not None for n in notes):
            ok, n = accuracy(asg.hands, [x.hand for x in notes])
            print(f"  accuracy vs score labels: {100 * ok / n:.2f}%  ({ok}/{n})")
    if a.midi:
        write_midi(asgs[0], a.midi, left_program=a.left_program,
                   right_program=a.right_program)
        print(a.midi)
    if a.out:
        Path(a.out).write_text(json.dumps(
            [{"t_on": n.t_on, "t_off": n.t_off, "pitch": n.pitch,
              "velocity": n.velocity, "hand": h}
             for n, h in zip(asgs[0].notes, asgs[0].hands)], indent=0))
        print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
