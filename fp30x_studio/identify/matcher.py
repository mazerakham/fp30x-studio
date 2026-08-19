"""The index and the vote: Shazam's alignment histogram, moved into pitch.

Audio fingerprinting wins by refusing to score similarity. It hashes local
features, looks each hash up in an inverted index, and then asks a much sharper
question than "how many hashes agree?" -- it asks whether the agreeing hashes
agree *at a constant offset*. Coincidences scatter across offsets; a real match
piles onto one diagonal.

The same trick works here with two substitutions:

* the local feature is a **melodic interval n-gram**, not a spectral peak pair,
  so the hash is invariant under transposition -- he can play the Joplin in any
  key and it hashes identically;
* the offset is a **position in the line**, not a time in seconds, so the hash
  is invariant under tempo, rubato and stopping to fix a bar.

What the diagonal buys, concretely: four notes of a scale fragment match half
the corpus. Four notes that match theme T at position 12 while the next four
match T at 13 and the next at 14 do not.

Cost control, since this runs on a timer next to a man playing the piano:

* **Votes accumulate.** Each n-gram is looked up exactly once, ever. A tick
  costs the n-grams that tick produced, not the n-grams so far.
* **Stop-hashes are dropped at build time.** A hash held by a large fraction of
  the corpus (chromatic wandering, repeated notes) carries no information and
  all of the cost; :data:`MAX_DF` drops it.
* The accumulator is keyed by ``(theme, diagonal)`` and only ever holds pairs
  that actually collided, so it stays small on the silence and the noise.

Scoring is deliberately conservative. The gates are on the *shape* of the
evidence -- how many hits, how densely they sit on their diagonal, and how far
clear of the runner-up -- so that "not enough yet" is the default answer and
stays the answer until the evidence is unambiguous.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from .corpus import CorpusLike, Theme
from .features import ngrams_of

__all__ = ["NGRAM", "MAX_DF", "MIN_DF_CAP", "MAX_POSTINGS", "MIN_HITS",
           "MIN_DENSITY", "MIN_CONFIDENCE", "RESCAN_AT", "PRUNE_LAG",
           "PRUNE_EVERY", "ThemeIndex", "Candidate", "Votes", "score",
           "evidence", "separation", "best_candidate"]

#: Intervals per hash. Four intervals is five notes: long enough that a hash is
#: rare, short enough that one wrong note costs only four hashes.
NGRAM = 4

#: A hash held by more than this fraction of the corpus's *works* is a
#: stop-hash: it costs a posting list to walk and carries no information about
#: which piece this is. On a real corpus this is what removes the scale and
#: arpeggio fragments. Counted in works rather than themes because the corpus
#: carries roughly nine themes per work, and counting themes would let one
#: heavily sectioned sonata look like a hundred independent pieces.
MAX_DF = 0.10

#: ...but never prune below this many themes. A ten-theme stub would otherwise
#: throw away every hash two of its themes happen to share, which is most of
#: them, and the identifier would look broken when only the corpus was small.
MIN_DF_CAP = 8

#: Hard ceiling on one hash's posting list, whatever the corpus size. This is
#: the only thing standing between a tick and an unbounded inner loop.
MAX_POSTINGS = 4000

#: Fewest aligned hits that may ever be called an identification.
MIN_HITS = 6

#: Aligned hits, over the query span they cover. Below this the "match" is a
#: scatter of coincidences that happen to share a diagonal.
MIN_DENSITY = 0.34

#: Votes are re-ranked only when the leading diagonal grows. Below this many
#: hits no candidate could clear the gates anyway, so the tick does no work at
#: all beyond the lookups themselves.
#:
#: This is not an optimisation detail, it is the difference between a runner
#: that keeps up and one that does not: without it every tick rescans the whole
#: accumulator, the accumulator grows with the take, and the cost per tick grows
#: quadratically in a session that lasts an hour.
RESCAN_AT = MIN_HITS

#: Stale single-hit cells are dropped once the query has moved this far past
#: them. A cell that old could only contribute to a match spanning hundreds of
#: positions, and :data:`MIN_DENSITY` has already refused that.
PRUNE_LAG = 240
PRUNE_EVERY = 8192

#: Confidence at or above which the runner speaks. Chosen so that a wrong
#: answer costs more than a late one -- he is reading this out of one eye.
MIN_CONFIDENCE = 0.80


@dataclass(slots=True)
class Candidate:
    """One theme's best diagonal, and the evidence for it."""

    theme: Theme
    hits: int
    diagonal: int
    q_first: int
    q_last: int
    line: str = ""
    confidence: float = 0.0
    runner_up: str = ""
    runner_up_hits: int = 0

    @property
    def span(self) -> int:
        return self.q_last - self.q_first + 1

    @property
    def density(self) -> float:
        return self.hits / max(1, self.span)

    @property
    def margin(self) -> float:
        if not self.hits:
            return 0.0
        return (self.hits - self.runner_up_hits) / self.hits


