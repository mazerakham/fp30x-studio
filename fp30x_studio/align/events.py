"""From a take to a sequence of onset events.

Three reductions, in order, each with a stated reason.

**Notes come from the pipeline, never from a hand parser.** A CoreMIDI packet
may carry several MIDI messages, and every hand-rolled reader on this machine
has at some point split them wrongly. :class:`~fp30x_studio.pipeline.TakeStore`
owns the framing and the note-on/note-off pairing; this module reads its
``interval`` rows and nothing else.

**Silences longer than a threshold cut the take into segments.** A session file
holds several attempts back to back. A gap of more than a few seconds between
consecutive onsets is not phrasing, it is Jake stopping.

**Simultaneous notes cluster into one event.** The FP-30X arrives over BLE and
a chord lands as a ~5 ms arpeggio, so a chord is several rows with distinct
timestamps. Onsets within ``tol`` of the first of a run become one event whose
time is the earliest of them. ``tol`` defaults to 55 ms: wide enough to gather
a rolled chord, narrow enough not to swallow a genuine semiquaver at speed.

**The timestamps sit on a 5.000 ms lattice.** That is a BLE artefact of the
transport, and it is the noise floor for everything downstream: no alignment
claim finer than 5 ms means anything, whatever the residual arithmetic says.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["Event", "LATTICE_S", "Segment", "load_segments", "cluster_onsets"]

#: The transport's timestamp quantum, in seconds. The noise floor.
LATTICE_S = 0.005

#: Default onset-cluster tolerance: a rolled chord, not a fast run.
CLUSTER_TOL_S = 0.055

#: Default silence that separates one attempt from the next.
SEGMENT_GAP_S = 6.0


@dataclass(frozen=True)
class Event:
    """One onset event: a chord, or a single note."""

    t: float                      #: earliest onset in the cluster, seconds
    notes: frozenset[int]         #: MIDI note numbers struck together
    n: int                        #: how many rows were folded in
    velocity: int                 #: loudest note-on velocity in the cluster

    @property
    def pitch_classes(self) -> frozenset[int]:
        return frozenset(n % 12 for n in self.notes)


@dataclass(frozen=True)
class Segment:
    """A contiguous attempt: everything between two long silences."""

    index: int
    events: tuple[Event, ...]
    n_notes: int
    t0: float                     #: start, in take seconds
    label: str = ""

    @property
    def duration(self) -> float:
        return self.events[-1].t - self.events[0].t

    def shifted(self) -> tuple[Event, ...]:
        """Events with the first onset moved to ``t = 0``."""
        z = self.events[0].t
        return tuple(Event(e.t - z, e.notes, e.n, e.velocity) for e in self.events)

    def window(self, seconds: float) -> "Segment":
        """The opening ``seconds`` of this segment, as a segment."""
        z = self.events[0].t
        kept = tuple(e for e in self.events if e.t - z <= seconds)
        return Segment(self.index, kept, sum(e.n for e in kept), self.t0,
                       f"{self.label} [first {seconds:g} s]".strip())


def cluster_onsets(onsets, *, tol: float = CLUSTER_TOL_S) -> list[Event]:
    """Fold near-simultaneous onsets into chord events.

    ``onsets`` is an iterable of ``(t, note, velocity)``. The window is measured
    from the **first** member of a run, not the previous one, so a fast scale
    cannot chain-collapse into a single event.
    """
    rows = sorted(onsets, key=lambda r: (r[0], r[1]))
    out: list[Event] = []
    run: list[tuple] = []
    for r in rows:
        if run and r[0] - run[0][0] > tol:
            out.append(_event(run))
            run = []
        run.append(r)
    if run:
        out.append(_event(run))
    return out


def _event(run) -> Event:
    return Event(t=float(run[0][0]), notes=frozenset(int(r[1]) for r in run),
                 n=len(run), velocity=max(int(r[2]) for r in run))


def load_segments(take: str | Path, *, gap: float = SEGMENT_GAP_S,
                  tol: float = CLUSTER_TOL_S, min_notes: int = 1
                  ) -> list[Segment]:
    """Ingest a ``.fp30`` take and split it into attempts.

    Segment indices are assigned before ``min_notes`` filtering, so they stay
    stable when the filter changes -- a segment referred to by number in a
    report keeps meaning the same thing.
    """
    from ..pipeline import TakeStore

    store = TakeStore(take)
    store.ingest()
    rows = [(store.seconds(r["ns_on"]), r["note"], r["velocity_on"])
            for r in store.intervals()]
    rows.sort()
    if not rows:
        return []

    cuts = [0]
    for i in range(1, len(rows)):
        if rows[i][0] - rows[i - 1][0] > gap:
            cuts.append(i)
    cuts.append(len(rows))

    segs = []
    for idx in range(len(cuts) - 1):
        chunk = rows[cuts[idx]:cuts[idx + 1]]
        segs.append(Segment(index=idx, events=tuple(cluster_onsets(chunk, tol=tol)),
                            n_notes=len(chunk), t0=chunk[0][0]))
    return [s for s in segs if s.n_notes >= min_notes]
