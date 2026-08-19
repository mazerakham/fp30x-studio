"""The function and its cache: one tick of identification.

The shape asked for was "a python function that takes as input the input stream
plus whatever cache you want to create of recent data", run on a timer, without
burning the CPU and without an LLM anywhere in the loop. That is
:meth:`Identifier.update`. Everything it needs to not repeat itself lives in the
object:

============================  ==========================================
cache                         who keeps it, and what it costs
============================  ==========================================
byte offset + pairing state   :class:`~fp30x_studio.pipeline.TakeStore`,
                              on disk. A tick re-reads only the bytes
                              appended since the last tick.
message cursor                one integer here. New note-ons only.
onset clusters, committed
line, trill state             :class:`~.features.Segment`, a few hundred
                              notes of tail.
alignment histogram           :class:`~.matcher.Votes`, one entry per
                              ``(theme, diagonal)`` that actually
                              collided.
============================  ==========================================

Nothing in that table is recomputed from the top of the take. The work a tick
does is proportional to the music played since the previous tick, not to the
music played since he sat down, which is what keeps a 44-minute session as cheap
in its 44th minute as in its first.

**No model is consulted.** The identification is an inverted-index lookup and an
integer histogram. A language model may be handed the shortlist afterwards to
say something about the piece; it is not in this path and must not be put in it.

Segmenting: silence longer than :data:`~.features.SEGMENT_GAP_NS` ends a piece.
Each segment gets its own votes, so the Chopin he plays after the Joplin is
identified on its own evidence and cannot inherit the Joplin's.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..pipeline import TakeStore
from .corpus import CorpusLike, load_corpus
from .features import SEGMENT_GAP_NS, Segment
from .matcher import (MIN_CONFIDENCE, NGRAM, Candidate, ThemeIndex, Votes,
                      best_candidate)

__all__ = ["Verdict", "Identifier", "Stats", "replay", "replay_all"]


@dataclass(frozen=True, slots=True)
class Verdict:
    """An identification, with the evidence that earned it."""

    theme_id: str
    composer: str
    work: str
    opus: str | None
    number: str | None
    label: str
    confidence: float
    hits: int
    density: float
    margin: float
    line: str
    notes: int
    seconds: float
    segment: int
    runner_up: str = ""
    corpus_size: int = 0

    def line_text(self, *, colour: bool = True) -> str:
        """The single line the runner prints. Purple, because he asked."""
        body = (f"{self.label} "
                f"[{self.notes} notes, {self.seconds:.0f} s, "
                f"conf {self.confidence:.2f}]")
        if not colour:
            return f"IDENTIFIED  {body}"
        return f"\033[95m\U0001f7e3 {body}\033[0m"


@dataclass(slots=True)
class Stats:
    """What a run cost. Reported, not guessed at."""

    ticks: int = 0
    cpu_s: float = 0.0
    wall_s: float = 0.0
    new_messages: int = 0
    new_notes: int = 0
    ngrams: int = 0

    @property
    def cpu_per_tick_ms(self) -> float:
        return 1000.0 * self.cpu_s / max(1, self.ticks)

    @property
    def load(self) -> float:
        return self.cpu_s / max(1e-9, self.wall_s)

    def line(self) -> str:
        return (f"{self.ticks} ticks, {self.cpu_s * 1000:.0f} ms CPU total, "
                f"{self.cpu_per_tick_ms:.2f} ms/tick, "
                f"{self.new_notes} notes, {self.ngrams} n-grams")


@dataclass(slots=True)
class _SegState:
    seg: Segment
    votes: dict[str, Votes]
    announced: bool = False
    best: Candidate | None = None


class Identifier:
    """Identify what is being played, from a take that is still being written.

    ::

        ident = Identifier()                 # loads whatever corpus exists
        while True:
            v = ident.update(take)           # None until it is sure
            if v:
                print(v.line_text())
            time.sleep(3)

    ``update`` is safe to call as often as you like: with no new bytes it is a
    ``stat`` and a couple of integer comparisons.
    """

    def __init__(self, corpus: CorpusLike | None = None, *,
                 index: ThemeIndex | None = None, n: int = NGRAM,
                 min_confidence: float = MIN_CONFIDENCE,
                 segment_gap_ns: int = SEGMENT_GAP_NS, repeat: bool = False):
        if index is not None:
            self.index = index
            self.corpus = corpus
        else:
            self.corpus = corpus if corpus is not None else load_corpus()
            self.index = ThemeIndex(self.corpus, n=n)
        self.min_confidence = min_confidence
        self.segment_gap_ns = segment_gap_ns
        self.repeat = repeat
        self.stats = Stats()
        self.store: TakeStore | None = None
        self.take: Path | None = None
        self._cursor = -1          # highest message seq consumed
        self._last_ns = 0
        self._segments: list[_SegState] = []
        self._t0 = time.perf_counter()

    # -- lifecycle ---------------------------------------------------------

    def attach(self, take: str | Path) -> None:
        """Point at a take, discarding any state from a previous one."""
        take = Path(take).expanduser()
        if self.store is not None:
            self.store.close()
        self.store = TakeStore(take)
        self.take = take
        self._reset_caches()

    def _reset_caches(self) -> None:
        self._cursor = -1
        self._last_ns = 0
        self._segments = []

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None

    # -- the tick ----------------------------------------------------------

    def update(self, take: str | Path | None = None) -> Verdict | None:
        """One tick. Returns a verdict only when the evidence clears the bar."""
        t_cpu = time.process_time()
        if take is not None and (self.take is None or
                                 Path(take).expanduser() != self.take):
            self.attach(take)
        if self.store is None:
            raise ValueError("no take attached; pass one to update()")

        res = self.store.ingest()
        if res.full_reingest:
            # The file was replaced or truncated, so the store threw its index
            # away and read from zero. Every cache here is keyed to the old
            # numbering and is now lying; drop them too rather than resume into
            # a stream that has silently restarted.
            self._reset_caches()
        onsets = self._new_onsets()
        verdict = self.feed(onsets) if onsets else self._decide()

        self.stats.ticks += 1
        self.stats.cpu_s += time.process_time() - t_cpu
        self.stats.wall_s = time.perf_counter() - self._t0
        return verdict

    def _new_onsets(self) -> list[tuple[int, int]]:
        """Note-ons appended since the last tick, in stream order.

        Read from ``message``, not ``interval``: a message is written the moment
        it is ingested, whereas an interval is written when its note-off closes
        it, so intervals arrive out of onset order and a pedalled bass note can
        arrive seconds late. Onsets are all this layer wants anyway.
        """
        assert self.store is not None
        rows = self.store.db.execute(
            "SELECT seq, ns, d1 FROM message "
            "WHERE seq > ? AND kind = 'note_on' AND d2 > 0 ORDER BY seq",
            (self._cursor,)).fetchall()
        last = self.store.db.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM message").fetchone()[0]
        self._cursor = max(self._cursor, last)
        self.stats.new_notes += len(rows)
        return [(r[1], r[2]) for r in rows]

    # -- the cache, fed forward -------------------------------------------

    def feed(self, onsets: Sequence[tuple[int, int]]) -> Verdict | None:
        """Push onsets through segmentation, the lines, and the votes."""
        for chunk, ended in self._split(onsets):
            if not chunk and not ended:
                continue
            if not self._segments:
                self._open_segment()
            st = self._segments[-1]
            st.seg.feed(chunk, final=ended)
            for name, line in st.seg.lines.items():
                self.stats.ngrams += st.votes[name].add(line.pitches)
            if ended:
                st.best = best_candidate(st.votes, force=True)
                self._open_segment()
        return self._decide()

    def _split(self, onsets: Sequence[tuple[int, int]]
               ) -> list[tuple[list[tuple[int, int]], bool]]:
        """Cut wherever the silence is long enough to have ended a piece.

        A cut yields the notes accumulated *before* the silence, flagged ended;
        the flag can arrive with an empty chunk, which is the case where the
        silence began during the previous tick.
        """
        out: list[tuple[list[tuple[int, int]], bool]] = []
        cur: list[tuple[int, int]] = []
        for ns, note in onsets:
            if self._last_ns and ns - self._last_ns > self.segment_gap_ns:
                out.append((cur, True))
                cur = []
            cur.append((ns, note))
            self._last_ns = ns
        out.append((cur, False))
        return out

    def _open_segment(self) -> None:
        seg = Segment(index=len(self._segments))
        self._segments.append(
            _SegState(seg=seg,
                      votes={name: Votes(self.index) for name in seg.lines}))

    def _decide(self) -> Verdict | None:
        if not self._segments:
            return None
        st = self._segments[-1]
        if st.announced and not self.repeat:
            return None
        c = best_candidate(st.votes)
        if c is not None:
            st.best = c
        if c is None or c.confidence < self.min_confidence:
            return None
        st.announced = True
        return self._verdict(st, c)

    def _verdict(self, st: _SegState, c: Candidate) -> Verdict:
        th = c.theme
        return Verdict(
            theme_id=th.id, composer=th.composer, work=th.work, opus=th.opus,
            number=th.number, label=th.label, confidence=c.confidence,
            hits=c.hits, density=round(c.density, 3), margin=round(c.margin, 3),
            line=c.line, notes=st.seg.notes, seconds=st.seg.seconds,
            segment=st.seg.index, runner_up=c.runner_up,
            corpus_size=len(self.index))

    # -- introspection -----------------------------------------------------

    @property
    def current(self) -> Candidate | None:
        """The leading candidate right now, whether or not it clears the bar."""
        if not self._segments:
            return None
        return best_candidate(self._segments[-1].votes, force=True)

    @property
    def collisions(self) -> int:
        return sum(v.collisions for s in self._segments for v in s.votes.values())

    @property
    def notes_seen(self) -> int:
        return sum(s.seg.notes for s in self._segments)

    def finish(self) -> Verdict | None:
        """Flush the tail. For a take known to have ended, never while playing."""
        if not self._segments:
            return None
        st = self._segments[-1]
        st.seg.feed([], final=True)
        for name, line in st.seg.lines.items():
            self.stats.ngrams += st.votes[name].add(line.pitches)
        return self._decide()


def replay(take: str | Path, corpus: CorpusLike | None = None, *,
           chunk: int = 8, **kw) -> tuple[Verdict | None, Identifier]:
    """Replay a finished take through the identifier as if it were live.

    ``chunk`` notes are handed over at a time, so the returned verdict's
    ``notes`` count is the number of notes the identifier actually needed,
    to within one chunk. This is the regression harness and the benchmark.
    """
    ident = Identifier(corpus, **kw)
    ident.attach(take)
    ident.store.ingest()  # type: ignore[union-attr]
    rows = ident.store.db.execute(  # type: ignore[union-attr]
        "SELECT ns, d1 FROM message WHERE kind = 'note_on' AND d2 > 0 "
        "ORDER BY seq").fetchall()
    onsets = [(r[0], r[1]) for r in rows]
    for i in range(0, len(onsets), chunk):
        t = time.process_time()
        v = ident.feed(onsets[i:i + chunk])
        ident.stats.ticks += 1
        ident.stats.cpu_s += time.process_time() - t
        ident.stats.new_notes += len(onsets[i:i + chunk])
        if v is not None:
            return v, ident
    return ident.finish(), ident


def replay_all(take: str | Path, corpus: CorpusLike | None = None, *,
               chunk: int = 8, **kw) -> tuple[list[Verdict], Identifier]:
    """Replay to the end, collecting one verdict per segment.

    Used on the 44-minute session take, which is four pieces and needs four
    answers rather than the first one.
    """
    ident = Identifier(corpus, **kw)
    ident.attach(take)
    ident.store.ingest()  # type: ignore[union-attr]
    rows = ident.store.db.execute(  # type: ignore[union-attr]
        "SELECT ns, d1 FROM message WHERE kind = 'note_on' AND d2 > 0 "
        "ORDER BY seq").fetchall()
    onsets = [(r[0], r[1]) for r in rows]
    out: list[Verdict] = []
    for i in range(0, len(onsets), chunk):
        t = time.process_time()
        v = ident.feed(onsets[i:i + chunk])
        ident.stats.ticks += 1
        ident.stats.cpu_s += time.process_time() - t
        ident.stats.new_notes += len(onsets[i:i + chunk])
        if v is not None:
            out.append(v)
    v = ident.finish()
    if v is not None:
        out.append(v)
    return out, ident
