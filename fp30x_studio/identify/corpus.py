"""What the identifier needs from a theme corpus, and a small one to start with.

The corpus is a *dependency*, deliberately kept behind two dozen lines of
protocol so that the matcher can be built, tested and tuned before any corpus
exists. :class:`Theme` is the whole contract: an identity, a label a human can
read, and a monophonic sequence of MIDI pitch numbers. Durations are optional
and unused by the pitch-interval index; they are carried so a later rhythmic
refinement has somewhere to read them from.

Two corpora ship here:

``STUB`` -- themes entered by hand, each with its provenance in
``Theme.source``. Some are common-practice incipits entered from memory of the
score; the rest are *derived from Jake's own takes*, and say so. A derived theme
is honest evidence of what was played, and dishonest evidence of what the piece
is, so its ``source`` records which take and which passage it came from.

The real corpus, when it lands at :mod:`fp30x_studio.idcorpus`, is picked up by
:func:`load_corpus` without any change here: it need only expose ``themes()``
yielding objects with these attributes, or dicts with these keys.

Pitch spelling note: only the *intervals* between consecutive pitches are ever
hashed, so the octave a theme is written in is free and its key is irrelevant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Protocol, Sequence, runtime_checkable

__all__ = ["Theme", "CorpusLike", "ListCorpus", "STUB", "load_corpus",
           "note_names", "NOTE_TO_PC", "LAST_CORPUS_ERROR"]

#: Why the real corpus was not loaded, if it was not. Read by the runner's
#: banner. A silent fallback to fifteen themes looks exactly like a working
#: identifier that never recognises anything, which is the worst way to fail.
LAST_CORPUS_ERROR: str = ""

NOTE_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_names(spec: str, *, octave: int = 4) -> tuple[int, ...]:
    """Parse ``"G G F E D C B C D G"`` into MIDI numbers, ascending-nearest.

    Each note is placed in the octave that puts it within a tritone of its
    predecessor, which is what a reader of a melody line does by eye. Absolute
    octave never matters -- only the intervals are hashed -- but a melody that
    wanders an octave per step would hash wrongly, so nearest-placement it is.
    Explicit octaves (``G4``) and accidentals (``F#``, ``Bb``) are honoured.
    """
    out: list[int] = []
    for tok in spec.split():
        letter = tok[0].upper()
        if letter not in NOTE_TO_PC:
            raise ValueError(f"not a note name: {tok!r}")
        pc = NOTE_TO_PC[letter]
        i = 1
        while i < len(tok) and tok[i] in "#b":
            pc += 1 if tok[i] == "#" else -1
            i += 1
        if i < len(tok):
            out.append(12 * (int(tok[i:]) + 1) + pc)
            continue
        if not out:
            out.append(12 * (octave + 1) + pc)
            continue
        base = 12 * (octave + 1) + pc
        prev = out[-1]
        best = min((base + 12 * k for k in range(-4, 5)),
                   key=lambda p: (abs(p - prev), p))
        out.append(best)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Theme:
    """One searchable incipit."""

    id: str
    composer: str
    work: str
    pitches: tuple[int, ...]
    opus: str | None = None
    number: str | None = None
    year: str | None = None
    durations: tuple[float, ...] | None = None
    source: str = ""
    #: Themes sharing a group are the same piece of music -- two lines of one
    #: score, two sections of one movement, two editions. They confirm each
    #: other; they must never be treated as rival answers. Empty means "group
    #: by the printed label", which is the right default: two entries that would
    #: be read out identically *are* the same answer, whatever the corpus's own
    #: identifiers say about them.
    group: str = ""

    @property
    def key(self) -> str:
        """What counts as one answer. See :attr:`group`.

        Grouping by the label rather than by the corpus's row identity was
        forced by the data: the 27,921-theme corpus holds *The Strenuous Life*
        under several work rows, and with row identity as the key those rows
        competed, each one's evidence cancelling the others', and the correct
        answer was reported at confidence 0.0005. Two answers that read the same
        are one answer.
        """
        if self.group:
            return self.group
        return re.sub(r"[^a-z0-9]+", " ", self.label.lower()).strip()

    @property
    def label(self) -> str:
        """``Composer, Work, Op. N No. M`` -- what the runner prints."""
        bits = [self.composer, self.work]
        tail = " ".join(x for x in (
            f"Op. {self.opus}" if self.opus else "",
            f"No. {self.number}" if self.number else "") if x)
        if tail:
            bits.append(tail)
        return ", ".join(b for b in bits if b)

    @classmethod
    def coerce(cls, obj) -> "Theme":
        """Accept a mapping or any object carrying the same attribute names."""
        if isinstance(obj, Theme):
            return obj
        get = obj.get if isinstance(obj, dict) else (lambda k, d=None: getattr(obj, k, d))
        pitches = tuple(int(p) for p in get("pitches", ()) or ())
        durs = get("durations", None)
        return cls(
            id=str(get("id", "")) or f"anon-{abs(hash(pitches)) % 10**8}",
            composer=str(get("composer", "") or ""),
            work=str(get("work", "") or ""),
            pitches=pitches,
            opus=(str(get("opus")) if get("opus") not in (None, "") else None),
            number=(str(get("number")) if get("number") not in (None, "") else None),
            year=(str(get("year")) if get("year") not in (None, "") else None),
            durations=tuple(float(d) for d in durs) if durs else None,
            source=str(get("source", "") or ""),
            group=str(get("group", "") or ""),
        )


@runtime_checkable
class CorpusLike(Protocol):
    """The only thing the identifier asks of a corpus."""

    def themes(self) -> Iterable[Theme]:  # pragma: no cover - protocol
        ...


@dataclass(slots=True)
class ListCorpus:
    """A corpus that is just a list. Also the adapter for anything iterable."""

    entries: list[Theme] = field(default_factory=list)
    name: str = "list"

    @classmethod
    def of(cls, items: Iterable, name: str = "list") -> "ListCorpus":
        return cls([Theme.coerce(x) for x in items], name)

    def themes(self) -> Iterator[Theme]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __add__(self, other: "ListCorpus") -> "ListCorpus":
        seen = {t.id for t in self.entries}
        merged = list(self.entries)
        merged += [t for t in other.entries if t.id not in seen]
        return ListCorpus(merged, f"{self.name}+{other.name}")


def _t(id: str, composer: str, work: str, spec: str, *, opus=None, number=None,
       year=None, source="") -> Theme:
    return Theme(id=id, composer=composer, work=work, pitches=note_names(spec),
                 opus=opus, number=number, year=year, source=source)


def _d(id: str, composer: str, work: str, pitches: Sequence[int], *,
       opus=None, number=None, source="") -> Theme:
    return Theme(id=id, composer=composer, work=work,
                 pitches=tuple(pitches), opus=opus, number=number, source=source)


_SCORE = "incipit entered by hand from the score"

_DERIVED = ("derived, not read from a score: the melody line this package "
            "extracts from ")

#: The starting corpus. Small, and every entry says where it came from.
STUB = ListCorpus([
    # -- the two pieces the identifier is required to get right ------------
    _t("joplin-strenuous-life", "Scott Joplin", "The Strenuous Life",
       "G4 G4 F4 E4 D4 C4 B3 C4 D4 G3", year="1902",
       source="opening melody as measured from 2026-08-17-piece.fp30 "
              "(octave-doubled in the take; one octave kept here)"),

    _d("chopin-nocturne-op62-1", "Chopin", "Nocturne in B major",
       (64, 66, 73, 71, 70, 68, 66, 63, 75, 73, 71, 71, 70, 70, 68, 66, 63),
       opus="62", number="1",
       source=_DERIVED + "2026-08-17-session.fp30 at 24:31, first statement "
                         "of the theme. NOT independent of that take: it will "
                         "recognise that performance and is untested against "
                         "any other."),

    # -- negative controls: real music, honestly unlabelled ----------------
    # The other three pieces in the 44-minute session take. They are here to
    # make the vote a real vote -- a matcher that cannot tell the Chopin from
    # a piece played twenty minutes earlier is not a matcher.
    _d("session-2026-08-17-seg0", "", "Unidentified (session take, from 0:00)",
       (67, 66, 65, 70, 69, 70, 72, 71, 70, 74, 73, 74, 77, 73, 74, 69, 70,
        67, 66, 65, 77, 65),
       source=_DERIVED + "2026-08-17-session.fp30, first segment"),
    _d("session-2026-08-17-seg1", "", "Unidentified (session take, from 7:25)",
       (53, 58, 74, 74, 65, 62, 70, 74, 77, 82, 86, 89, 91, 89, 87, 84, 79,
        77, 79, 82, 86, 89),
       source=_DERIVED + "2026-08-17-session.fp30, second segment"),
    _d("session-2026-08-17-seg2", "", "Unidentified (session take, from 10:14)",
       (69, 77, 68, 69, 74, 67, 65, 70, 74, 69, 71, 69, 68, 69, 72, 71, 69,
        68, 69, 72, 76, 74),
       source=_DERIVED + "2026-08-17-session.fp30, third segment"),
    _d("session-2026-08-17-seg4", "", "Unidentified (session take, from 41:40, "
       "A-flat major)",
       (63, 65, 67, 68, 70, 72, 77, 75, 61, 67, 60, 58, 73, 60, 58, 57, 58,
        72, 68, 56, 51, 51),
       source=_DERIVED + "2026-08-17-session.fp30, fourth segment"),

    # -- common-practice incipits, entered from the score ------------------
    _t("beethoven-fur-elise", "Beethoven", "Fur Elise",
       "E5 D#5 E5 D#5 E5 B4 D5 C5 A4 C4 E4 A4 B4 E4 G#4 B4 C5",
       opus="WoO 59", source=_SCORE),
    _t("beethoven-ode-to-joy", "Beethoven", "Ode to Joy (Symphony No. 9)",
       "E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4",
       opus="125", source=_SCORE),
    _t("bach-invention-1", "J. S. Bach", "Invention No. 1 in C major",
       "C4 D4 E4 F4 D4 E4 C4 G4 C5 B4 C5 D5 C5 B4 C5",
       opus="BWV 772", source=_SCORE),
    _t("bach-prelude-c", "J. S. Bach", "Prelude No. 1 in C major (WTC I)",
       "C4 E4 G4 C5 E5 G4 C5 E5 C4 E4 G4 C5 E5 G4 C5 E5",
       opus="BWV 846", source=_SCORE),
    _t("mozart-k545", "Mozart", "Piano Sonata in C major, K. 545, I",
       "C5 E5 G5 B4 C5 D5 C5", opus="K. 545", source=_SCORE),
    _t("pachelbel-canon", "Pachelbel", "Canon in D (ground bass)",
       "D3 A2 B2 F#2 G2 D2 G2 A2", source=_SCORE),
    _t("brahms-lullaby", "Brahms", "Wiegenlied (Lullaby)",
       "G4 G4 Bb4 G4 G4 Bb4 G4 Bb4 Eb5 D5 C5 C5 Bb4",
       opus="49", number="4", source=_SCORE),
    _t("grieg-morning", "Grieg", "Morning Mood (Peer Gynt)",
       "E5 D5 B4 A4 B4 D5 B4 D5 E5 D5 B4 A4 B4 D5 B4",
       opus="46", number="1", source=_SCORE),
    _t("beethoven-moonlight", "Beethoven", "Piano Sonata No. 14, I (Moonlight)",
       "G#3 C#4 E4 G#3 C#4 E4 G#3 C#4 E4 A3 C#4 E4",
       opus="27", number="2", source=_SCORE),
])


def _opus(raw: str | None) -> str | None:
    """``op. 58`` -> ``58``; the label puts the ``Op.`` back on itself."""
    if not raw:
        return None
    return re.sub(r"^(op\.?|opus)\s*", "", raw.strip(), flags=re.I) or None


def _from_idcorpus_db(db_path) -> ListCorpus:
    """Read :mod:`fp30x_studio.idcorpus`'s table straight into :class:`Theme`.

    Deliberately a read of the table rather than a call into that package's own
    search: the two workstreams share the *data* and nothing else. Its index is
    numpy and whole-query; this one is incremental and integer. Sharing the
    schema and not the algorithm is what let both be built at once.
    """
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT t.id AS tid, t.pitches AS pitches, t.line AS line, "
        "       t.kind AS kind, t.start_measure AS bar, t.source_id AS src, "
        "       w.id AS wid, "
        "       w.composer AS composer, w.title AS title, "
        "       w.parent_title AS parent, w.opus AS opus, "
        "       w.catalogue AS catalogue, w.movement AS movement "
        "FROM themes t JOIN works w ON w.id = t.work_id").fetchall()
    out: list[Theme] = []
    for r in rows:
        try:
            pitches = tuple(int(x) for x in r["pitches"].split(",") if x)
        except ValueError:
            continue
        if len(pitches) < 6:
            continue
        work = r["title"] or r["parent"] or ""
        if r["movement"] and r["movement"] not in work:
            work = f"{work}, {r['movement']}" if work else r["movement"]
        out.append(Theme(
            id=f"idc-{r['tid']}", composer=r["composer"] or "", work=work,
            pitches=pitches, opus=_opus(r["opus"] or r["catalogue"]),
            source=f"idcorpus/{r['src']} {r['line']} {r['kind']}"
                   + (f" bar {r['bar']}" if r["bar"] else "")))
    con.close()
    return ListCorpus(out, "idcorpus")


def load_corpus(*, stub: bool = True, path=None) -> ListCorpus:
    """The corpus to search: the real one if it has landed, plus the stub.

    ``fp30x_studio.idcorpus`` and its database are owned by another workstream.
    Both are reached defensively -- absent, half-built, or raising, the
    identifier still runs on the stub rather than failing at the piano, which is
    the only failure mode that actually costs anything.
    """
    global LAST_CORPUS_ERROR
    LAST_CORPUS_ERROR = ""
    real: ListCorpus | None = None
    try:  # pragma: no cover - depends on a package that may not exist yet
        if path is None:
            from ..idcorpus.sources import DB_PATH  # type: ignore

            path = DB_PATH
        from pathlib import Path as _P

        if _P(path).exists():
            real = _from_idcorpus_db(path)
        else:
            LAST_CORPUS_ERROR = f"{path} does not exist"
    except Exception as exc:
        LAST_CORPUS_ERROR = f"{type(exc).__name__}: {exc}"
        real = None

    if real is not None and not len(real):
        LAST_CORPUS_ERROR = f"{path} held no usable themes"
    if real and len(real):
        return (real + STUB) if stub else real
    return STUB
