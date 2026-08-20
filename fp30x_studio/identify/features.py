"""Turning a live note stream into a stable, hashable melodic line.

Everything here is written to be fed forward and never revisited. A tick hands
this layer only the intervals the pipeline has not shown it before, and the
layer answers with the *new* committed positions of each line. Position ``i`` of
a committed line means the same thing on every later tick, which is the whole
reason the matcher below can accumulate votes instead of recomputing them.

Four properties of this stream drive the design, and each cost a measurement to
learn (see ``PROVENANCE.md`` and the pipeline docstrings):

**Chords arrive as arpeggios.** Both hands come down one undifferentiated MIDI
stream and the BLE link serialises them, so a struck chord lands as a ~5 ms
spray of note-ons. Onsets within :data:`CLUSTER_NS` are therefore one event.

**Timestamps sit on a 5.000 ms lattice.** Nothing below 5 ms is real. That is
harmless here: the smallest quantity this layer cares about is a 55 ms cluster
window, and rhythm is only ever used as a *ratio* of inter-onset gaps.

**Ornaments are dense.** One Chopin take carried 38 trill runs, 30 of them in a
single minute. Left in, they inflate a bar's note count severalfold and every
n-gram that overlaps one is garbage. :class:`Line` collapses a run of >= 5
alternating notes a semitone or tone apart to its principal note before the
n-grams are cut.

**There is no voice separation.** So two lines are kept, the skyline and the
bass line, and the matcher tries both: Joplin's melody is octave-doubled (the
skyline holds it) and Chopin's frequently is not the top voice at all.

A note on the commit lag: a trill is only recognisable once it has ended, so the
last :data:`COMMIT_LAG` events of each line are held back. That is the price of
stable indices, and it is about a second of music.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

__all__ = [
    "CLUSTER_NS", "TRILL_GAP_NS", "TRILL_MIN_RUN", "TRILL_MAX_STEP",
    "COMMIT_LAG", "SEGMENT_GAP_NS", "IV_CLAMP",
    "REGISTER_WINDOW", "REGISTER_DROP",
    "Event", "Clusterer", "Line", "RegisterLine", "Segment", "intervals_of",
    "ngrams_of", "collapse_trills",
]

#: Onsets no further apart than this are one struck event. Chords reach us as
#: ~5 ms arpeggios; 55 ms is wide enough to gather a rolled chord and narrow
#: enough to keep a 120 bpm sixteenth (125 ms) as its own event.
CLUSTER_NS = 55_000_000

#: Longest gap between two notes of the same trill run.
TRILL_GAP_NS = 170_000_000

#: A run this long, or longer, counts as an ornament rather than as melody.
TRILL_MIN_RUN = 5

#: Trills and mordents move by a semitone or a tone. Anything wider is melody.
TRILL_MAX_STEP = 2

#: Safety valve on how many events a line may hold back waiting for an ornament
#: to end. Reached only inside a trill longer than this, where the melody has by
#: definition stopped moving anyway.
COMMIT_LAG = 64

#: Silence longer than this ends a piece and starts a new one. Measured: the
#: four pieces of the 44-minute session take are separated by gaps > 6 s.
SEGMENT_GAP_NS = 6_000_000_000

#: Accepted notes remembered when deciding what the melodic register is.
REGISTER_WINDOW = 8

#: A skyline note this far below the melodic register is the left hand playing
#: alone between melody notes, not the melody dropping an octave.
REGISTER_DROP = 11

#: Melodic intervals are clamped here. Beyond a tenth the exact size carries
#: little identifying information and a lot of octave-displacement noise.
IV_CLAMP = 15


@dataclass(slots=True)
class Event:
    """One struck simultaneity: everything that landed inside 55 ms."""

    ns: int
    lo: int
    hi: int
    n: int = 1


class Clusterer:
    """Streaming onset clustering. Emits a cluster once it cannot grow."""

    __slots__ = ("window_ns", "_ns", "_lo", "_hi", "_n", "_open")

    def __init__(self, window_ns: int = CLUSTER_NS):
        self.window_ns = window_ns
        self._ns = 0
        self._lo = 0
        self._hi = 0
        self._n = 0
        self._open = False

    def feed(self, onsets: Iterable[tuple[int, int]]) -> list[Event]:
        """Feed ``(ns, note)`` in onset order; return the clusters now closed."""
        out: list[Event] = []
        for ns, note in onsets:
            if self._open and ns - self._ns <= self.window_ns:
                if note < self._lo:
                    self._lo = note
                if note > self._hi:
                    self._hi = note
                self._n += 1
                continue
            if self._open:
                out.append(Event(self._ns, self._lo, self._hi, self._n))
            self._ns, self._lo, self._hi, self._n = ns, note, note, 1
            self._open = True
        return out

    def flush(self) -> list[Event]:
        """Close the cluster still open. Only for a stream known to have ended."""
        if not self._open:
            return []
        self._open = False
        return [Event(self._ns, self._lo, self._hi, self._n)]


def collapse_trills(ns: Sequence[int], pitches: Sequence[int], *,
                    limit: int | None = None) -> list[int]:
    """Indices of ``pitches`` surviving trill collapse, in order.

    A run is >= :data:`TRILL_MIN_RUN` notes, each within :data:`TRILL_MAX_STEP`
    semitones of the last, alternating in direction, each within
    :data:`TRILL_GAP_NS` of the last. The run is replaced by its first note --
    the principal -- so the melodic skeleton keeps the note the ornament
    decorates and loses the decoration.

    ``limit`` stops the walk early; a run that would reach past it is left for a
    later call, when its end is known.
    """
    keep: list[int] = []
    n = len(pitches)
    stop = n if limit is None else min(n, limit)
    i = 0
    while i < stop:
        run = _run_length(ns, pitches, i)
        if run >= TRILL_MIN_RUN:
            if limit is not None and i + run >= n - 1:
                break  # the run may still be growing; decide when it has ended
            keep.append(i)
            i += run
        else:
            keep.append(i)
            i += 1
    return keep


def _run_length(ns: Sequence[int], pitches: Sequence[int], i: int) -> int:
    """How many notes from ``i`` form one alternating ornament run."""
    n = len(pitches)
    if i + 1 >= n:
        return 1
    d = pitches[i + 1] - pitches[i]
    if not (0 < abs(d) <= TRILL_MAX_STEP) or ns[i + 1] - ns[i] > TRILL_GAP_NS:
        return 1
    k = i + 1
    prev = d
    while k + 1 < n:
        d2 = pitches[k + 1] - pitches[k]
        if not (0 < abs(d2) <= TRILL_MAX_STEP):
            break
        if (d2 > 0) == (prev > 0):
            break
        if ns[k + 1] - ns[k] > TRILL_GAP_NS:
            break
        prev = d2
        k += 1
    return k - i + 1


class Line:
    """One monophonic voice extracted from the event stream, index-stable.

    ``pitches[i]`` and ``ns[i]`` never change once written, so an n-gram cut at
    position ``i`` is the same n-gram on every future tick.
    """

    __slots__ = ("name", "pick", "pitches", "ns", "_bns", "_bp")

    def __init__(self, name: str, pick: Callable[[Event], int]):
        self.name = name
        self.pick = pick
        self.pitches: list[int] = []
        self.ns: list[int] = []
        self._bns: list[int] = []
        self._bp: list[int] = []

    def feed(self, events: Iterable[Event], *, final: bool = False) -> int:
        """Add events; return how many new positions were committed."""
        for e in events:
            p = self.pick(e)
            if self._bp and p == self._bp[-1] and e.ns - self._bns[-1] <= CLUSTER_NS:
                continue  # same key re-reported inside one cluster
            self._bp.append(p)
            self._bns.append(e.ns)
        return self._commit(final=final)

    def _commit(self, *, final: bool) -> int:
        """Commit every position whose reading can no longer change.

        Only two things can still change a buffered note's reading: the next
        note, which fixes the interval leaving it, and an ornament run it might
        turn out to belong to, which is not decidable until the run ends. So the
        hold-back is one note in ordinary texture and the length of the run
        inside an ornament -- not a fixed lag. That matters: a fixed lag of two
        dozen events is six seconds of silence from the identifier at exactly
        the moment an answer is wanted.
        """
        limit = None if final else len(self._bp) - 1
        if limit is not None and limit <= 0:
            return 0
        if limit is not None and len(self._bp) > COMMIT_LAG:
            limit = max(limit, len(self._bp) - COMMIT_LAG)
        keep = collapse_trills(self._bns, self._bp, limit=limit)
        if not keep:
            return 0
        for i in keep:
            self.pitches.append(self._bp[i])
            self.ns.append(self._bns[i])
        cut = keep[-1] + _run_length(self._bns, self._bp, keep[-1])
        del self._bp[:cut]
        del self._bns[:cut]
        return len(keep)

    def __len__(self) -> int:
        return len(self.pitches)


class RegisterLine(Line):
    """The skyline, minus the left hand showing through the gaps in it.

    A plain skyline is right for a texture where the melody is always on top.
    It is wrong for almost all piano writing, where the melody rests for a beat
    and the accompaniment becomes, briefly, the highest thing sounding. On the
    Chopin nocturne take that puts bass notes two octaves below the tune into
    the middle of every phrase, and every n-gram spanning one is destroyed.

    So a note is admitted only if it is within :data:`REGISTER_DROP` semitones
    of the median of the last :data:`REGISTER_WINDOW` admitted notes. The band
    follows the melody up and down at melodic speed and cannot follow it down an
    octave and a half in one event. Nothing is dropped from the take -- the
    other lines still see every event -- this is one more view of it.
    """

    __slots__ = ("_recent", "drop")

    def __init__(self, name: str = "mel",
                 pick: Callable[[Event], int] = lambda e: e.hi, *,
                 window: int = REGISTER_WINDOW, drop: int = REGISTER_DROP):
        super().__init__(name, pick)
        self._recent: deque[int] = deque(maxlen=window)
        self.drop = drop

    def feed(self, events: Iterable[Event], *, final: bool = False) -> int:
        kept: list[Event] = []
        for e in events:
            p = self.pick(e)
            if self._recent and p < statistics.median(self._recent) - self.drop:
                continue
            self._recent.append(p)
            kept.append(e)
        return super().feed(kept, final=final)


def intervals_of(pitches: Sequence[int]) -> list[int]:
    """Melodic intervals, clamped. Transposition-invariant by construction."""
    out = []
    for a, b in zip(pitches, pitches[1:]):
        d = b - a
        out.append(IV_CLAMP if d > IV_CLAMP else -IV_CLAMP if d < -IV_CLAMP else d)
    return out


def ngrams_of(pitches: Sequence[int], n: int, *, start: int = 0
              ) -> list[tuple[int, tuple[int, ...]]]:
    """``(position, interval-tuple)`` for every n-gram at or after ``start``."""
    out: list[tuple[int, tuple[int, ...]]] = []
    last = len(pitches) - n - 1
    for i in range(start, last + 1):
        g = []
        for k in range(n):
            d = pitches[i + k + 1] - pitches[i + k]
            if d > IV_CLAMP:
                d = IV_CLAMP
            elif d < -IV_CLAMP:
                d = -IV_CLAMP
            g.append(d)
        out.append((i, tuple(g)))
    return out


@dataclass(slots=True)
class Segment:
    """One continuous stretch of playing: a piece, or an attempt at one."""

    index: int
    start_ns: int = 0
    last_ns: int = 0
    notes: int = 0
    clusterer: Clusterer = field(default_factory=Clusterer)
    lines: dict[str, Line] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.lines:
            self.lines = {
                "mel": RegisterLine("mel"),
                "top": Line("top", lambda e: e.hi),
                "bass": Line("bass", lambda e: e.lo),
            }

    @property
    def seconds(self) -> float:
        return (self.last_ns - self.start_ns) / 1e9

    def feed(self, onsets: Sequence[tuple[int, int]], *, final: bool = False
             ) -> dict[str, int]:
        if onsets:
            if not self.start_ns:
                self.start_ns = onsets[0][0]
            self.last_ns = onsets[-1][0]
            self.notes += len(onsets)
        events = self.clusterer.feed(onsets)
        if final:
            events = list(events) + self.clusterer.flush()
        return {name: line.feed(events, final=final)
                for name, line in self.lines.items()}
