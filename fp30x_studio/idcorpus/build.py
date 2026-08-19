"""Build the theme index (SQLite) from the raw corpora under ~/workspace/audio/corpus/raw.

    python -m fp30x_studio.idcorpus.build            # build everything
    python -m fp30x_studio.idcorpus.build --only joplin nifc-chopin
    python -m fp30x_studio.idcorpus.build --limit 20 --db /tmp/test.sqlite

Why SQLite and not JSON: there are tens of thousands of theme records, each
carrying two numeric sequences plus a metadata row.  The builder for the live
identifier wants indexed lookups (by composer, by licence tier, by source) and
the ability to stream a subset without parsing the whole file.  SQLite is in
the standard library, gives that for free, and is inspectable with `sqlite3`.
A JSONL export is available with --jsonl for anything that would rather stream.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from . import extract as E
from .sources import (DB_PATH, RAW_DIR, RISM_DB_PATH, RISM_JSONL, SOURCES,
                      SOURCES_BY_ID, Source)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, name TEXT, url TEXT, licence TEXT, spdx TEXT,
    commercial_ok INTEGER, attribution TEXT, notes TEXT,
    file_count INTEGER, work_count INTEGER, theme_count INTEGER, captured_at TEXT
);

CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    path TEXT NOT NULL,
    composer TEXT, composer_key TEXT,
    title TEXT, parent_title TEXT,
    opus TEXT, catalogue TEXT,
    movement TEXT, movement_number TEXT,
    music_key TEXT, genre TEXT, date TEXT,
    display TEXT,
    licence TEXT, commercial_ok INTEGER,
    n_notes INTEGER,
    skyline_pitches TEXT, skyline_onsets TEXT,
    melody_pitches TEXT, melody_onsets TEXT
);

CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY,
    work_id INTEGER NOT NULL REFERENCES works(id),
    source_id TEXT NOT NULL,
    line TEXT NOT NULL,          -- 'skyline' (all parts) or 'melody' (top part)
    kind TEXT NOT NULL,          -- 'incipit' or 'section'
    start_index INTEGER, start_measure INTEGER,
    n INTEGER,
    pitches TEXT NOT NULL,       -- comma-separated MIDI note numbers
    onsets TEXT NOT NULL,        -- comma-separated quarter-note offsets from theme start
    intervals TEXT NOT NULL      -- comma-separated semitone deltas (transposition invariant)
);

CREATE INDEX IF NOT EXISTS ix_works_composer ON works(composer_key);
CREATE INDEX IF NOT EXISTS ix_works_source   ON works(source_id);
CREATE INDEX IF NOT EXISTS ix_works_comm     ON works(commercial_ok);
CREATE INDEX IF NOT EXISTS ix_works_opus     ON works(opus);
CREATE INDEX IF NOT EXISTS ix_themes_work    ON themes(work_id);
CREATE INDEX IF NOT EXISTS ix_themes_source  ON themes(source_id);

-- The flat record shape the live identifier consumes.
CREATE VIEW IF NOT EXISTS theme_records AS
SELECT t.id            AS theme_id,
       w.composer      AS composer,
       w.composer_key  AS composer_key,
       w.display       AS title,
       w.opus          AS opus,
       w.catalogue     AS catalogue,
       w.movement      AS movement,
       t.pitches       AS pitches,
       t.onsets        AS onsets,
       t.intervals     AS intervals,
       t.line          AS line,
       t.kind          AS kind,
       t.start_measure AS start_measure,
       s.name          AS source,
       s.id            AS source_id,
       w.licence       AS licence,
       w.commercial_ok AS commercial_ok,
       w.path          AS path
FROM themes t JOIN works w ON w.id = t.work_id JOIN sources s ON s.id = t.source_id;
"""


def _seq(xs) -> str:
    return ",".join(str(x) for x in xs)


def _fseq(xs) -> str:
    return ",".join(f"{x:g}" for x in xs)


# --------------------------------------------------------------- per-file job

_MUTOPIA_META: dict | None = None


def _mutopia_meta() -> dict:
    global _MUTOPIA_META
    if _MUTOPIA_META is None:
        p = RAW_DIR / "mutopia-midi" / "_mutopia_metadata.json"
        _MUTOPIA_META = json.loads(p.read_text()) if p.exists() else {}
    return _MUTOPIA_META


