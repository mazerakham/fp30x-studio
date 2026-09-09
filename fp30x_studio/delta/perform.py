"""The played side: a take as a note table, in the same shape as the score.

:class:`fp30x_studio.performance.Performance` already turns a ``.mid`` or a
capture into strikes, each a scaled indicator :math:`P\\cdot 1_{[a,b]}` on one
key, with the note on and note off pairing and its defect report done once. This
module reads those strikes and nothing else, then applies the same onset
clustering the score side applies, so the two note tables are comparable objects
and the aligner sees one kind of thing.

**Durations here are key held durations, not sounding durations.** The pedal is
a separate dimension of the playing and folding it into the note lengths would
report every pedalled passage as legato beyond what the fingers did. What
articulation measures is the finger.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..align.events import CLUSTER_TOL_S, Event
from .cluster import cluster_with_index

__all__ = ["PerformedNote", "PerfDoc", "load_performance"]


@dataclass(frozen=True)
class PerformedNote:
    """One key press: what the hands did."""

    index: int
    pitch: int
    t_on: float         #: seconds from the start of the clip
    dur: float          #: seconds the key was held
    velocity: int       #: MIDI velocity, 1..127


@dataclass(frozen=True)
class PerfDoc:
    notes: tuple[PerformedNote, ...]
    events: tuple[Event, ...]
    event_notes: tuple[tuple[int, ...], ...]
    source: str
    sha256: str
    t_end: float
    defects: str        #: the parse report, carried so it is never invisible

    def event_of_note(self) -> dict[int, int]:
        return {i: e for e, group in enumerate(self.event_notes) for i in group}


def load_performance(path: str | Path, *, tol: float = CLUSTER_TOL_S) -> PerfDoc:
    """Read a ``.mid`` or ``.fp30x`` take into a note table and its events."""
    from ..performance import Performance

    path = Path(path).expanduser()
    if path.suffix == ".fp30x":
        perf = Performance.from_raw_capture(path)
    else:
        perf = Performance.from_midi_file(path)

    strikes = sorted(perf.strikes(), key=lambda s: (s.t_on, s.note))
    notes = tuple(PerformedNote(index=i, pitch=s.note, t_on=float(s.t_on),
                                dur=float(s.duration), velocity=int(s.velocity))
                  for i, s in enumerate(strikes))
    onsets = [(n.t_on, n.pitch, n.velocity) for n in notes]
    groups = cluster_with_index(onsets, tol=tol)
    events = tuple(_event(onsets, g) for g in groups)

    from ..align.events import cluster_onsets
    assert events == tuple(cluster_onsets(onsets, tol=tol)), \
        "performance clustering diverged from fp30x_studio.align.events"

    report = perf.report.summary()
    return PerfDoc(notes=notes, events=events, event_notes=groups,
                   source=str(path),
                   sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   t_end=float(perf.t_end), defects=report)


def _event(onsets, group) -> Event:
    run = [onsets[i] for i in group]
    return Event(t=float(run[0][0]), notes=frozenset(int(r[1]) for r in run),
                 n=len(run), velocity=max(int(r[2]) for r in run))
