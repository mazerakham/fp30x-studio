"""Parse symbolic scores into normalized work metadata + monophonic themes.

Two jobs live here:

1. **Metadata normalization.**  Every corpus states composer / title / opus
   differently (Humdrum reference records, MusicXML metadata, LilyPond headers).
   Jake's requirement is that an identification can name "opus and number", so
   opus and catalogue number are pulled out into their own fields rather than
   left buried in a title string.

2. **Theme extraction.**  A polyphonic score is reduced to a *skyline* -- the
   highest pitch sounding at each distinct onset -- which for solo piano is a
   good stand-in for the melody.  Themes are then fixed-length windows of that
   reduction anchored at musically plausible starting points (bar lines a fixed
   number of measures apart), deduplicated by interval signature.

   These are algorithmically-derived incipits, NOT editorial themes in the
   Barlow & Morgenstern sense.  See PROVENANCE.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- parameters

THEME_LEN = 32          # notes per theme window
MIN_THEME_LEN = 8       # shorter than this and a window is not worth indexing
THEME_BAR_STRIDE = 8    # candidate theme every N measures
MAX_THEMES = 12         # per work
MIN_WORK_NOTES = 6


# ------------------------------------------------------------------ metadata


@dataclass
class WorkMeta:
    composer: str = ""
    title: str = ""
    parent_title: str = ""
    opus: str = ""
    catalogue: str = ""
    movement: str = ""
    movement_number: str = ""
    key: str = ""
    genre: str = ""
    date: str = ""
    licence: str = ""
    commercial_ok: bool | None = None

    def display(self) -> str:
        bits = [b for b in (self.parent_title, self.title) if b]
        # avoid "12 Etudes: 12 Etudes"
        if len(bits) == 2 and bits[0] == bits[1]:
            bits = bits[:1]
        head = ": ".join(bits) or "(untitled)"
        low = _digits_only(head)
        tail = ", ".join(b for b in (self.opus, self.catalogue)
                         if b and _digits_only(b) not in low)
        if tail:
            head = f"{head}, {tail}"
        if self.movement and self.movement.lower() not in head.lower():
            head = f"{head} - {self.movement}"
        return head


def _digits_only(s: str) -> str:
    """'Op. 28, No. 1' -> 'op28no1' -- for cheap substring comparisons."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_SUP = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS.sub(" ", _SUP.sub("", s or "")).strip().strip(".,;: ")


def norm_opus(ops: str, onm: str = "") -> str:
    """'2' + '1' -> 'Op. 2 No. 1';  'Op. 62' + 'No. 1' -> 'Op. 62 No. 1'."""
    ops, onm = _clean(ops), _clean(onm)
    out = ""
    if ops:
        out = ops if re.match(r"(?i)^(op|posth|woo|wo\b)", ops) else f"Op. {ops}"
        out = re.sub(r"(?i)^op\.?\s*", "Op. ", out)
    if onm:
        n = onm if re.match(r"(?i)^(no|nr)", onm) else f"No. {onm}"
        n = re.sub(r"(?i)^(no|nr)\.?\s*", "No. ", n)
        out = f"{out} {n}".strip()
    return out


_CAT_RE = re.compile(
    r"(?i)\b("
    r"BWV\s*\d+[a-z]?|"
    r"K(?:V)?\.?\s*\d+[a-z]?|"
    r"Hob\.?\s*[IVXL]+\s*[:/]\s*\d+[a-z]?|"
    r"D\.?\s*\d+[a-z]?|"
    r"L\.?\s*\d+[a-z]?|"
    r"WoO\s*\d+|"
    r"S\.?\s*\d+|"
    r"Sz\.?\s*\d+|"
    r"BB\s*\d+|"
    r"H\.?\s*\d+"
    r")\b"
)
_OPUS_RE = re.compile(r"(?i)\bop(?:us|\.)?\s*(\d+[a-z]?)(?:\s*,?\s*(?:no|nr)\.?\s*(\d+[a-z]?))?")


def opus_from_text(text: str) -> str:
    m = _OPUS_RE.search(text or "")
    if not m:
        return ""
    return norm_opus(m.group(1), m.group(2) or "")


def catalogue_from_text(text: str) -> str:
    m = _CAT_RE.search(text or "")
    return _clean(m.group(1)) if m else ""


# ------------------------------------------------------------ humdrum **kern

