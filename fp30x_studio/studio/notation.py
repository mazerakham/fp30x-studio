"""Engraved notation for a measure range, so the player can find the place on the page.

The studio asks for bar 137. That instruction is useless without the music: the
player needs to see the bar, in the key, with its clefs, to know where to put
their hands and which page to turn to.

Rendering is **not** done in the browser. Verovio has a JavaScript build, but a
page that depends on it stops working the day the vendored copy goes missing,
and this has to open in a year. So every bar is engraved once, ahead of time,
into a plain ``.svg`` on disk, and the page is an ``<img>``. The cache is
derived and deletable: throw it away and it rebuilds.

The path is kern -> music21 -> MusicXML -> Verovio. music21 does the measure
extraction because it carries the clef, key signature and meter into the
excerpt; Verovio's own ``myank`` filter refused the file with "no measure
numbers present", and hand-slicing Humdrum spines would have meant
reimplementing exactly that.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = ["Engraver"]

OPTIONS = {
    "pageWidth": 1700,
    "scale": 36,
    "adjustPageHeight": True,
    "footer": "none",
    "header": "none",
    "breaks": "none",
    "spacingStaff": 3,
}


class Engraver:
    """Engraves measure ranges of one score, caching to disk."""

    def __init__(self, kern: str | Path, cache: str | Path):
        self.kern = Path(kern).expanduser()
        self.cache = Path(cache).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        self._score = None
        self._tk = None

    # Loading either library costs a second and neither is needed when every
    # bar asked for is already on disk, which is the normal case.
    @property
    def score(self):
        if self._score is None:
            from music21 import converter
            self._score = converter.parse(str(self.kern))
        return self._score

    @property
    def toolkit(self):
        if self._tk is None:
            import verovio
            tk = verovio.toolkit()
            tk.setOptions(OPTIONS)
            self._tk = tk
        return self._tk

    def path(self, first: int, last: int) -> Path:
        return self.cache / f"m{first:03d}-{last:03d}.svg"

    def engrave(self, first: int, last: int | None = None) -> Path:
        last = first if last is None else last
        out = self.path(first, last)
        if out.exists() and out.stat().st_size > 0:
            return out
        from music21.musicxml.m21ToXml import GeneralObjectExporter
        excerpt = self.score.measures(first, last)
        xml = GeneralObjectExporter().parse(excerpt).decode("utf-8")
        if not self.toolkit.loadData(xml):
            raise RuntimeError(f"verovio refused bars {first}-{last}")
        out.write_text(self.toolkit.renderToSVG(1))
        return out

    def engrave_all(self, n_measures: int, *, context: int = 1,
                    progress=None) -> int:
        """Every bar, each with ``context`` bars after it for orientation."""
        made = 0
        for m in range(1, n_measures + 1):
            last = min(m + context, n_measures)
            before = self.path(m, last).exists()
            self.engrave(m, last)
            made += 0 if before else 1
            if progress and m % 20 == 0:
                progress(m, n_measures)
        return made
