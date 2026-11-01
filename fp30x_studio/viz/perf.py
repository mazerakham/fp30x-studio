r"""The performance, as the take recorded it -- and where the trills are.

This module reads note *data* only: onset, release, pitch, velocity, and the
damper pedal. Nothing here knows what a notehead looks like.

The trill problem
-----------------
A trill is one symbol in the score and twenty-odd key presses in the take. Both
of the obvious renderings fail. Print the twenty presses as twenty noteheads and
the bar turns into a black smear that no one can read. Print ``tr`` over one
notehead and you have thrown away the only thing the capture knows that the
score does not -- how fast, how even, how hard, how many.

So the trill is *segmented out* rather than notated away.
:func:`detect_trills` finds maximal runs that satisfy all four of:

* at least :data:`MIN_ONSETS` onsets in a row,
* at most three distinct pitches spanning at most three semitones,
* a median inter-onset interval below :data:`MAX_IOI` seconds,
* no two consecutive onsets further apart than :data:`MAX_GAP` seconds.

The span test is what keeps a fast scale out: a scale run has the same onset
rate as a trill and is not one. The gap test is what stops a run from bridging
the rest between two separate trills, which the median alone tolerates.

What a detected run reports is then enough for the scene to draw all three
layers of the visual language: a principal notehead at the run's start (the
lower of the alternating pitches -- the note a score would print), every
individual onset as its own faint mark, and a shimmer whose crest count equals
:attr:`Trill.n_onsets` exactly.

Onset rate, not velocity, drives the trill's colour. Velocity already has a job
(see :mod:`.scene`), and rate is the quantity a trill is actually *about*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

MIN_ONSETS = 6
MAX_IOI = 0.135
MAX_GAP = 0.24
MAX_SEMITONE_SPAN = 3
MAX_DISTINCT = 3


@dataclass(frozen=True)
class Note:
    """One key press, in take seconds."""

    t: float
    t_off: float
    pitch: int
    velocity: int

    @property
    def duration(self) -> float:
        return max(self.t_off - self.t, 0.02)


@dataclass
class Trill:
    """A maximal run of dense alternation."""

    notes: list[Note] = field(default_factory=list)

    @property
    def t0(self) -> float:
        return self.notes[0].t

    @property
    def t1(self) -> float:
        return self.notes[-1].t_off

    @property
    def n_onsets(self) -> int:
        return len(self.notes)

    @property
    def principal(self) -> int:
        """The pitch a score would print: the lower of the alternating pair."""
        return min(n.pitch for n in self.notes)

    @property
    def upper(self) -> int:
        return max(n.pitch for n in self.notes)

    @property
    def rate(self) -> float:
        """Onsets per second."""
        return self.n_onsets / max(self.t1 - self.t0, 1e-3)

    @property
    def mean_velocity(self) -> float:
        return sum(n.velocity for n in self.notes) / self.n_onsets


def load_window(take: str | Path, t0: float, t1: float) -> tuple[list[Note], list[tuple[float, int]]]:
    """Notes sounding in ``[t0, t1)``, plus the damper pedal over the same window.

    A note that began *before* ``t0`` and is still down is kept, clamped to
    ``t0``. Dropping it instead leaves the window's opening bar visually empty
    while the audio is plainly still ringing, which is the sort of mismatch that
    makes the whole thing look out of sync when it is not.
    """
    from ..pipeline import TakeStore

    store = TakeStore(take)
    store.ingest()
    notes: list[Note] = []
    for r in store.intervals():
        on = store.seconds(r["ns_on"])
        off = store.seconds(r["ns_off"]) if r["ns_off"] else on + 0.4
        if off <= t0 or on >= t1:
            continue
        notes.append(Note(t=max(on, t0), t_off=min(off, t1),
                          pitch=int(r["note"]), velocity=int(r["velocity_on"])))
    notes.sort(key=lambda n: (n.t, n.pitch))

    pedal: list[tuple[float, int]] = []
    for r in store.messages(kinds=["control_change"]):
        try:
            if r["d1"] != 64:
                continue
        except (IndexError, KeyError):
            continue
        t = store.seconds(r["ns"])
        if t0 - 8.0 <= t < t1:
            pedal.append((max(t, t0), int(r["d2"])))
    return notes, pedal


def detect_trills(notes: list[Note]) -> tuple[list[Trill], set[int]]:
    """Partition ``notes`` into trill runs and everything else.

    Returns the runs and the set of ``id(note)`` that belong to one, so the
    caller can draw the remainder normally without re-deriving membership.
    """
    # Trills live in one hand. Restrict to the upper register before looking:
    # a right-hand trill over a moving left-hand accompaniment is not a run in
    # the combined onset stream, and searching the combined stream finds nothing.
    upper = [n for n in notes if n.pitch >= 64]
    runs: list[Trill] = []
    i = 0
    while i < len(upper):
        j = i + 1
        while j < len(upper):
            window = upper[i:j + 1]
            pitches = {n.pitch for n in window}
            if len(pitches) > MAX_DISTINCT or max(pitches) - min(pitches) > MAX_SEMITONE_SPAN:
                break
            if upper[j].t - upper[j - 1].t > MAX_GAP:
                break
            j += 1
        window = upper[i:j]
        if len(window) >= MIN_ONSETS:
            iois = [b.t - a.t for a, b in zip(window, window[1:])]
            if median(iois) <= MAX_IOI:
                runs.append(Trill(list(window)))
                i = j
                continue
        i += 1

    # A trill that dips below MAX_GAP for one beat comes back as two runs on
    # the same pitch pair. Musically it is one trill and the crest count has to
    # be the whole thing, so glue those back together before reporting.
    merged: list[Trill] = []
    for r in runs:
        if (merged and {merged[-1].principal, merged[-1].upper} == {r.principal, r.upper}
                and r.t0 - merged[-1].notes[-1].t <= 0.40):
            merged[-1].notes.extend(r.notes)
        else:
            merged.append(r)

    marked = {id(n) for r in merged for n in r.notes}
    return merged, marked
