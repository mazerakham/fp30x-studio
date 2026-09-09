r"""The score side, at note level: every notated note with its bar and duration.

:mod:`fp30x_studio.align.score` reads a ``**kern`` score into *onset events*,
which is what a correspondence needs. A delta needs one more thing: the
individual notes inside each event, because the delta is indexed by score note
and an event is a chord. This module is that refinement. It uses the same
music21 pass, the same grace-note spreading constant and the same clustering
function as the align layer, and it asserts that the events it builds are the
events :func:`fp30x_studio.align.events.cluster_onsets` builds, so the two
layers cannot drift apart.

Three notation repairs, each stated because each changes a count:

**Ties are joined.** A tie is one keystroke written as two note heads. Left
alone, the continuation has no performed note to match and is reported as a
missed note, which is false. A note whose tie type is ``stop`` or ``continue``
is folded into the sounding note of the same pitch that began the tie, adding
its quarter length. 35 of the 1121 note heads in Op. 72 No. 1 are tie
continuations.

**Unisons are folded.** Two staves may notate the same pitch at the same
offset. There is one key on the piano and it goes down once, so the two heads
are one note; the longer duration wins.

**Grace notes are spread and marked.** Spreading is inherited from the align
layer: notation gives a whole flourish one offset, and a performance plays it
as a sweep. A grace note has zero notated duration, so its articulation
:math:`\log(d_{\text{played}}/d_{\text{notated}})` does not exist. It is
carried in the note list, matched, and given timing and velocity components,
and it is excluded from the articulation vector rather than assigned a value.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..align.events import CLUSTER_TOL_S, Event, cluster_onsets
from ..align.score import DEFAULT_QPM, EDITIONS, GRACE_SPREAD_QL
from .cluster import cluster_with_index

__all__ = ["ScoreNote", "ScoreDoc", "ScoreParseError", "load_score_notes",
           "parse_health"]


class ScoreParseError(ValueError):
    """The parser did not read the file as the file is written."""


@dataclass(frozen=True)
class ScoreNote:
    """One notated note: what the page asks for."""

    index: int          #: position in score order, the index the delta is on
    pitch: int          #: MIDI note number
    beat: float         #: onset in quarter notes from the start of the piece
    dur_ql: float       #: notated duration in quarter notes; 0 for a grace note
    measure: int        #: bar number
    is_grace: bool


@dataclass(frozen=True)
class ScoreDoc:
    """A notated score as a note list, an event list, and the map between."""

    notes: tuple[ScoreNote, ...]
    events: tuple[Event, ...]
    event_notes: tuple[tuple[int, ...], ...]   #: note indices per event
    measures: tuple[int, ...]                  #: bar per event
    source: str
    sha256: str
    title: str
    edition: str
    qpm: float
    n_measures: int
    tied_heads: int
    unison_heads: int

    @property
    def beats(self):
        return [n.beat for n in self.notes]

    def event_of_note(self) -> dict[int, int]:
        return {i: e for e, group in enumerate(self.event_notes) for i in group}


def _edition_of(stem: str) -> str:
    """Name the edition from whichever stem token is an edition code.

    ``062-1-BR-001`` is the Brandus first edition; the code is the third token,
    not the last, so taking the last token names the edition ``001``.
    """
    for tok in stem.split("-"):
        if tok in EDITIONS:
            return EDITIONS[tok]
    return stem


def parse_health(s) -> dict:
    """What each part of a parsed score covers, for the check below."""
    parts = []
    for p in s.parts:
        measures = list(p.getElementsByClass("Measure"))
        offs = [float(m.offset + el.offset)
                for m in measures for el in m.recurse().notes]
        parts.append({"measures": len(measures),
                      "last_offset": max(offs) if offs else 0.0,
                      "notes": len(offs)})
    return {"parts": parts}


def _check_health(h: dict, path: Path) -> None:
    r"""Refuse a score whose parts do not agree about how long the piece is.

    Every part of a piano score ends at the same bar line. When one part of a
    parse ends a hundred quarter notes before another, the parser has folded or
    dropped something and the offsets in that part are fiction, so every onset
    downstream is fiction too. That failure is silent: the notes are all there,
    the pitches are right, and only the times are wrong, which produces a
    plausible looking delta with a low match rate rather than an error.

    The Humdrum first editions of Op. 62 No. 1 are the case this was written
    for. Both of them use ``*^`` spine splits, and music21 reads the left hand
    of ``062-1-BR-001.krn`` as 47 measures ending at quarter note 280 against
    the right hand's 94 measures ending at 372. There is nothing to be done
    with that here, and a delta fitted to it matched 34% of the page. Convert
    the file with ``hum2mid`` from humlib, or read a different edition.
    """
    parts = h["parts"]
    if len(parts) < 2:
        return
    ends = [p["last_offset"] for p in parts]
    counts = [p["measures"] for p in parts]
    # One bar of slack: a part may legitimately rest through the final bar.
    if max(ends) - min(ends) > 8.0 or max(counts) - min(counts) > 1:
        detail = ", ".join(f"part {i}: {p['measures']} measures ending at "
                           f"quarter note {p['last_offset']:g}"
                           for i, p in enumerate(parts))
        raise ScoreParseError(
            f"{path.name} did not parse as one piece: {detail}. The parts must "
            f"end together and they do not, so the onsets in the short part are "
            f"wrong and no delta fitted to this file would mean anything.")


def load_score_notes(path: str | Path, *, qpm: float = DEFAULT_QPM,
                     tol: float = CLUSTER_TOL_S, velocity: int = 64,
                     check: bool = True) -> ScoreDoc:
    """Parse a ``**kern`` (or MusicXML, or MIDI) score into notes and events."""
    from music21 import converter

    path = Path(path).expanduser()
    raw = path.read_bytes()
    s = converter.parse(str(path))
    if check:
        _check_health(parse_health(s), path)

    # (beat, pitch) -> row index, so a unison in two staves folds into one note
    # and a tie continuation can find the note it continues.
    rows: list[dict] = []
    at: dict[tuple[float, int], int] = {}
    open_tie: dict[tuple[int, int], int] = {}     # (part, pitch) -> row index
    tied = unison = 0

    for pi, part in enumerate(s.parts):
        for m in part.getElementsByClass("Measure"):
            num = int(m.number)
            grace_at: dict[float, int] = {}
            for el in m.recurse().notes:
                off = float(m.offset + el.offset)
                grace = bool(el.duration.isGrace)
                if grace:
                    k = grace_at.get(off, 0)
                    grace_at[off] = k + 1
                    off += k * GRACE_SPREAD_QL
                off = round(off, 6)
                ql = float(el.duration.quarterLength)
                tie = el.tie.type if el.tie else None
                for p in el.pitches:
                    pitch = int(p.midi)
                    if tie in ("stop", "continue"):
                        src = open_tie.get((pi, pitch))
                        if src is not None:
                            rows[src]["dur_ql"] += ql
                            tied += 1
                            if tie == "stop":
                                open_tie.pop((pi, pitch), None)
                            continue
                    key = (off, pitch)
                    if key in at:
                        r = rows[at[key]]
                        r["dur_ql"] = max(r["dur_ql"], ql)
                        r["measure"] = min(r["measure"], num)
                        unison += 1
                    else:
                        at[key] = len(rows)
                        rows.append({"beat": off, "pitch": pitch, "dur_ql": ql,
                                     "measure": num, "grace": grace})
                    if tie == "start":
                        open_tie[(pi, pitch)] = at[key]

    rows.sort(key=lambda r: (r["beat"], r["pitch"]))
    notes = tuple(ScoreNote(index=i, pitch=r["pitch"], beat=r["beat"],
                            dur_ql=r["dur_ql"], measure=r["measure"],
                            is_grace=r["grace"])
                  for i, r in enumerate(rows))

    spq = 60.0 / qpm
    onsets = [(n.beat * spq, n.pitch, velocity) for n in notes]
    groups = cluster_with_index(onsets, tol=tol)
    events = tuple(_event(onsets, g) for g in groups)
    assert events == tuple(cluster_onsets(onsets, tol=tol)), \
        "score clustering diverged from fp30x_studio.align.events"

    measures = tuple(min(notes[i].measure for i in g) for g in groups)
    md = s.metadata
    n_meas = max((int(m.number) for p in s.parts
                  for m in p.getElementsByClass("Measure")), default=0)
    return ScoreDoc(
        notes=notes, events=events, event_notes=groups, measures=measures,
        source=str(path), sha256=hashlib.sha256(raw).hexdigest(),
        title=(md.title if md and md.title else path.stem),
        edition=_edition_of(path.stem), qpm=qpm, n_measures=n_meas,
        tied_heads=tied, unison_heads=unison,
    )


def _event(onsets, group) -> Event:
    run = [onsets[i] for i in group]
    return Event(t=float(run[0][0]),
                 notes=frozenset(int(r[1]) for r in run),
                 n=len(run), velocity=max(int(r[2]) for r in run))