def _mutopia_licence(header: dict) -> tuple[str, bool]:
    """Mutopia states the licence in `license`, or in `copyright` on older files.

    CC BY and CC BY-SA both permit commercial use; only NonCommercial and
    NoDerivatives block it.  Share-alike is permissive but viral -- flagged in
    the licence string itself so a downstream consumer can see it.
    """
    raw = (header.get("license") or header.get("copyright") or "").strip()
    low = raw.lower()
    if not low:
        return "unstated", False
    if "noncommercial" in low or "non-commercial" in low or "-nc" in low or "noderiv" in low:
        return raw, False
    if "public domain" in low or "cc0" in low:
        return raw, True
    if "attribution" in low or low.startswith("cc-by") or low.startswith("cc by"):
        return raw, True
    return raw, False


M21_COMPOSER_DIR = {
    "bach": "Johann Sebastian Bach", "beethoven": "Ludwig van Beethoven",
    "chopin": "Fryderyk Chopin", "joplin": "Scott Joplin",
    "mozart": "Wolfgang Amadeus Mozart", "haydn": "Joseph Haydn",
    "palestrina": "Giovanni Pierluigi da Palestrina", "monteverdi": "Claudio Monteverdi",
    "josquin": "Josquin des Prez", "schumann_robert": "Robert Schumann",
    "schumann_clara": "Clara Schumann", "schubert": "Franz Schubert",
    "verdi": "Giuseppe Verdi", "weber": "Carl Maria von Weber",
    "webern": "Anton Webern", "schoenberg": "Arnold Schoenberg",
    "corelli": "Arcangelo Corelli", "handel": "George Frideric Handel",
    "cpebach": "Carl Philipp Emanuel Bach", "beach": "Amy Beach",
    "ciconia": "Johannes Ciconia", "luca": "Luca", "lusitano": "Vicente Lusitano",
    "johnson_j_r": "J. Rosamond Johnson", "liliuokalani": "Queen Liliuokalani",
    "essenFolksong": "Anonymous (Essen Folksong Collection)",
    "ryansMammoth": "Anonymous (Ryan's Mammoth Collection)",
    "oneills1850": "Anonymous (O'Neill's Music of Ireland)",
    "airdsAirs": "Anonymous (Aird's Airs)",
    "nottingham-dataset": "Anonymous (Nottingham Dataset)",
    "trecento": "Anonymous (Italian Trecento)", "miscFolk": "Anonymous (folk)",
    "leadSheet": "Anonymous (lead sheet)", "demos": "", "theoryExercises": "",
}


def _m21_meta(score, path: Path, src) -> "E.WorkMeta":
    m = E.meta_from_music21(score, path, src)
    parts = path.parts
    if "corpus" in parts:
        i = len(parts) - 1 - parts[::-1].index("corpus")
        if i + 1 < len(parts):
            folder = parts[i + 1]
            if not m.composer or m.composer.lower() in ("", "unknown"):
                m.composer = M21_COMPOSER_DIR.get(folder, folder.replace("_", " ").title())
            if not m.title or m.title == path.stem:
                sub = [x for x in parts[i + 2:-1]]
                m.title = " ".join([*sub, path.stem]).strip() or path.stem
    if not m.opus:
        m.opus = E.opus_from_text(path.stem)
    if not m.catalogue:
        m.catalogue = E.catalogue_from_text(m.title) or E.catalogue_from_text(path.stem)
    return m


def _scores_of(obj):
    """A multi-tune ABC/Humdrum file parses to an Opus; yield (score, index)."""
    from music21 import stream
    if isinstance(obj, stream.Opus):
        for i, sc in enumerate(obj.scores):
            yield sc, i
    else:
        yield obj, -1


def _one_score(score, meta, src, source_id, path: Path, index: int) -> dict | None:
    lines: dict[str, tuple[list, list, list]] = {}
    lines["skyline"] = E.skyline(score)
    tp = E.top_part(score)
    if tp is not None:
        lines["melody"] = E.skyline(tp)
    return _from_lines(lines, meta, src, source_id, path, index)