class ThemeIndex:
    """An inverted index from interval n-gram to ``(theme, position)``."""

    __slots__ = ("themes", "groups", "n", "postings", "n_hashes", "dropped")

    def __init__(self, corpus: CorpusLike | Iterable[Theme], *, n: int = NGRAM,
                 max_df: float = MAX_DF):
        if hasattr(corpus, "themes"):
            themes = [Theme.coerce(t) for t in corpus.themes()]
        else:
            themes = [Theme.coerce(t) for t in corpus]
        self.themes: list[Theme] = themes
        self.groups: list[str] = [t.key for t in themes]
        self.n = n
        raw: dict[tuple[int, ...], list[tuple[int, int]]] = {}
        for ti, th in enumerate(themes):
            for pos, g in ngrams_of(th.pitches, n):
                raw.setdefault(g, []).append((ti, pos))
        n_groups = len(set(self.groups))
        cap = max(MIN_DF_CAP, int(max_df * max(1, n_groups)))
        groups = self.groups
        self.dropped = 0
        self.postings: dict[tuple[int, ...], tuple[tuple[int, int], ...]] = {}
        for g, lst in raw.items():
            if len(lst) > MAX_POSTINGS or len({groups[ti] for ti, _ in lst}) > cap:
                self.dropped += 1
                continue
            self.postings[g] = tuple(lst)
        self.n_hashes = len(self.postings)

    def __len__(self) -> int:
        return len(self.themes)

    def lookup(self, g: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
        return self.postings.get(g, ())


class Votes:
    """The running alignment histogram for one query line against one index.

    Two dictionaries, both keyed by ``(theme index, diagonal)``:

    ``cells``    the exact diagonal, and the first and last query position that
                 voted for it. This is what density is measured on.
    ``merged``   the same, summed over a diagonal and its two neighbours, which
                 is what absorbs one inserted or dropped note mid-phrase.

    ``merged`` is maintained on the way in rather than rebuilt on the way out.
    That is the whole trick: the leading candidate is known after every vote for
    the cost of three dictionary updates, so a tick that identifies nothing
    costs nothing but its lookups, and the accumulator's size stops mattering.
    """

    __slots__ = ("index", "cells", "merged", "consumed", "looked_up",
                 "collisions", "peak", "peak_key", "_scanned_at", "_dirty",
                 "_since_prune")

    def __init__(self, index: "ThemeIndex"):
        self.index = index
        self.cells: dict[tuple[int, int], list[int]] = {}
        self.merged: dict[tuple[int, int], list[int]] = {}
        self.consumed = 0
        self.looked_up = 0
        self.collisions = 0
        self.peak = 0
        self.peak_key: tuple[int, int] | None = None
        self._scanned_at = -1
        self._dirty = False
        self._since_prune = 0

    def add(self, pitches: Sequence[int]) -> int:
        """Hash every n-gram not yet hashed. Returns how many were added."""
        grams = ngrams_of(pitches, self.index.n, start=self.consumed)
        if not grams:
            return 0
        lookup = self.index.postings.get
        cells, merged = self.cells, self.merged
        peak, peak_key = self.peak, self.peak_key
        for qi, g in grams:
            for ti, pos in lookup(g, ()):
                d = pos - qi
                key = (ti, d)
                cell = cells.get(key)
                if cell is None:
                    cells[key] = [1, qi, qi]
                else:
                    cell[0] += 1
                    cell[2] = qi
                for dd in (d - 1, d, d + 1):
                    mk = (ti, dd)
                    m = merged.get(mk)
                    if m is None:
                        merged[mk] = m = [1, qi, qi]
                    else:
                        m[0] += 1
                        if qi < m[1]:
                            m[1] = qi
                        if qi > m[2]:
                            m[2] = qi
                    if m[0] >= RESCAN_AT:
                        self._dirty = True
                        if m[0] > peak:
                            peak, peak_key = m[0], mk
                    elif m[0] > peak:
                        peak, peak_key = m[0], mk
                self.collisions += 1
        self.peak, self.peak_key = peak, peak_key
        self.consumed = grams[-1][0] + 1
        self.looked_up += len(grams)
        self._since_prune += len(grams)
        if self._since_prune >= PRUNE_EVERY:
            self._prune()
        return len(grams)

    def _prune(self) -> None:
        """Forget coincidences the query has left far behind."""
        self._since_prune = 0
        floor = self.consumed - PRUNE_LAG
        if floor <= 0:
            return
        for d in (self.cells, self.merged):
            for k in [k for k, v in d.items() if v[0] < MIN_HITS and v[2] < floor]:
                del d[k]

    @property
    def worth_ranking(self) -> bool:
        """Could anything here clear the gates, and has it changed since asked?

        A tick where this is false is a tick that does no ranking work at all.

        Two ways to become worth ranking: the leading diagonal grew past the
        floor, or a diagonal already past the floor gained a hit. The second
        clause matters -- without it a candidate that reaches the floor before
        its density is good enough is never looked at again until some other
        diagonal overtakes it, which on the Joplin cost fifteen notes of delay.
        """
        return self.peak >= RESCAN_AT and (self.peak > self._scanned_at
                                           or self._dirty)

    def best(self, *, force: bool = False) -> tuple["Candidate | None", int]:
        """Rank the accumulator. Returns the leader and the runner-up's hits."""
        if not force and not self.worth_ranking:
            return None, 0
        self._scanned_at = self.peak
        self._dirty = False
        if not self.merged:
            return None, 0

        def effective(v: list[int]) -> int:
            """Hits that sit densely enough on their diagonal to be evidence.

            Without this the runner-up is whichever theme collected the most
            *scattered* coincidences over the whole take, which on a 1700-note
            rag is three or four, and the margin gate would then punish a
            perfect match for the existence of noise.
            """
            h, q0, q1 = v
            return h if h / max(1, q1 - q0 + 1) >= MIN_DENSITY else 0

        best_key, best_val, best_eff = None, None, -1
        per_key: dict[tuple[int, int], list[int]] = {}
        for (ti, d), v in self.merged.items():
            e = effective(v)
            if e > best_eff or (e == best_eff and best_val is not None
                                and v[0] > best_val[0]):
                best_key, best_val, best_eff = (ti, d), v, e
        if best_key is None or best_val is None:
            return None, 0
        ti, d = best_key
        h, q0, q1 = best_val
        group = self.index.groups[ti]

        # The runner-up is whatever *other work* explains this same passage. A
        # coincidence four hundred notes away is not a competing hypothesis
        # about the phrase just played, and neither is the same piece showing up
        # twice -- the corpus holds several lines and several sections per work,
        # and letting them veto each other would silence every correct answer.
        second, second_label = 0, ""
        for (tj, dj), v in self.merged.items():
            if self.index.groups[tj] == group or v[2] < q0 or v[1] > q1:
                continue
            e = effective(v)
            if e > second:
                second, second_label = e, self.index.themes[tj].label
        return Candidate(theme=self.index.themes[ti], hits=h, diagonal=d,
                         q_first=q0, q_last=q1, runner_up_hits=second,
                         runner_up=second_label), second


def evidence(hits: int) -> float:
    """How much an aligned run of ``hits`` n-grams is worth, in [0, 1).

    ``hits`` aligned n-grams sitting contiguously pin down ``hits + n - 1``
    consecutive melodic intervals. The value of that grows sharply and then
    saturates, because past a certain length the answer is already certain and
    more agreement cannot make it more certain. Two hits is worth nothing --
    two adjacent four-grams is five notes, and five notes of a scale are in half
    the repertoire.
    """
    return max(0.0, 1.0 - math.exp(-(hits - 2) / 2.0))


def separation(best: int, second: int) -> float:
    """How clear the winner is of the best competing *work*, in [0, 1].

    Two rules, both learned from the data rather than chosen:

    A candidate that could not itself be an answer is not competition. Six is
    the floor for an identification, so a rival holding five aligned hits costs
    the winner nothing. Without this, a descending scale shared with Ode to Joy
    was docking the Joplin fifteen points of confidence.

    Separation is measured in the *difference* of hit counts, not their ratio.
    Each additional aligned n-gram is one more interval of agreement, so it adds
    a roughly constant amount of log-odds; ratios of a saturating quantity say
    the opposite of what they should, and reported twenty-three hits against six
    as a near-tie.
    """
    if second < MIN_HITS or best <= 0:
        return 1.0
    if best <= second:
        return 0.0
    return 1.0 - math.exp(-(best - second) / 2.0)


def score(c: Candidate) -> float:
    """Calibrated confidence in ``[0, 1]``: evidence, shape, and separation.

    Three independent things must all be true before this is near one, and any
    one of them being weak pulls it down:

    ``weight``   there is enough evidence at all -- :func:`evidence` of the
                 aligned hit count, and zero below :data:`MIN_HITS`;
    ``shape``    the hits sit densely on their diagonal rather than scattered
                 across the query;
    ``clear``    nothing else explains the same passage nearly as well
                 (:func:`separation`).

    The product, not the mean: a weak factor must be able to veto. A corpus
    holding two *different* pieces that open alike will never clear ``clear``,
    which is right -- the honest answer there is "one of these two", not a coin
    toss. Two entries for the *same* piece are not rivals at all; see
    :attr:`~.corpus.Theme.key`.
    """
    if c.hits < MIN_HITS:
        return 0.0
    weight = evidence(c.hits)
    shape = min(1.0, c.density / 0.75)
    if c.density < MIN_DENSITY:
        shape = 0.0
    return round(weight * shape * separation(c.hits, c.runner_up_hits), 4)


def best_candidate(votes_by_line: dict[str, Votes], *, force: bool = False
                   ) -> Candidate | None:
    """Best candidate across every line, with its confidence filled in."""
    best: Candidate | None = None
    for name, v in votes_by_line.items():
        c, _ = v.best(force=force)
        if c is None:
            continue
        c.line = name
        c.confidence = score(c)
        if best is None or (c.confidence, c.hits) > (best.confidence, best.hits):
            best = c
    return best