_REF = re.compile(r"^!!!([^:@]+)(?:@+[A-Za-z]*)?:\s*(.*)$")


def read_kern_records(path: Path) -> dict[str, list[str]]:
    recs: dict[str, list[str]] = {}
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line.startswith("!!!"):
                if line.startswith("**") or line.startswith("*-"):
                    continue
                continue
            m = _REF.match(line.rstrip("\n"))
            if m:
                recs.setdefault(m.group(1).strip(), []).append(m.group(2).strip())
    return recs


_MAZURKA = re.compile(r"mazurka(\d{2})-(\d)")
_SONATA = re.compile(r"sonata(\d+)-(\d+)")
_PRELUDE = re.compile(r"prelude(\d+)-(\d+)")


def meta_from_kern(path: Path, source) -> WorkMeta:
    r = read_kern_records(path)

    def one(*keys: str) -> str:
        for k in keys:
            for kk, vs in r.items():
                if kk.upper() == k.upper() and vs:
                    return _clean(vs[0])
        return ""

    m = WorkMeta()
    m.composer = one("COM", "COA", "COC") or source.composer_hint
    m.title = one("rism-title", "OTL") or ""
    m.parent_title = one("OPR")
    if not m.parent_title:
        ptl = one("PTL")
        surname = (m.composer.split(",")[0] or "").strip()
        if ptl and len(ptl) < 50 and (not surname or surname.lower() not in ptl.lower()):
            m.parent_title = ptl
    if m.title and m.parent_title and m.parent_title.lower() in m.title.lower():
        m.parent_title = ""
    m.opus = norm_opus(one("OPS"), one("ONM"))
    if not m.opus:
        m.opus = opus_from_text(one("rism-opus")) or opus_from_text(m.title) or opus_from_text(m.parent_title)
    elif "No." not in m.opus:
        # NIFC records carry the opus in !!!OPS but the number only inside
        # !!!rism-opus / the title ("op. 62, no. 1"). Jake asked for opus AND
        # number, so recover it rather than citing a half-identified work.
        for src_text in (one("rism-opus"), m.title, m.parent_title):
            full = opus_from_text(src_text)
            if full and "No." in full and full.split()[1] == m.opus.split()[1]:
                m.opus = full
                break
    m.catalogue = one("SCT", "SCT1", "SCA") or catalogue_from_text(m.title)
    m.catalogue = _clean(m.catalogue)
    m.catalogue = re.sub(r"(?i)^K\s*[16]\s+(\d)", r"K. \1", m.catalogue)
    m.catalogue = re.sub(r"(?i)^([A-Z]+)\.?\s*(\d)", lambda g: f"{g.group(1)}. {g.group(2)}"
                         if g.group(1).upper() not in ("HOB", "BWV", "WOO") else g.group(0), m.catalogue)
    m.movement = one("OMD")
    m.movement_number = one("OMV")
    m.key = one("rism-key")
    m.genre = one("AGN", "rism-genre")
    m.date = one("ODT", "PDT")

    stem = path.stem
    if not m.opus:
        mm = _MAZURKA.search(stem)
        if mm:
            m.opus = norm_opus(mm.group(1).lstrip("0"), mm.group(2))
        else:
            mm = _PRELUDE.search(stem)
            if mm:
                m.opus = norm_opus(mm.group(1).lstrip("0"), mm.group(2))
    if not m.movement_number:
        mm = _SONATA.search(stem)
        if mm:
            m.movement_number = mm.group(2)
    if not m.title:
        m.title = stem
    return m


# ------------------------------------------------------------ music21 / MusicXML