def _from_lines(lines, meta, src, source_id, path: Path, index: int) -> dict | None:
    sky = lines.get("skyline", ([], [], []))
    if len(sky[0]) < E.MIN_WORK_NOTES:
        return None

    themes, seen = [], set()
    for line_name, (pt, on, mb) in lines.items():
        for t in E.make_themes(pt, on, mb):
            sig = t.intervals()
            if sig in seen:
                continue
            seen.add(sig)
            themes.append(dict(line=line_name, kind=t.kind, start_index=t.start_index,
                               start_measure=t.start_measure, n=len(t.pitches),
                               pitches=_seq(t.pitches), onsets=_fseq(t.onsets),
                               intervals=_seq(sig)))
    if not themes:
        return None

    comp, ckey = E.canonical_composer(meta.composer)
    mel = lines.get("melody", ([], [], []))
    ps = str(path)
    if ps.startswith(str(RAW_DIR)):
        rel = str(path.relative_to(RAW_DIR))
    elif "/music21/corpus/" in ps:
        rel = "music21://" + ps.split("/music21/corpus/", 1)[1]
    else:
        rel = ps
    if index >= 0:
        rel = f"{rel}#{index}"
    return dict(
        source_id=source_id, path=rel,
        composer=comp, composer_key=ckey,
        title=meta.title, parent_title=meta.parent_title,
        opus=meta.opus, catalogue=meta.catalogue,
        movement=meta.movement, movement_number=meta.movement_number,
        music_key=meta.key, genre=meta.genre, date=meta.date,
        display=meta.display(),
        licence=meta.licence or src.licence,
        commercial_ok=int(src.commercial_ok if meta.commercial_ok is None else meta.commercial_ok),
        n_notes=len(sky[0]),
        skyline_pitches=_seq(sky[0]), skyline_onsets=_fseq(sky[1]),
        melody_pitches=_seq(mel[0]), melody_onsets=_fseq(mel[1]),
        themes=themes,
    )


_DCML_META_CACHE: dict[str, dict] = {}


