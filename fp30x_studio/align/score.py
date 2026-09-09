r"""The score side: Chopin's text as keystroke timings, carrying bar numbers.

Everything else in :mod:`fp30x_studio.align` compares one performance against
another, and can therefore only ever say *these two attempts are the same
music*. It cannot say **which** music, because a performance does not know
where the bar lines are. This module supplies the missing side.

A Humdrum ``**kern`` score is read into exactly the shape a take produces —
onset-clustered :class:`~.events.Event` values with pitch sets — so it drops
straight into :func:`~.corr.gram`, :func:`~.correspond.correspond` and
:func:`~.warp.fit_warp` with nothing adapted. What it adds is
:attr:`ScoreEvents.measures`: the bar number of every event, so an alignment
that lands at event *i* of the score reports a **bar**, not an index.

**The timings here are notated, not played.** Offsets are in quarter notes of
score time, converted to seconds at one constant tempo. That is not a
performance and must not be read as one — the tempo is a unit of measurement,
chosen so the warp has sensible units, and any rubato in the recording shows up
as :math:`\varphi` departing from the straight line, which is the entire point.
Chopin marked this piece *Allegro maestoso* and nothing here knows what that
means in seconds.

**Which edition, and why it matters.** ``061-1-BR`` is the 1846 Brandus French
first edition and ``061-1-BH`` the Breitkopf German one — the two texts Chopin
saw through the press, encoded by the Humdrum Chopin First Editions project.
They are not identical to each other. Aligning a performance against a modern
editor's text would attach bar numbers from a source Chopin never approved, so
the default is the first edition, named in the output.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .events import CLUSTER_TOL_S, Event, cluster_onsets

__all__ = ["ScoreEvents", "load_score", "DEFAULT_QPM"]

#: Quarter notes per minute used to turn score offsets into seconds. A unit of
#: measurement, not an interpretation: see the module docstring.
DEFAULT_QPM = 100.0

#: Score-time separation given to consecutive grace notes sharing one offset,
#: in quarter notes. Small enough to stay inside the beat it decorates, large
#: enough to clear the onset-clustering tolerance so each becomes its own event.
GRACE_SPREAD_QL = 0.06


@dataclass(frozen=True)
class ScoreEvents:
    """Onset events of a notated score, each tagged with its bar."""

    events: tuple[Event, ...]
    measures: tuple[int, ...]        #: bar number, parallel to ``events``
    source: str                      #: the file these came from
    edition: str                     #: human name of the edition
    title: str
    qpm: float
    n_measures: int
    n_notes: int

    def __post_init__(self):
        if len(self.events) != len(self.measures):
            raise ValueError("events and measures must be parallel")

    def measure_of(self, i: int) -> int:
        return self.measures[i]

    def span(self, i: int, j: int) -> tuple[int, int]:
        """Bar range covered by events ``i..j`` inclusive."""
        lo = min(self.measures[i], self.measures[j])
        hi = max(self.measures[i], self.measures[j])
        return lo, hi

    def rows(self) -> list[dict]:
        """One row per onset event, for writing out."""
        return [
            {"i": i, "measure": m, "t": round(e.t, 4),
             "notes": sorted(e.notes), "n": e.n, "velocity": e.velocity}
            for i, (e, m) in enumerate(zip(self.events, self.measures))
        ]


#: Publisher sigla used in the Humdrum Chopin First Editions filenames, from
#: that repository's own README. The siglum is a token of the stem, so
#: ``061-1-BR`` and ``062-1-BR-001`` both resolve to Brandus.
EDITIONS = {
    "BH": "Breitkopf und Haertel, Leipzig",
    "Sm": "Maurice Schlesinger, Paris",
    "KI": "Fr. Kistner, Leipzig",
    "Sam": "Ad. Mt. Schlesinger, Berlin",
    "W": "Wessel and Co., London",
    "C": "Ad. Catelin et Cie, Paris",
    "BR": "Brandus et Cie, Paris",
    "TR": "E. Troupenas et Cie, Paris",
    "MEIf": "J. Meissonnier fils, Paris",
    "LW": "Stanley Lucas, Weber and Co., London",
    "G": "Gustaw Gebethner i Spolka, Warsaw",
    "LE": "Henry Lemoine, Paris",
    "ME": "Pietro Mechetti, Vienna",
    "MEIj": "J. Meissonnier, Paris",
    "HAc": "Charles Haslinger, Vienna",
    "GE": "E. Gerard et Cie, Paris",
    "COC": "Robert Cocks and Co., London",
    "ST": "Stern et Cie, Berlin",
    "HO": "Friedrich Hofmeister, Leipzig",
    "WS": "Wessel and Stapleton, London",
    "SCHU": "Schuberth und Comp., Hamburg",
    "SCH": "B. Schott's Soehne, Mainz",
}


def load_score(path: str | Path, *, qpm: float = DEFAULT_QPM,
               tol: float = CLUSTER_TOL_S,
               velocity: int = 64) -> ScoreEvents:
    """Parse a ``**kern`` (or MusicXML, or MIDI) score into onset events.

    Grace notes are kept and **spread**. They are struck, so a performance
    contains them, and dropping them would put every later onset out of
    correspondence. But notation gives a whole grace flourish one offset — this
    score has twelve places where eight or more notes share an instant, up to
    28 at once — and clustering would then fold a written-out sweep into a
    single 28-note chord that no performance can ever match. Each grace note is
    therefore given a small increasing offset within its beat, in written
    order, so it becomes its own event exactly as it does under the hands.
    ``GRACE_SPREAD_QL`` is a notation-repair constant and nothing about how
    fast the sweep is actually played.
    """
    from music21 import converter

    path = Path(path).expanduser()
    s = converter.parse(str(path))
    spq = 60.0 / qpm                       # seconds per quarter note

    onsets: list[tuple[float, int, int]] = []
    at_measure: dict[float, int] = {}
    n_notes = 0
    for part in s.parts:
        for m in part.getElementsByClass("Measure"):
            num = m.number
            grace_at: dict[float, int] = {}
            for el in m.recurse().notes:
                off = float(m.offset + el.offset)
                if el.duration.isGrace:
                    k = grace_at.get(off, 0)
                    grace_at[off] = k + 1
                    off += k * GRACE_SPREAD_QL
                off = round(off, 6)
                # A bar line is the earliest claim on a shared offset: two
                # staves reach the same beat, and both must call it one bar.
                at_measure[off] = min(at_measure.get(off, num), num)
                for p in el.pitches:
                    onsets.append((off * spq, int(p.midi), velocity))
                    n_notes += 1

    events = tuple(cluster_onsets(onsets, tol=tol))
    # Each cluster takes the bar of its own first onset, looked up on the
    # score-offset grid it came from.
    measures = tuple(at_measure[round(e.t / spq, 6)]
                     if round(e.t / spq, 6) in at_measure
                     else _nearest(at_measure, e.t / spq)
                     for e in events)

    md = s.metadata
    # The edition code is a token of the stem, not necessarily the last one:
    # ``061-1-BR`` ends in it and ``062-1-BR-001`` does not.
    code = next((t for t in path.stem.split("-") if t in EDITIONS),
                path.stem.split("-")[-1])
    n_meas = max((m.number for p in s.parts
                  for m in p.getElementsByClass("Measure")), default=0)
    return ScoreEvents(
        events=events, measures=measures, source=str(path),
        edition=EDITIONS.get(code, code),
        title=(md.title if md and md.title else path.stem),
        qpm=qpm, n_measures=int(n_meas), n_notes=n_notes,
    )


def _nearest(grid: dict[float, int], q: float) -> int:
    k = min(grid, key=lambda x: abs(x - q))
    return grid[k]