def meta_from_music21(score, path: Path, source) -> WorkMeta:
    md = score.metadata
    m = WorkMeta()
    allmd = {}
    try:
        for k, v in md.all():
            allmd.setdefault(k, str(v))
    except Exception:
        pass
    m.composer = _clean(str(md.composer or "")) or source.composer_hint
    m.title = _clean(str(md.title or ""))
    m.movement = _clean(str(md.movementName or ""))
    m.movement_number = _clean(str(md.movementNumber or ""))
    number = allmd.get("number", "") or allmd.get("opusNumber", "")

    # OpenScore Lieder path: scores/<Composer>/<Collection|_>/<Song>/<id>.mxl
    parts = path.parts
    coll = song = ""
    if "scores" in parts:
        i = parts.index("scores")
        seg = parts[i + 1:]
        if len(seg) >= 2:
            if not m.composer:
                m.composer = seg[0].replace("_", " ")
            # Lieder: <Composer>/<Collection|_>/<Song>/<id>.mxl
            # Quartets: <Composer>/<Work>/<id>.mxl
            if len(seg) >= 4:
                coll = seg[1].replace("_", " ").strip()
                song = seg[2].replace("_", " ").strip()
            else:
                coll = ""
                song = seg[1].replace("_", " ").strip()
            song = re.sub(r"^\d+[_\s]+", "", song)
            if coll in ("", "_"):
                coll = ""
    # md.title is the collection/opus heading; md.movementName is the song.
    heading = m.title
    song_title = m.movement or song
    # MuseScore exports sometimes put the filename in movementName
    if song_title and (song_title == path.name or song_title == path.stem
                       or song_title.lower().endswith((".mxl", ".xml", ".mscz", ".musicxml"))):
        song_title = song
    if heading and (heading == path.name or heading == path.stem
                    or heading.lower().endswith((".mxl", ".xml", ".mscz", ".musicxml"))):
        heading = coll
    if song_title and heading and _digits_only(song_title) != _digits_only(heading):
        m.parent_title, m.title = heading, song_title
    else:
        m.title = heading or song_title or coll or path.stem
        m.parent_title = coll if _digits_only(coll) != _digits_only(m.title) else ""
    m.movement = ""
    if not m.title:
        m.title = path.stem

    m.opus = opus_from_text(m.title) or opus_from_text(m.parent_title)
    if not m.opus and number and str(number).isdigit():
        m.opus = norm_opus(str(number))
    m.catalogue = catalogue_from_text(m.title) or catalogue_from_text(m.parent_title)
    m.licence = _clean(allmd.get("copyright", ""))
    return m


# ------------------------------------------------------------------- skyline