def _dcml_metadata(repo: Path) -> dict:
    key = str(repo)
    if key not in _DCML_META_CACHE:
        rows: dict[str, dict] = {}
        mp = repo / "metadata.tsv"
        if mp.exists():
            import csv
            with open(mp, encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    rows[(row.get("piece") or "").strip()] = row
        _DCML_META_CACHE[key] = rows
    return _DCML_META_CACHE[key]


def _frac(x: str) -> float:
    x = (x or "").strip()
    if not x:
        return 0.0
    if "/" in x:
        a, _, b = x.partition("/")
        try:
            return float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(x)
    except ValueError:
        return 0.0


def _dcml_file(path: Path, src) -> tuple["E.WorkMeta", dict]:
    """Read a DCML notes TSV into (metadata, {line: (pitches, onsets, measures)})."""
    import csv
    repo = path.parent.parent
    piece = path.name[:-len(".notes.tsv")]
    md = _dcml_metadata(repo).get(piece, {})

    m = E.WorkMeta()
    m.composer = (md.get("composer") or "").strip()
    m.title = E._clean(md.get("workTitle") or piece)
    m.movement = E._clean(md.get("movementTitle") or "")
    m.movement_number = E._clean(md.get("movementNumber") or "")
    wn = E._clean(md.get("workNumber") or "")
    m.opus = E.norm_opus(wn) if wn.replace(".", "").isdigit() else (E.opus_from_text(wn) or E.opus_from_text(m.title))
    m.catalogue = E.catalogue_from_text(wn) or E.catalogue_from_text(m.title)
    m.key = E._clean(md.get("annotated_key") or "")
    m.date = E._clean(md.get("composed_end") or md.get("composed_start") or "")

    best_all: dict[float, tuple[int, int]] = {}
    best_top: dict[float, tuple[int, int]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            mid = row.get("midi")
            if not mid:
                continue
            try:
                midi = int(float(mid))
                off = round(_frac(row.get("quarterbeats", "")), 4)
                meas = int(float(row.get("mn") or -1))
                staff = int(float(row.get("staff") or 1))
            except (TypeError, ValueError):
                continue
            if not (0 <= midi <= 127):
                continue
            for tgt, ok in ((best_all, True), (best_top, staff == 1)):
                if not ok:
                    continue
                prev = tgt.get(off)
                if prev is None or midi > prev[0]:
                    tgt[off] = (midi, meas)

    def seq(d):
        offs = sorted(d)
        return [d[o][0] for o in offs], [float(o) for o in offs], [d[o][1] for o in offs]

    lines = {"skyline": seq(best_all)}
    if len(best_top) >= E.MIN_WORK_NOTES and len(best_top) < len(best_all):
        lines["melody"] = seq(best_top)
    return m, lines


def process_file(args) -> list[dict]:
    source_id, path_str = args
    src = SOURCES_BY_ID[source_id]
    path = Path(path_str)
    if src.fmt == "dcml":
        try:
            meta, lines = _dcml_file(path, src)
        except Exception as exc:  # noqa: BLE001
            return [{"error": f"dcml {type(exc).__name__}: {exc}"[:300], "path": path_str,
                     "source_id": source_id}]
        rec = _from_lines(lines, meta, src, source_id, path, -1)
        return [rec] if rec else [{"error": "no themes", "path": path_str, "source_id": source_id}]

    from music21 import converter

    try:
        obj = converter.parse(str(path))
    except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
        return [{"error": f"parse {type(exc).__name__}: {exc}"[:300], "path": path_str,
                 "source_id": source_id}]

    out: list[dict] = []
    for score, index in _scores_of(obj):
        try:
            if src.fmt == "kern":
                meta = E.meta_from_kern(path, src)
            elif src.fmt == "music21":
                meta = _m21_meta(score, path, src)
            elif src.fmt == "midi":
                meta = E.WorkMeta()
                rel = path.stem.replace("__", "/")
                h = _mutopia_meta().get(rel, {})
                meta.composer = h.get("composer", "") or h.get("mutopiacomposer", "")
                meta.title = E._clean(h.get("mutopiatitle", "") or h.get("title", "") or path.stem)
                op = str(h.get("mutopiaopus", ""))
                meta.opus = E.norm_opus(op) if op.isdigit() else (E.opus_from_text(op) or E.opus_from_text(meta.title))
                meta.catalogue = E.catalogue_from_text(op) or E.catalogue_from_text(meta.title)
                meta.genre = h.get("mutopiainstrument", "")
                meta.licence, meta.commercial_ok = _mutopia_licence(h)
            else:
                meta = E.meta_from_music21(score, path, src)
            if index >= 0:
                t = E._clean(str(getattr(score.metadata, "title", "") or ""))
                if t:
                    meta.parent_title = meta.parent_title or meta.title
                    meta.title = t
                else:
                    meta.title = f"{meta.title} [{index + 1}]"
            rec = _one_score(score, meta, src, source_id, path, index)
        except Exception as exc:  # noqa: BLE001
            out.append({"error": f"{type(exc).__name__}: {exc}"[:300],
                        "path": f"{path_str}#{index}", "source_id": source_id})
            continue
        if rec:
            out.append(rec)
    if not out:
        out.append({"error": "no themes", "path": path_str, "source_id": source_id})
    return out


# ------------------------------------------------------------ music21 corpus


def music21_core_files() -> list[Path]:
    from music21 import corpus
    out = []
    for p in corpus.getCorePaths():
        p = Path(str(p))
        if p.suffix.lower() in (".mxl", ".xml", ".musicxml", ".krn", ".abc"):
            out.append(p)
    return sorted(out)


def ingest_rism(con: sqlite3.Connection, src: Source, jsonl: Path) -> tuple[int, int]:
    """Load decoded RISM incipits straight into the DB (no music21 involved).

    One work per RISM source record; one theme per incipit.  Incipits are
    already short melodic fragments, so no windowing is applied and the work's
    "skyline" is simply its longest incipit.
    """
    if not jsonl.exists() and jsonl.with_suffix(jsonl.suffix + ".gz").exists():
        jsonl = jsonl.with_suffix(jsonl.suffix + ".gz")
    if not jsonl.exists():
        print(f"  [skip] rism: {jsonl} not present "
              f"(build it with `python -m fp30x_studio.idcorpus.rism`)", file=sys.stderr)
        return 0, 0

    n_works = n_themes = 0
    next_commit = 50000
    cur_id, buf, meta = None, [], None

    def flush():
        nonlocal n_works, n_themes, buf, meta
        if not buf or meta is None:
            buf = []
            return
        seen, themes = set(), []
        for r in buf:
            iv = tuple(r["pitches"][i + 1] - r["pitches"][i] for i in range(len(r["pitches"]) - 1))
            if iv in seen or len(set(r["pitches"])) < 3:
                continue
            seen.add(iv)
            themes.append((r, iv))
        if not themes:
            buf = []
            return
        longest = max(themes, key=lambda t: len(t[0]["pitches"]))[0]
        m = E.WorkMeta(composer=meta["composer"], title=meta["title"],
                       opus=meta["opus"], key=meta.get("key", ""),
                       genre=meta.get("genre", ""), date=meta.get("date", ""))
        comp, ckey = E.canonical_composer(m.composer)
        row = con.execute(
            """INSERT INTO works (source_id,path,composer,composer_key,title,parent_title,
                   opus,catalogue,movement,movement_number,music_key,genre,date,display,
                   licence,commercial_ok,n_notes,skyline_pitches,skyline_onsets,
                   melody_pitches,melody_onsets)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'','')""",
            (src.id, f"rism://{meta['rism_id']}", comp, ckey, m.title, "",
             m.opus, "", "", "", m.key, m.genre, m.date, m.display(),
             src.licence, 1, len(longest["pitches"]),
             _seq(longest["pitches"]), _fseq(longest["onsets"])))
        wid = row.lastrowid
        con.executemany(
            """INSERT INTO themes (work_id,source_id,line,kind,start_index,start_measure,n,
                   pitches,onsets,intervals) VALUES (?,?,'incipit',?,0,1,?,?,?,?)""",
            [(wid, src.id, "incipit" if i == 0 else "section", len(r["pitches"]),
              _seq(r["pitches"]), _fseq(r["onsets"]), _seq(iv))
             for i, (r, iv) in enumerate(themes)])
        n_works += 1
        n_themes += len(themes)
        buf = []

    opener = gzip.open if jsonl.suffix == ".gz" else open
    with opener(jsonl, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["rism_id"] != cur_id:
                flush()
                cur_id, meta = r["rism_id"], r
            buf.append(r)
            if n_works >= next_commit:
                next_commit = n_works + 50000
                con.commit()
    flush()
    con.commit()
    print(f"  rism: {n_works} works, {n_themes} incipits", flush=True)
    return n_works, n_themes


# --------------------------------------------------------------------- main


def _as_done(futures):
    from concurrent.futures import as_completed
    yield from as_completed(futures)


def collect_files(only: list[str] | None) -> list[tuple[str, str]]:
    jobs: list[tuple[str, str]] = []
    for src in SOURCES:
        if only and src.id not in only:
            continue
        if src.fmt == "rism":
            continue          # ingested separately, not file-by-file
        if src.id == "music21-core":
            files = music21_core_files()
        else:
            if not src.root.exists():
                print(f"  [skip] {src.id}: {src.root} not present", file=sys.stderr)
                continue
            files = src.files()
        jobs.extend((src.id, str(f)) for f in files)
    return jobs


def build(db_path: Path, only: list[str] | None = None, limit: int | None = None,
          workers: int | None = None, jsonl: Path | None = None) -> dict:
    jobs = collect_files(only)
    if limit:
        by_src: dict[str, int] = {}
        trimmed = []
        for sid, f in jobs:
            if by_src.get(sid, 0) >= limit:
                continue
            by_src[sid] = by_src.get(sid, 0) + 1
            trimmed.append((sid, f))
        jobs = trimmed
    # RISM is opt-in: 2M incipits belong in their own database file, not in the
    # curated one that other packages load row-by-row into memory.
    want_rism = bool(only) and "rism" in only
    print(f"{len(jobs)} files to parse across {len({j[0] for j in jobs})} sources"
          + (" (+ RISM incipits)" if want_rism else ""), flush=True)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    for ext in ("-wal", "-shm"):
        q = Path(str(db_path) + ext)
        if q.exists():
            q.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    captured = time.strftime("%Y-%m-%d")
    file_counts: dict[str, int] = {}
    for sid, _ in jobs:
        file_counts[sid] = file_counts.get(sid, 0) + 1
    if want_rism and RISM_JSONL.exists():
        file_counts["rism"] = 1
    for src in SOURCES:
        if src.id not in file_counts:
            continue
        con.execute(
            "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?,?,0,0,?)",
            (src.id, src.name, src.url, src.licence, src.spdx, int(src.commercial_ok),
             src.attribution, src.notes, file_counts[src.id], captured),
        )
    con.commit()

    errors: list[dict] = []
    n_works = n_themes = 0
    next_report = 250
    t0 = time.time()
    workers = workers or max(2, (os.cpu_count() or 4))
    jsonl_fh = open(jsonl, "w", encoding="utf-8") if jsonl else None

    # as_completed rather than map(): one pathologically slow file must not
    # stall progress reporting or balloon the result buffer behind it.
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_file, j): j for j in jobs}
        for i, fut in enumerate(_as_done(futures)):
          try:
              recs = fut.result()
          except Exception as exc:  # noqa: BLE001
              errors.append({"error": f"worker {type(exc).__name__}: {exc}"[:300],
                             "path": futures[fut][1], "source_id": futures[fut][0]})
              continue
          for rec in (recs or []):
            if "error" in rec:
                errors.append(rec)
                continue
            themes = rec.pop("themes")
            cur = con.execute(
                """INSERT INTO works (source_id,path,composer,composer_key,title,parent_title,
                       opus,catalogue,movement,movement_number,music_key,genre,date,display,
                       licence,commercial_ok,n_notes,skyline_pitches,skyline_onsets,
                       melody_pitches,melody_onsets)
                   VALUES (:source_id,:path,:composer,:composer_key,:title,:parent_title,
                       :opus,:catalogue,:movement,:movement_number,:music_key,:genre,:date,:display,
                       :licence,:commercial_ok,:n_notes,:skyline_pitches,:skyline_onsets,
                       :melody_pitches,:melody_onsets)""", rec)
            wid = cur.lastrowid
            con.executemany(
                """INSERT INTO themes (work_id,source_id,line,kind,start_index,start_measure,n,
                       pitches,onsets,intervals)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(wid, rec["source_id"], t["line"], t["kind"], t["start_index"],
                  t["start_measure"], t["n"], t["pitches"], t["onsets"], t["intervals"])
                 for t in themes])
            n_works += 1
            n_themes += len(themes)
            if jsonl_fh:
                jsonl_fh.write(json.dumps({**rec, "themes": themes}, ensure_ascii=False) + "\n")
            # A threshold, not `n_works % 250 == 0`: one multi-tune ABC file adds
            # three hundred works at once and steps straight over the modulo,
            # after which the build commits and reports nothing ever again.
            if n_works >= next_report:
                next_report = n_works + 250
                con.commit()
                print(f"  {i+1}/{len(jobs)} files | {n_works} works | {n_themes} themes "
                      f"| {time.time()-t0:.0f}s | {len(errors)} errors", flush=True)

    if jsonl_fh:
        jsonl_fh.close()
    con.commit()

    if want_rism:
        rw, rt = ingest_rism(con, SOURCES_BY_ID["rism"], RISM_JSONL)
        n_works += rw
        n_themes += rt

    con.execute("""UPDATE sources SET
                     work_count  = (SELECT COUNT(*) FROM works  WHERE works.source_id  = sources.id),
                     theme_count = (SELECT COUNT(*) FROM themes WHERE themes.source_id = sources.id)""")
    con.commit()
    con.execute("VACUUM")
    con.close()

    if errors:
        errp = db_path.parent / "build-errors.json"
        errp.write_text(json.dumps(errors, indent=1))
        print(f"{len(errors)} files failed -> {errp}", file=sys.stderr)
    print(f"DONE  works={n_works}  themes={n_themes}  errors={len(errors)}  "
          f"{time.time()-t0:.0f}s  db={db_path}", flush=True)
    return {"works": n_works, "themes": n_themes, "errors": len(errors), "files": len(jobs)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None, help="max files per source (smoke tests)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--jsonl", type=Path, default=None)
    ap.add_argument("--rism", action="store_true",
                    help="shorthand for --only rism --db <RISM_DB_PATH>")
    a = ap.parse_args(argv)
    db, only = a.db, a.only
    if a.rism:
        only = ["rism"]
        if db == DB_PATH:
            db = RISM_DB_PATH
    build(db, only, a.limit, a.workers, a.jsonl)


if __name__ == "__main__":
    main()
