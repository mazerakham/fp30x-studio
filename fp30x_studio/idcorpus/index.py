"""Load the theme corpus and match a live pitch stream against it.

    from fp30x_studio.idcorpus import ThemeCorpus
    tc = ThemeCorpus()                       # ~/workspace/audio/corpus/themes.sqlite
    tc.stats()
    for hit in tc.search([76, 73, 71, 70, 68, 66, 75, 54, 73, 71]):
        print(hit.score, hit.record.composer, hit.record.title, hit.record.opus)

Matching is transposition-invariant: a theme is reduced to its sequence of
semitone intervals, chopped into overlapping n-grams, and each n-gram is packed
into a single int64.  All n-grams from the whole corpus live in one sorted
numpy array, so looking up a query n-gram is a binary search and scoring a
query is a bincount.  No third-party dependency beyond numpy.

Licence filtering is first-class: `ThemeCorpus(commercial_only=True)` drops
every record whose source forbids or leaves unclear commercial use.  Do not
route around it.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .sources import DB_PATH

# n-gram parameters.  NGRAM=5 intervals means 6 notes -- short enough to hit
# early in a performance, long enough to be discriminating.
NGRAM = 5
IVL_CLIP = 24            # semitones; intervals are clipped into [-24, 24]
_BASE = 2 * IVL_CLIP + 1


def _nums(text: str, dtype) -> np.ndarray:
    if not text:
        return np.empty(0, dtype)
    return np.array(text.split(","), dtype=dtype)


@dataclass(frozen=True)
class ThemeRecord:
    theme_id: int
    composer: str
    composer_key: str
    title: str
    opus: str
    catalogue: str
    movement: str
    line: str
    kind: str
    start_measure: int
    source: str
    source_id: str
    licence: str
    commercial_ok: bool
    path: str
    _pitches: str
    _onsets: str
    _intervals: str

    @property
    def pitches(self) -> np.ndarray:
        return _nums(self._pitches, np.int64)

    @property
    def onsets(self) -> np.ndarray:
        return _nums(self._onsets, np.float64)

    @property
    def durations(self) -> np.ndarray:
        o = self.onsets
        return np.diff(o, append=o[-1] if len(o) else 0.0)

    @property
    def intervals(self) -> np.ndarray:
        return _nums(self._intervals, np.int64)

    def cite(self) -> str:
        bits = [self.composer, self.title]
        return " - ".join(b for b in bits if b)


@dataclass(frozen=True)
class Hit:
    score: float
    votes: int
    record: ThemeRecord


def _pack(intervals: np.ndarray, n: int = NGRAM) -> np.ndarray:
    """Overlapping n-grams of an interval sequence, packed one per int64."""
    iv = np.clip(np.asarray(intervals, dtype=np.int64), -IVL_CLIP, IVL_CLIP) + IVL_CLIP
    if iv.size < n:
        return np.empty(0, np.int64)
    w = np.lib.stride_tricks.sliding_window_view(iv, n)
    mult = _BASE ** np.arange(n - 1, -1, -1, dtype=np.int64)
    return w @ mult


_COLS = ("theme_id, composer, composer_key, title, opus, catalogue, movement, "
         "line, kind, start_measure, source, source_id, licence, commercial_ok, "
         "path, pitches, onsets, intervals")


class ThemeCorpus:
    def __init__(self, db_path: Path | str = DB_PATH, commercial_only: bool = False,
                 ngram: int = NGRAM, sources: list[str] | None = None,
                 cache: bool = True):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"{self.db_path} not found - build it with "
                f"`python -m fp30x_studio.idcorpus.build`")
        self.commercial_only = commercial_only
        self.ngram = ngram
        self._con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._con.row_factory = sqlite3.Row

        where, args = [], []
        if commercial_only:
            where.append("commercial_ok = 1")
        if sources:
            where.append("source_id IN (%s)" % ",".join("?" * len(sources)))
            args += list(sources)
        sql = f"SELECT {_COLS} FROM theme_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        self._filter_sql, self._filter_args = (
            (" WHERE " + " AND ".join(where)) if where else "", args)
        self._cache_path = self.db_path.with_suffix(".ngrams.npz") if cache else None
        self._cache_key = hashlib.sha1(
            f"{self.db_path.stat().st_mtime_ns}|{self.db_path.stat().st_size}"
            f"|{ngram}|{self._filter_sql}|{args}".encode()).hexdigest()[:16]
        if not self._load_cache():
            self._build_ngrams()
            self._save_cache()

    # ------------------------------------------------------------------ index

    def _build_ngrams(self) -> None:
        """Stream (theme_id, intervals) out of SQLite into two flat numpy arrays.

        Only the n-gram arrays are kept resident.  Metadata stays in the
        database and is fetched per hit, which keeps a million-theme corpus
        inside a few hundred megabytes instead of a few gigabytes of Python
        objects.
        """
        sql = f"SELECT theme_id, intervals FROM theme_records{self._filter_sql}"
        hashes, owners, ids = [], [], []
        for row_i, (tid, ivs) in enumerate(self._con.execute(sql, self._filter_args)):
            ids.append(tid)
            g = _pack(_nums(ivs, np.int64), self.ngram)
            if g.size:
                hashes.append(g)
                owners.append(np.full(g.size, row_i, dtype=np.int32))
        self._ids = np.array(ids, dtype=np.int64) if ids else np.empty(0, np.int64)
        self.n_themes = int(self._ids.size)
        if hashes:
            h = np.concatenate(hashes)
            o = np.concatenate(owners)
            order = np.argsort(h, kind="stable")
            self._hash, self._owner = h[order], o[order]
            self._n_per_theme = np.bincount(o, minlength=self.n_themes).astype(np.int64)
        else:
            self._hash = np.empty(0, np.int64)
            self._owner = np.empty(0, np.int32)
            self._n_per_theme = np.zeros(self.n_themes, np.int64)

    # The n-gram arrays take a minute to build over a million-theme corpus, and
    # a live identifier cannot pay that on every launch, so they are cached
    # beside the database and invalidated by the database's mtime and size.

    def _load_cache(self) -> bool:
        if not self._cache_path or not self._cache_path.exists():
            return False
        try:
            with np.load(self._cache_path) as z:
                k = self._cache_key
                if f"h_{k}" not in z:
                    return False
                self._hash = z[f"h_{k}"]
                self._owner = z[f"o_{k}"]
                self._ids = z[f"i_{k}"]
        except Exception:  # noqa: BLE001 - a corrupt cache must not be fatal
            return False
        self.n_themes = int(self._ids.size)
        self._n_per_theme = np.bincount(self._owner, minlength=self.n_themes).astype(np.int64)
        return True

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        payload = {}
        if self._cache_path.exists():
            try:
                with np.load(self._cache_path) as z:
                    payload = {k: z[k] for k in z.files}
            except Exception:  # noqa: BLE001
                payload = {}
        k = self._cache_key
        payload[f"h_{k}"], payload[f"o_{k}"], payload[f"i_{k}"] = (
            self._hash, self._owner, self._ids)
        try:
            np.savez(self._cache_path, **payload)
        except OSError:
            pass

    def record(self, theme_id: int) -> ThemeRecord:
        r = self._con.execute(
            f"SELECT {_COLS} FROM theme_records WHERE theme_id = ?", (int(theme_id),)).fetchone()
        return ThemeRecord(
            theme_id=r["theme_id"], composer=r["composer"] or "",
            composer_key=r["composer_key"] or "", title=r["title"] or "",
            opus=r["opus"] or "", catalogue=r["catalogue"] or "",
            movement=r["movement"] or "", line=r["line"], kind=r["kind"],
            start_measure=r["start_measure"] if r["start_measure"] is not None else -1,
            source=r["source"], source_id=r["source_id"],
            licence=r["licence"] or "", commercial_ok=bool(r["commercial_ok"]),
            path=r["path"] or "", _pitches=r["pitches"], _onsets=r["onsets"],
            _intervals=r["intervals"])

    # ----------------------------------------------------------------- search

    # An interval n-gram shared by more postings than this is a stopword: it
    # carries almost no evidence and expanding it dominates the query cost.
    MAX_POSTINGS = 3000

    def _rank(self, pitches, min_votes: int = 2):
        """-> (candidate row indices, their scores, their vote counts)."""
        p = np.asarray([int(x) for x in pitches], dtype=np.int64)
        empty = (np.empty(0, np.int64),) * 3
        if p.size < self.ngram + 1 or self._hash.size == 0:
            return empty
        q = _pack(np.diff(p), self.ngram)
        if q.size == 0:
            return empty

        lo = np.searchsorted(self._hash, q, "left")
        hi = np.searchsorted(self._hash, q, "right")
        counts = hi - lo
        keep = (counts > 0) & (counts <= self.MAX_POSTINGS)
        if not keep.any():
            keep = (counts > 0) & (counts <= self.MAX_POSTINGS * 10)
        if not keep.any():
            return empty
        idx = np.concatenate([np.arange(a, b) for a, b in zip(lo[keep], hi[keep])])
        votes = np.bincount(self._owner[idx], minlength=self.n_themes)

        cand = np.flatnonzero(votes >= min_votes)
        if cand.size == 0:
            cand = np.flatnonzero(votes > 0)
        if cand.size == 0:
            return empty
        # Geometric mean of query coverage and theme coverage: rewards a theme
        # matched throughout, not one that merely contains a common figure.
        denom = float(q.size) ** 0.5
        nth = np.maximum(1, self._n_per_theme[cand]).astype(np.float64) ** 0.5
        scores = votes[cand].astype(np.float64) / (denom * nth)
        order = np.argsort(-scores)
        return cand[order], scores[order], votes[cand][order]

    def search(self, pitches, top: int = 10, min_votes: int = 2) -> list[Hit]:
        """Rank themes against a monophonic pitch sequence (MIDI note numbers)."""
        cand, scores, votes = self._rank(pitches, min_votes)
        out = []
        for i in range(min(top, cand.size)):
            out.append(Hit(score=round(float(scores[i]), 4), votes=int(votes[i]),
                           record=self.record(int(self._ids[cand[i]]))))
        return out

    def search_works(self, pitches, top: int = 10, min_votes: int = 2,
                     scan: int = 200) -> list[Hit]:
        """Like search(), but collapse multiple themes of the same work.

        Only the best `scan` themes are resolved to metadata; below that the
        scores are too low to win a group anyway, and each resolution is a
        database round-trip.
        """
        cand, scores, votes = self._rank(pitches, min_votes)
        best: dict[str, Hit] = {}
        for i in range(min(scan, cand.size)):
            rec = self.record(int(self._ids[cand[i]]))
            k = f"{rec.composer_key}|{rec.title}|{rec.opus}"
            if k not in best:
                best[k] = Hit(score=round(float(scores[i]), 4), votes=int(votes[i]), record=rec)
            if len(best) >= top and i > top * 4:
                break
        return sorted(best.values(), key=lambda h: -h.score)[:top]

    # ------------------------------------------------------------------ meta

    def stats(self) -> dict:
        c = self._con
        q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
        return {
            "db": str(self.db_path),
            "sources": q("SELECT COUNT(*) FROM sources WHERE work_count > 0"),
            "works": q("SELECT COUNT(*) FROM works"),
            "themes": q("SELECT COUNT(*) FROM themes"),
            "composers": q("SELECT COUNT(DISTINCT composer_key) FROM works WHERE composer_key <> ''"),
            "works_with_opus_or_catalogue":
                q("SELECT COUNT(*) FROM works WHERE opus <> '' OR catalogue <> ''"),
            "themes_commercial_ok": q("SELECT COUNT(*) FROM themes t JOIN works w ON w.id=t.work_id "
                                      "WHERE w.commercial_ok=1"),
            "loaded_themes": self.n_themes,
            "ngrams_indexed": int(self._hash.size),
            "commercial_only": self.commercial_only,
        }

    def by_source(self) -> list[sqlite3.Row]:
        return self._con.execute(
            "SELECT id, name, licence, spdx, commercial_ok, file_count, work_count, theme_count "
            "FROM sources WHERE work_count > 0 ORDER BY theme_count DESC").fetchall()

    def composers(self, limit: int = 40) -> list[sqlite3.Row]:
        return self._con.execute(
            "SELECT composer, composer_key, COUNT(*) n FROM works WHERE composer_key <> '' "
            "GROUP BY composer_key ORDER BY n DESC LIMIT ?", (limit,)).fetchall()

    def find(self, text: str, limit: int = 20) -> list[sqlite3.Row]:
        """Free-text lookup over composer/title/opus - for spot-checking coverage."""
        like = f"%{text}%"
        return self._con.execute(
            "SELECT source_id, composer, display, opus, catalogue, licence, commercial_ok, path "
            "FROM works WHERE composer LIKE ? OR display LIKE ? OR opus LIKE ? "
            "ORDER BY composer, display LIMIT ?", (like, like, like, limit)).fetchall()

    def close(self) -> None:
        self._con.close()
