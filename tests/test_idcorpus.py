"""Tests for :mod:`fp30x_studio.idcorpus`, the theme corpus behind identification.

Nothing here touches the network or the ~2 GB of downloaded corpora: the
metadata normalizers and the Plaine & Easie decoder are pure functions, and the
index is exercised against a database built in a temp directory from three
hand-written works.  If the real database happens to be present, one extra test
asserts the two pieces the brief actually names are in it -- and skips, loudly,
if it is not.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fp30x_studio.idcorpus import ThemeCorpus
from fp30x_studio.idcorpus import build as B
from fp30x_studio.idcorpus import extract as E
from fp30x_studio.idcorpus import pae
from fp30x_studio.idcorpus.sources import DB_PATH, SOURCES


# --------------------------------------------------------------- licence data

def test_every_source_states_a_licence_and_a_commercial_verdict():
    for s in SOURCES:
        assert s.licence.strip(), f"{s.id} has no licence string"
        assert isinstance(s.commercial_ok, bool)
        assert s.url.strip(), f"{s.id} has no source URL"


def test_unlicensed_and_noncommercial_sources_are_not_marked_commercial_ok():
    """The one invariant that must never regress by accident."""
    for s in SOURCES:
        low = s.licence.lower()
        if "noncommercial" in low or "nc-" in low or "no licence stated" in low:
            assert not s.commercial_ok, f"{s.id} is marked commercial_ok but is not"


# ------------------------------------------------------------ opus / metadata

@pytest.mark.parametrize("ops,onm,want", [
    ("2", "1", "Op. 2 No. 1"),
    ("Op. 62", "No. 1", "Op. 62 No. 1"),
    ("28", "", "Op. 28"),
    ("", "", ""),
    ("posth.", "2", "posth No. 2"),
])
def test_norm_opus(ops, onm, want):
    assert E.norm_opus(ops, onm) == want


@pytest.mark.parametrize("text,want", [
    ("Prelude in C Minor, Op. 28, No. 1", "Op. 28 No. 1"),
    ("String Quartet No.1, Op.7 (Sz.40)", "Op. 7"),
    ("Nocturne in B major op. 62, no. 1", "Op. 62 No. 1"),
    ("The Entertainer", ""),
])
def test_opus_from_text(text, want):
    assert E.opus_from_text(text) == want


@pytest.mark.parametrize("text,want", [
    ("Aus meines Herzens Grunde, BWV 269", "BWV 269"),
    ("Sonata No. 12 in A major, Hob. XVI:12", "Hob. XVI:12"),
    ("Vier Gesange aus 'Wilhelm Meister' (D.877)", "D.877"),
    ("The Strenuous Life", ""),
])
def test_catalogue_from_text(text, want):
    assert E.catalogue_from_text(text) == want


@pytest.mark.parametrize("raw,name,key", [
    ("F. Chopin", "Fryderyk Chopin", "chopin"),
    ("Chopin, Frederic", "Fryderyk Chopin", "chopin"),
    ("Joplin, Scott", "Scott Joplin", "joplin"),
    ("Beethoven, Ludwig van", "Ludwig van Beethoven", "beethoven"),
    ("Franz Abt (1819-1885)", "Franz Abt", "abt"),
])
def test_canonical_composer(raw, name, key):
    assert E.canonical_composer(raw) == (name, key)


def test_display_does_not_repeat_the_opus_already_in_the_title():
    m = E.WorkMeta(title="Prelude in C Minor, Op. 28, No. 1", opus="Op. 28 No. 1",
                   movement="Agitato")
    assert m.display() == "Prelude in C Minor, Op. 28, No. 1 - Agitato"


# ------------------------------------------------------------ Plaine & Easie

def test_pae_middle_c_and_octave_marks():
    p, _ = pae.decode("4,C'C''C'''C")
    assert p == [48, 60, 72, 84]


def test_pae_key_signature_applies():
    flat, _ = pae.decode("4'B", "bBEA")
    plain, _ = pae.decode("4'B", "")
    assert plain[0] - flat[0] == 1


def test_pae_accidental_is_local_to_the_measure():
    p, _ = pae.decode("4'CxCC/C")
    assert p == [60, 61, 61, 60]


def test_pae_durations_and_dots_drive_onsets():
    p, o = pae.decode("2'C4D8E")
    assert p == [60, 62, 64]
    assert o == [0.0, 2.0, 3.0]
    _, od = pae.decode("2.'C4D")
    assert od[1] == 3.0


def test_pae_measure_rest_count_is_not_read_as_durations():
    _, o = pae.decode("=12/4'CD")
    assert o == [0.0, 1.0]


def test_pae_tolerates_junk():
    p, _ = pae.decode("4'C{}()!@#$%~D")
    assert p == [60, 62]


# ------------------------------------------------------------------- windows

def _ramp(n):
    return list(range(60, 60 + n)), [float(i) for i in range(n)], [1 + i // 4 for i in range(n)]


def test_make_themes_yields_an_incipit_first():
    themes = E.make_themes(*_ramp(64))
    assert themes
    assert themes[0].kind == "incipit"
    assert themes[0].start_index == 0
    assert len(themes[0].pitches) == E.THEME_LEN


def test_make_themes_deduplicates_identical_interval_signatures():
    pitches = [60, 62, 64, 65] * 20
    onsets = [float(i) for i in range(len(pitches))]
    measures = [1 + i // 4 for i in range(len(pitches))]
    sigs = [t.intervals() for t in E.make_themes(pitches, onsets, measures)]
    assert len(sigs) == len(set(sigs))


def test_make_themes_declines_a_stream_too_short_to_be_a_theme():
    assert E.make_themes([60, 62], [0.0, 1.0], [1, 1]) == []


# --------------------------------------------------------------------- index

@pytest.fixture
def tiny_db(tmp_path: Path) -> Path:
    db = tmp_path / "tiny.sqlite"
    con = sqlite3.connect(db)
    con.executescript(B.SCHEMA)
    con.execute("INSERT INTO sources VALUES ('s1','Permissive Corpus','http://x',"
                "'CC BY 4.0','CC-BY-4.0',1,'','',1,0,0,'2026-08-19')")
    con.execute("INSERT INTO sources VALUES ('s2','Restricted Corpus','http://y',"
                "'CC BY-NC-SA 4.0','CC-BY-NC-SA-4.0',0,'','',1,0,0,'2026-08-19')")
    works = [
        ("s1", "Fryderyk Chopin", "chopin", "Nocturne", "Op. 62 No. 1", 1,
         [76, 73, 71, 70, 68, 66, 64, 63, 61, 59, 58, 56]),
        ("s2", "Scott Joplin", "joplin", "The Entertainer", "", 0,
         [86, 88, 84, 81, 81, 83, 79, 74, 76, 72, 71, 69]),
        ("s1", "Johann Sebastian Bach", "bach", "Invention", "BWV 772", 1,
         [60, 62, 64, 65, 67, 65, 64, 62, 60, 67, 72, 71]),
    ]
    for i, (src, comp, ck, title, opus, comm, pitches) in enumerate(works, 1):
        ivs = [pitches[j + 1] - pitches[j] for j in range(len(pitches) - 1)]
        onsets = ",".join(str(float(j)) for j in range(len(pitches)))
        con.execute(
            "INSERT INTO works (id,source_id,path,composer,composer_key,title,parent_title,"
            "opus,catalogue,movement,movement_number,music_key,genre,date,display,licence,"
            "commercial_ok,n_notes,skyline_pitches,skyline_onsets,melody_pitches,melody_onsets)"
            " VALUES (" + ",".join("?" * 22) + ")",
            (i, src, f"p{i}", comp, ck, title, "", opus, "", "", "", "", "", "",
             title, "", comm, len(pitches), ",".join(map(str, pitches)), onsets, "", ""))
        con.execute(
            "INSERT INTO themes (work_id,source_id,line,kind,start_index,start_measure,n,"
            "pitches,onsets,intervals) VALUES (?,?,'skyline','incipit',0,1,?,?,?,?)",
            (i, src, len(pitches), ",".join(map(str, pitches)),
             ",".join(str(float(j)) for j in range(len(pitches))), ",".join(map(str, ivs))))
    con.commit()
    con.close()
    return db


def test_index_finds_the_right_work(tiny_db):
    tc = ThemeCorpus(tiny_db, cache=False)
    hits = tc.search_works([76, 73, 71, 70, 68, 66, 64, 63, 61])
    assert hits
    assert hits[0].record.composer == "Fryderyk Chopin"
    assert hits[0].record.opus == "Op. 62 No. 1"


def test_index_is_transposition_invariant(tiny_db):
    tc = ThemeCorpus(tiny_db, cache=False)
    up = [p + 7 for p in (76, 73, 71, 70, 68, 66, 64, 63, 61)]
    assert tc.search_works(up)[0].record.composer == "Fryderyk Chopin"


def test_commercial_only_excludes_restricted_sources(tiny_db):
    strict = ThemeCorpus(tiny_db, commercial_only=True, cache=False)
    assert strict.n_themes == 2
    assert strict.search_works([86, 88, 84, 81, 81, 83, 79, 74, 76]) == []
    loose = ThemeCorpus(tiny_db, cache=False)
    assert loose.search_works([86, 88, 84, 81, 81, 83, 79, 74, 76])[0].record.composer \
        == "Scott Joplin"


def test_theme_record_exposes_numeric_arrays(tiny_db):
    tc = ThemeCorpus(tiny_db, cache=False)
    rec = tc.search_works([60, 62, 64, 65, 67, 65, 64, 62, 60])[0].record
    assert rec.pitches.tolist()[:4] == [60, 62, 64, 65]
    assert len(rec.onsets) == len(rec.pitches)
    assert len(rec.durations) == len(rec.pitches)
    assert rec.intervals.tolist()[:3] == [2, 2, 1]


def test_ngram_cache_round_trips(tmp_path, tiny_db):
    a = ThemeCorpus(tiny_db, cache=True)
    b = ThemeCorpus(tiny_db, cache=True)
    assert a.n_themes == b.n_themes
    assert (a._hash == b._hash).all()


def test_stats_reports_what_is_loaded(tiny_db):
    st = ThemeCorpus(tiny_db, cache=False).stats()
    assert st["works"] == 3 and st["themes"] == 3
    assert st["themes_commercial_ok"] == 2
    assert st["ngrams_indexed"] > 0


# --------------------------------------------------- the real corpus, if built

@pytest.mark.skipif(not DB_PATH.exists(), reason="corpus not built on this machine")
def test_the_two_confirmed_pieces_are_in_the_real_corpus():
    tc = ThemeCorpus(DB_PATH)
    assert tc.find("Chopin"), "no Chopin in the corpus"
    assert any("62" in (r["opus"] or "") or "62" in (r["display"] or "")
               for r in tc.find("Chopin", limit=2000)), "Chopin Op. 62 missing"
    assert any("strenuous" in (r["display"] or "").lower()
               for r in tc.find("Strenuous", limit=50)), "Joplin, The Strenuous Life missing"
