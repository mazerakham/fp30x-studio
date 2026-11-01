r"""Material that comes back: the figures a free take states more than once.

Why this and not "themes"
-------------------------
A composing session is not a performance of a piece. What makes it usable
afterwards is not its structure -- it usually has none -- but the handful of
shapes the player kept returning to, often without noticing. Finding those is
what turns 22 minutes of playing into parts that can be arranged.

The unit is an **interval n-gram of the skyline**: the top sounding pitch at
each onset cluster, differenced. Two things follow from that choice.

*Differenced*, so a figure counts as the same figure when it comes back a fifth
higher. Transposition is the commonest way material recurs, and a pitch-literal
matcher misses all of it.

*Skyline*, so the accompaniment does not drown the melody in the match. A plain
skyline is not enough, though: wherever the right hand rests, the left hand
becomes the top voice, and the line reads that as a melodic leap of thirty-odd
semitones and back. Those pseudo-leaps then match each other across the take
and crowd out real figures. So the line is filtered by
:class:`~fp30x_studio.identify.features.RegisterLine`'s rule -- a note is
admitted only within :data:`~fp30x_studio.identify.features.REGISTER_DROP`
semitones of the running median of the ones already admitted -- which follows
the melody at melodic speed and cannot follow it down an octave and a half in
one step. Nothing is deleted from the take; the bass is simply not mistaken for
the tune.

What a match is worth
---------------------
Recurrence is scored by :func:`Motif.weight` -- occurrences times length --
because a 3-note figure appearing nine times is usually the key of C, while a
9-note figure appearing three times is a decision the player made. Short and
common is noise; long and repeated is material.

Onsets inside :data:`CLUSTER` of each other are one chord, taking the highest
pitch, and an immediately repeated pitch is collapsed, so a trill or a tremolo
does not present as a long recurring motif of alternating ones.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from .performance import Performance

# Onsets within this are the same chord, not a melodic step.
CLUSTER = 0.045
# Shortest interval sequence worth calling a figure.
MIN_N = 4
# Longest we search for; past this, matches are rare and the table is noise.
MAX_N = 12
# An occurrence must start at least this far from the previous one, or a
# sequence repeated immediately (an ostinato bar) reports every offset.
MIN_SEPARATION = 0.35


@dataclass(frozen=True, slots=True)
class Occurrence:
    t_on: float
    t_off: float
    pitch0: int          # the pitch it started on -- the transposition
    index: int           # position in the skyline sequence

    @property
    def duration(self) -> float:
        return self.t_off - self.t_on


@dataclass(slots=True)
class Motif:
    intervals: tuple[int, ...]
    occurrences: list[Occurrence] = field(default_factory=list)

    @property
    def n(self) -> int:
        """Notes in the figure (one more than the intervals between them)."""
        return len(self.intervals) + 1

    @property
    def weight(self) -> float:
        return len(self.occurrences) * self.n

    @property
    def span(self) -> int:
        """Semitones from lowest to highest point of the figure."""
        run, lo, hi = 0, 0, 0
        for d in self.intervals:
            run += d
            lo, hi = min(lo, run), max(hi, run)
        return hi - lo

    def transpositions(self) -> list[int]:
        """Distinct starting pitches, so recurrence-by-transposition is visible."""
        return sorted({o.pitch0 for o in self.occurrences})


def skyline(perf: Performance) -> list[tuple[float, float, int]]:
    """``(t_on, t_off, pitch)`` of the top voice, one entry per onset cluster."""
    ss = perf.strikes()
    if not ss:
        return []
    out: list[tuple[float, float, int]] = []
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1].t_on - ss[i].t_on < CLUSTER:
            j += 1
        top = max(ss[i:j + 1], key=lambda s: s.key)
        out.append((top.t_on, top.t_off, top.note))
        i = j + 1
    # Drop the left hand showing through a rest in the right: see the module
    # docstring. The band is the median of the last REGISTER_WINDOW admitted
    # pitches, and a note more than REGISTER_DROP below it is not the melody.
    from .identify.features import REGISTER_DROP, REGISTER_WINDOW

    kept: list[tuple[float, float, int]] = []
    recent: list[int] = []
    for e in out:
        if recent:
            med = sorted(recent)[len(recent) // 2]
            if e[2] < med - REGISTER_DROP:
                continue
        kept.append(e)
        recent.append(e[2])
        if len(recent) > REGISTER_WINDOW:
            recent.pop(0)

    # Collapse an immediately repeated pitch: a tremolo is one event melodically.
    collapsed: list[tuple[float, float, int]] = []
    for e in kept:
        if collapsed and collapsed[-1][2] == e[2]:
            collapsed[-1] = (collapsed[-1][0], e[1], e[2])
        else:
            collapsed.append(e)
    return collapsed


def find(perf: Performance, *, min_n: int = MIN_N, max_n: int = MAX_N,
         min_occurrences: int = 2) -> list[Motif]:
    """Recurring figures, heaviest first."""
    line = skyline(perf)
    if len(line) < min_n + 1:
        return []
    pitches = [p for _, _, p in line]
    steps = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]

    found: list[Motif] = []
    claimed: set[int] = set()          # skyline indices already inside a longer motif
    for n in range(max_n, min_n - 1, -1):
        if n > len(steps):
            continue
        buckets: dict[tuple[int, ...], list[int]] = defaultdict(list)
        for i in range(len(steps) - n + 1):
            buckets[tuple(steps[i:i + n])].append(i)
        for ivs, idxs in buckets.items():
            if all(d == 0 for d in ivs):
                continue
            occ: list[Occurrence] = []
            last_t = -1e9
            for i in idxs:
                if any(k in claimed for k in range(i, i + n + 1)):
                    continue
                t_on = line[i][0]
                if t_on - last_t < MIN_SEPARATION:
                    continue
                occ.append(Occurrence(t_on, line[i + n][1], pitches[i], i))
                last_t = t_on
            if len(occ) < min_occurrences:
                continue
            for o in occ:
                claimed.update(range(o.index, o.index + n + 1))
            found.append(Motif(ivs, occ))

    found.sort(key=lambda m: (-m.weight, -m.n))
    return found


def describe(m: Motif) -> str:
    """One line: how the figure moves, how often it came back, where."""
    shape = " ".join(f"{d:+d}" for d in m.intervals)
    return (f"{m.n} notes [{shape}], {len(m.occurrences)}x, "
            f"{len(m.transpositions())} transposition(s)")
