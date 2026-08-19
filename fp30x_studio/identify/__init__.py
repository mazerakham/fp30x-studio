"""Name the piece being played, from the live stream, without asking a model.

    .fp30 bytes still being appended
        |  pipeline.TakeStore.ingest()     resumes at a byte offset
        v
    new note-ons only
        |  features.Segment                cluster 55 ms, skyline + bass,
        v                                   collapse trills, commit stably
    two index-stable melodic lines
        |  matcher.Votes                    interval n-grams -> inverted index
        v                                   -> alignment histogram
    (theme, diagonal) -> hits
        |  matcher.score                    hits x density x separation
        v
    Verdict, or None

Entry point: ``python -m fp30x_studio.identify watch``.

The three constraints this was built under, all of them from the brief:

**Incremental.** Nothing is re-derived. Every layer above keeps its own cache
and consumes only what is new. See :mod:`.identifier` for the table of caches.

**Cheap.** It runs on a timer beside a piano. A tick with no new music is a
``stat`` call; a tick with three seconds of music is a few hundred dict lookups.
The measured cost is in ``tests/test_identify.py`` and in the Linear issue, not
in an adjective.

**Arithmetic, not inference.** No model is called anywhere in this package. A
model may be handed the shortlist afterwards; it is not in the loop, because a
loop that calls out is a loop that cannot run every three seconds for an hour.

And one rule about output: it says nothing until it is sure. A confident wrong
answer, glanced at from the piano stool, is worse than silence.
"""

from __future__ import annotations

from .corpus import STUB, CorpusLike, ListCorpus, Theme, load_corpus, note_names
from .features import Clusterer, Event, Line, Segment, collapse_trills
from .identifier import Identifier, Stats, Verdict, replay
from .matcher import (MIN_CONFIDENCE, MIN_DENSITY, MIN_HITS, NGRAM, Candidate,
                      ThemeIndex, Votes, best_candidate, score)

__all__ = [
    "Identifier", "Verdict", "Stats", "replay",
    "Theme", "CorpusLike", "ListCorpus", "STUB", "load_corpus", "note_names",
    "ThemeIndex", "Votes", "Candidate", "score", "best_candidate",
    "NGRAM", "MIN_HITS", "MIN_DENSITY", "MIN_CONFIDENCE",
    "Segment", "Line", "Clusterer", "Event", "collapse_trills",
]