def top_part(score):
    """The part with the highest median pitch -- the right hand / soprano line.

    Returns None when the score has fewer than two usable parts.
    """
    try:
        parts = list(score.parts)
    except Exception:
        return None
    if len(parts) < 2:
        return None
    best, best_med = None, -1.0
    for pt in parts:
        ps = []
        for n in pt.recurse().notes:
            try:
                ps.append(max(p.midi for p in n.pitches))
            except Exception:
                pass
        if len(ps) < 8:
            continue
        ps.sort()
        med = ps[len(ps) // 2]
        if med > best_med:
            best, best_med = pt, med
    return best


def skyline(score) -> tuple[list[int], list[float], list[int]]:
    """Highest pitch per distinct onset, in score order.

    Returns (midi_pitches, offsets_in_quarters, measure_numbers).
    """
    best: dict[float, tuple[int, int]] = {}     # offset -> (midi, measure)
    try:
        notes = list(score.recurse().notes)
    except Exception:
        return [], [], []
    for n in notes:
        try:
            off = float(n.getOffsetInHierarchy(score))
        except Exception:
            try:
                off = float(n.offset)
            except Exception:
                continue
        try:
            mid = max(p.midi for p in n.pitches)
        except Exception:
            continue
        if not (0 <= mid <= 127):
            continue
        key = round(off, 4)
        mn = getattr(n, "measureNumber", None)
        prev = best.get(key)
        if prev is None or mid > prev[0]:
            best[key] = (mid, mn if isinstance(mn, int) else -1)
    if not best:
        return [], [], []
    offs = sorted(best)
    return [best[o][0] for o in offs], [float(o) for o in offs], [best[o][1] for o in offs]


# -------------------------------------------------------------------- themes


@dataclass
class Theme:
    kind: str                 # "incipit" | "section"
    start_index: int
    start_measure: int
    pitches: list[int]
    onsets: list[float]

    @property
    def durations(self) -> list[float]:
        o = self.onsets
        return [round(o[i + 1] - o[i], 4) for i in range(len(o) - 1)] + [0.0]

    def intervals(self) -> tuple[int, ...]:
        p = self.pitches
        return tuple(p[i + 1] - p[i] for i in range(len(p) - 1))


def make_themes(pitches, onsets, measures) -> list[Theme]:
    n = len(pitches)
    if n < MIN_WORK_NOTES:
        return []

    starts: list[int] = [0]
    seen_bars: set[int] = set()
    for i, mb in enumerate(measures):
        if mb is None or mb < 0:
            continue
        if mb % THEME_BAR_STRIDE == 1 and mb not in seen_bars:
            seen_bars.add(mb)
            if i:
                starts.append(i)
    if len(starts) == 1 and n > THEME_LEN * 2:
        # no usable measure numbers (typical for raw MIDI): fall back to a
        # regular stride through the note stream
        step = max(THEME_LEN, n // MAX_THEMES)
        starts = list(range(0, n - MIN_THEME_LEN, step))

    starts = sorted({s for s in starts if n - s >= MIN_THEME_LEN})
    if len(starts) > MAX_THEMES:
        keep = [starts[round(i * (len(starts) - 1) / (MAX_THEMES - 1))] for i in range(MAX_THEMES)]
        starts = sorted(set(keep))

    out: list[Theme] = []
    seen_sig: set[tuple[int, ...]] = set()
    for s in starts:
        end = min(s + THEME_LEN, n)
        if end - s < MIN_THEME_LEN:
            continue
        base = onsets[s]
        t = Theme(
            kind="incipit" if s == 0 else "section",
            start_index=s,
            start_measure=measures[s] if measures[s] is not None else -1,
            pitches=list(pitches[s:end]),
            onsets=[round(o - base, 4) for o in onsets[s:end]],
        )
        sig = t.intervals()
        if sig in seen_sig or len(set(t.pitches)) < 3:
            continue
        seen_sig.add(sig)
        out.append(t)
    return out


# ------------------------------------------------------- composer name keys

import unicodedata

_NAME_SUFFIX = {"jr", "sr", "ii", "iii", "the", "von", "van", "de", "da", "di", "le", "la"}
_PAREN = re.compile(r"\s*\([^)]*\)")          # life dates: "Franz Abt (1819-1885)"

# Canonical display forms for the repertoire that matters most here, keyed by
# the folded surname.  Anything not listed falls back to the observed spelling.
CANONICAL = {
    "chopin": "Fryderyk Chopin",
    "joplin": "Scott Joplin",
    "beethoven": "Ludwig van Beethoven",
    "mozart": "Wolfgang Amadeus Mozart",
    "bach": "Johann Sebastian Bach",
    "haydn": "Joseph Haydn",
    "scarlatti": "Domenico Scarlatti",
    "schubert": "Franz Schubert",
    "schumann": "Robert Schumann",
    "brahms": "Johannes Brahms",
    "debussy": "Claude Debussy",
    "satie": "Erik Satie",
    "scriabin": "Alexander Scriabin",
    "skryabin": "Alexander Scriabin",
    "liszt": "Franz Liszt",
    "hummel": "Johann Nepomuk Hummel",
    "mendelssohn": "Felix Mendelssohn",
    "grieg": "Edvard Grieg",
    "rachmaninoff": "Sergei Rachmaninoff",
    "rachmaninov": "Sergei Rachmaninoff",
    "tchaikovsky": "Pyotr Ilyich Tchaikovsky",
    "ravel": "Maurice Ravel",
    "faure": "Gabriel Faure",
    "palestrina": "Giovanni Pierluigi da Palestrina",
    "monteverdi": "Claudio Monteverdi",
    "josquin": "Josquin des Prez",
    "prez": "Josquin des Prez",
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def composer_key(raw: str) -> str:
    """Fold a composer string to a stable surname key ('Chopin, F.' -> 'chopin').

    Both "Last, First" and "First Last" occur across these corpora, frequently
    for the same person, so the key is the surname alone: the part before the
    comma when there is one, otherwise the last token that is not a nobiliary
    particle ("Ludwig van Beethoven" -> "beethoven").
    """
    s = _PAREN.sub("", _clean(raw))
    if not s:
        return ""
    surname_first = "," in s
    if surname_first:
        s = s.split(",")[0]
    toks = [t for t in re.split(r"[\s.]+", fold(s))
            if len(t) > 1 and t not in _NAME_SUFFIX]
    if not toks:
        return ""
    return re.sub(r"[^a-z]", "", toks[0] if surname_first else toks[-1])


def canonical_composer(raw: str) -> tuple[str, str]:
    """-> (display name, surname key)."""
    raw = _PAREN.sub("", raw or "").strip()
    key = composer_key(raw)
    if key in CANONICAL:
        return CANONICAL[key], key
    s = _clean(raw)
    if "," in s:
        last, _, first = s.partition(",")
        s = f"{first.strip()} {last.strip()}".strip()
    return s, key
