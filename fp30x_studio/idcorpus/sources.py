"""Manifest of the symbolic-music corpora that feed the theme index.

Every entry records the licence *verbatim enough to act on*.  ``commercial_ok``
is the only field the builder is allowed to trust when filtering: it is set
False for anything NonCommercial, NoDerivatives, or simply unclear.  When in
doubt it is False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Corpus root on disk.  Overridable via FP30X_CORPUS_DIR.
import os

CORPUS_DIR = Path(os.environ.get("FP30X_CORPUS_DIR", Path.home() / "workspace/audio/corpus"))
RAW_DIR = CORPUS_DIR / "raw"
RISM_JSONL = RAW_DIR / "rism-incipits.jsonl"   # .gz accepted too
# Two databases, one schema.
#   themes.sqlite      - curated score corpora; the piece-identification corpus.
#   themes-rism.sqlite - 2M RISM incipits; a breadth reference, built separately
#                        so that consumers which load every row into memory are
#                        not forced to swallow it.
DB_PATH = CORPUS_DIR / "themes.sqlite"
RISM_DB_PATH = CORPUS_DIR / "themes-rism.sqlite"


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    subdir: str                 # relative to RAW_DIR
    globs: tuple[str, ...]
    fmt: str                    # kern | musicxml | midi | abc | music21
    licence: str                # human-readable, as stated by the source
    spdx: str                   # best-effort SPDX id, or "unclear"
    commercial_ok: bool
    url: str
    attribution: str = ""
    notes: str = ""
    composer_hint: str = ""
    catalogue_hint: str = ""    # e.g. "K." for Scarlatti, "Hob." for Haydn

    @property
    def root(self) -> Path:
        return RAW_DIR / self.subdir

    def files(self) -> list[Path]:
        out: list[Path] = []
        for g in self.globs:
            out.extend(sorted(self.root.glob(g)))
        return out


CC_BY = "CC BY 4.0 (attribution; commercial use permitted)"
CC_BY_NC_SA = "CC BY-NC-SA 4.0 (attribution, NONCOMMERCIAL, share-alike)"
CC0 = "CC0 1.0 (public-domain dedication; no restrictions)"
CC_BY_3 = "CC BY 3.0 Unported (attribution required; commercial use permitted)"
NO_LICENCE = (
    "NO LICENCE STATED in the repository - verified 2026-08-19, no LICENSE file, no "
    "README licence statement, no !!!YEM record. The composition is public domain but "
    "the encoding is not licensed, so default all-rights-reserved applies."
)

SOURCES: tuple[Source, ...] = (
    # ---------------------------------------------------------------- CC BY
    Source(
        id="nifc-chopin",
        name="Chopin First Editions (Fryderyk Chopin Institute / NIFC)",
        subdir="humdrum-chopin-first-editions",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=CC_BY,
        spdx="CC-BY-4.0",
        commercial_ok=True,
        url="https://github.com/pl-wnifc/humdrum-chopin-first-editions",
        attribution=(
            "First Editions of Fryderyk Chopin's Music. Copyright 2017-2021 "
            "The Fryderyk Chopin Institute (https://nifc.pl). https://chopinscores.org"
        ),
        composer_hint="Fryderyk Chopin",
        notes=(
            "Multiple first-edition witnesses per work (BH = Breitkopf & Haertel, "
            "BR = Brandus, etc.), so several files can encode the same music. "
            "RISM ids and rism-title/rism-opus records give clean opus metadata."
        ),
    ),
    # ------------------------------------------------------------------ CC0
    Source(
        id="openscore-lieder",
        name="OpenScore Lieder Corpus",
        subdir="Lieder",
        globs=("scores/*/*/*/*.mxl",),
        fmt="musicxml",
        licence=CC0,
        spdx="CC0-1.0",
        commercial_ok=True,
        url="https://github.com/OpenScore/Lieder",
        attribution="OpenScore Lieder Corpus (CC0), fourscoreandmore.org/openscore/lieder/",
        notes=(
            "19th-century song: Schubert, Schumann, Brahms, Wolf, Mendelssohn, "
            "Faure and many under-recorded women composers. Voice + piano, so the "
            "skyline reduction is usually the vocal line, not the piano part."
        ),
    ),
    # ------------------------------------------- Mutopia (per-piece licences)
    Source(
        id="mutopia",
        name="Mutopia Project",
        subdir="mutopia-midi",
        globs=("*.mid",),
        fmt="midi",
        licence="per-piece: Public Domain / CC0 / CC BY-SA 4.0 (see licence column per record)",
        spdx="mixed",
        commercial_ok=True,   # per-record override applied in build.py
        url="https://www.mutopiaproject.org/",
        attribution="Mutopia Project (www.MutopiaProject.org)",
        notes=(
            "MIDI rendered by Mutopia from the LilyPond sources. Licence is read "
            "from the mutopia header of the matching .ly in MutopiaProject/MutopiaProject. "
            "CC BY-SA is share-alike: usable commercially, but a derived index that "
            "redistributes the note data may inherit the share-alike obligation. "
            "Records carry their own licence string; do not rely on the source-level value."
        ),
    ),
    # ----------------------------------------------- CC BY-NC-SA (craigsapp)
    Source(
        id="joplin",
        name="Scott Joplin, complete piano works (Humdrum, C. Sapp)",
        subdir="joplin",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=CC_BY_NC_SA,
        spdx="CC-BY-NC-SA-4.0",
        commercial_ok=False,
        url="https://github.com/craigsapp/joplin",
        attribution="Copyright (C) 2004-2021 Craig Stuart Sapp",
        composer_hint="Scott Joplin",
        notes="47 rags/waltzes. 'The Strenuous Life' (1902) is NOT encoded here - PDF only.",
    ),
    Source(
        id="beethoven-sonatas",
        name="Beethoven piano sonatas (Humdrum, C. Sapp)",
        subdir="beethoven-piano-sonatas",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=NO_LICENCE,
        spdx="none-stated",
        commercial_ok=False,
        url="https://github.com/craigsapp/beethoven-piano-sonatas",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Ludwig van Beethoven",
        notes="All 32 sonatas, 103 movements. Verified 2026-08-19: no LICENSE file anywhere in the repo.",
    ),
    Source(
        id="mozart-sonatas",
        name="Mozart piano sonatas, Alte Mozart-Ausgabe (Humdrum, C. Sapp)",
        subdir="mozart-piano-sonatas",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=CC_BY_NC_SA,
        spdx="CC-BY-NC-SA-4.0",
        commercial_ok=False,
        url="https://github.com/craigsapp/mozart-piano-sonatas",
        attribution="Copyright (C) 2004-2018 Craig Stuart Sapp",
        composer_hint="Wolfgang Amadeus Mozart",
        catalogue_hint="K.",
    ),
    Source(
        id="haydn-sonatas",
        name="Haydn piano sonatas (Humdrum, C. Sapp)",
        subdir="haydn-piano-sonatas",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=CC_BY_NC_SA,
        spdx="CC-BY-NC-SA-4.0",
        commercial_ok=False,
        url="https://github.com/craigsapp/haydn-piano-sonatas",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Joseph Haydn",
        catalogue_hint="Hob.",
    ),
    Source(
        id="scarlatti-sonatas",
        name="Scarlatti keyboard sonatas, Longo edition (Humdrum, C. Sapp)",
        subdir="scarlatti-keyboard-sonatas",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=CC_BY_NC_SA,
        spdx="CC-BY-NC-SA-4.0",
        commercial_ok=False,
        url="https://github.com/craigsapp/scarlatti-keyboard-sonatas",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Domenico Scarlatti",
        catalogue_hint="K.",
        notes="65 of 555 sonatas encoded; the rest of the repo is reference PDFs (not downloaded).",
    ),
    Source(
        id="chopin-mazurkas",
        name="Chopin mazurkas, Mikuli/Schirmer (Humdrum, C. Sapp)",
        subdir="chopin-mazurkas",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=NO_LICENCE,
        spdx="none-stated",
        commercial_ok=False,
        url="https://github.com/craigsapp/chopin-mazurkas",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Fryderyk Chopin",
        notes="Opus/number come from the filename (mazurkaNN-M.krn), not the reference records.",
    ),
    Source(
        id="chopin-preludes",
        name="Chopin preludes Op. 28 (Humdrum, C. Sapp)",
        subdir="chopin-preludes",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=NO_LICENCE,
        spdx="none-stated",
        commercial_ok=False,
        url="https://github.com/craigsapp/chopin-preludes",
        attribution="Copyright 2008 Craig Stuart Sapp",
        composer_hint="Fryderyk Chopin",
    ),
    Source(
        id="scriabin-mysterium",
        name="Mysterium corpus: Scriabin complete solo piano with opus (B. J. Bell)",
        subdir="scriabin",
        globs=("op*/*.krn", "ccarh_kern/*.krn"),
        fmt="kern",
        licence=NO_LICENCE,
        spdx="none-stated",
        commercial_ok=False,
        url="https://github.com/craigsapp/scriabin",
        attribution="Bryan Jacob Bell, Mysterium corpus (thesis, 2022)",
        composer_hint="Alexander Scriabin",
        notes="NO LICENCE STATED. Treated as not-for-commercial-use until clarified with the author.",
    ),
    Source(
        id="bach-chorales",
        name="J.S. Bach 370 four-part chorales (Humdrum, C. Sapp)",
        subdir="bach-370-chorales",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=CC_BY_NC_SA,
        spdx="CC-BY-NC-SA-4.0",
        commercial_ok=False,
        url="https://github.com/craigsapp/bach-370-chorales",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Johann Sebastian Bach",
        catalogue_hint="BWV",
    ),
    Source(
        id="bach-musical-offering",
        name="J.S. Bach, Musical Offering BWV 1079 (Humdrum, C. Sapp)",
        subdir="bach-musical-offering",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=NO_LICENCE,
        spdx="none-stated",
        commercial_ok=False,
        url="https://github.com/craigsapp/bach-musical-offering",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Johann Sebastian Bach",
        catalogue_hint="BWV",
    ),
    Source(
        id="hummel-preludes",
        name="Hummel, 24 Preludes Op. 67 (Humdrum, C. Sapp)",
        subdir="hummel-preludes",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=NO_LICENCE,
        spdx="none-stated",
        commercial_ok=False,
        url="https://github.com/craigsapp/hummel-preludes",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Johann Nepomuk Hummel",
    ),
    Source(
        id="beethoven-quartets",
        name="Beethoven string quartets (Humdrum, C. Sapp)",
        subdir="beethoven-string-quartets",
        globs=("kern/*.krn",),
        fmt="kern",
        licence=NO_LICENCE,
        spdx="none-stated",
        commercial_ok=False,
        url="https://github.com/craigsapp/beethoven-string-quartets",
        attribution="Copyright Craig Stuart Sapp",
        composer_hint="Ludwig van Beethoven",
    ),
    Source(
        id="openscore-quartets",
        name="OpenScore String Quartet Corpus",
        subdir="StringQuartets",
        globs=("scores/*/*/*/*.mxl", "scores/*/*/*.mxl"),
        fmt="musicxml",
        licence=CC0,
        spdx="CC0-1.0",
        commercial_ok=True,
        url="https://github.com/OpenScore/StringQuartets",
        attribution="OpenScore String Quartet Corpus (CC0), fourscoreandmore.org/openscore/stringquartets/",
        notes="122 movements, 47 composers. Zenodo DOI 10.5281/zenodo.21862554.",
    ),
    Source(
        id="dcml",
        name="DCML annotated corpora (EPFL Digital and Cognitive Musicology Lab)",
        subdir="dcml",
        globs=("*/notes/*.notes.tsv",),
        fmt="dcml",
        licence=CC_BY_NC_SA,
        spdx="CC-BY-NC-SA-4.0",
        commercial_ok=False,
        url="https://github.com/DCMLab",
        attribution="DCML corpora, EPFL Digital and Cognitive Musicology Lab",
        notes=(
            "Note-event TSVs (quarterbeats + MIDI pitch + staff), so no score parser is "
            "needed. This is the only openly-available symbolic Debussy in the set, and "
            "it adds Ravel, Liszt, Grieg, Rachmaninoff, Medtner, Tchaikovsky, Dvorak, "
            "Bartok and Schumann. Debussy's Preludes/Estampes/Images/Etudes exist in "
            "DCMLab only as MuseScore .mscx with no note TSVs - NOT captured."
        ),
    ),
    Source(
        id="rism",
        name="RISM incipits (Repertoire International des Sources Musicales)",
        subdir="",
        globs=(),
        fmt="rism",
        licence=CC_BY_3,
        spdx="CC-BY-3.0",
        commercial_ok=True,
        url="https://rism.digital/exports/files.json",
        attribution="Repertoire International des Sources Musicales (RISM), https://rism.info - CC BY 3.0",
        notes=(
            "The openly-licensed stand-in for Barlow & Morgenstern, whose 1948 copyright "
            "was renewed (R604688, 1975) and runs to 2043. Incipits are Plaine & Easie "
            "strings in MARC field 031, decoded by fp30x_studio.idcorpus.pae. Coverage "
            "skews to manuscripts and early prints, so 19th-century solo piano is thin, "
            "but breadth and metadata quality are unmatched."
        ),
    ),
    # ------------------------------------------------------- music21 bundled
    Source(
        id="music21-core",
        name="music21 bundled core corpus",
        subdir="",           # resolved specially - lives inside the installed package
        globs=(),
        fmt="music21",
        licence="mixed: music21 code is BSD-3-Clause; corpus files carry per-file terms, largely public domain or CC",
        spdx="mixed",
        commercial_ok=False,   # per-file terms unaudited -> conservative
        url="https://github.com/cuthbertLab/music21",
        attribution="music21, MIT/Cambridge (Michael Scott Asato Cuthbert et al.)",
        notes=(
            "Adds Palestrina, Monteverdi, Josquin, trecento, Essen folksong, Ryan's "
            "Mammoth Collection and the Bach chorales. Per-file corpus terms are not "
            "individually audited here, so commercial_ok is False by default."
        ),
    ),
)

SOURCES_BY_ID = {s.id: s for s in SOURCES}
