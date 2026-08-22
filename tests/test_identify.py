"""Tests for :mod:`fp30x_studio.identify`.

Three halves, which is one too many but honest.

**Synthetic.** Needs no piano. Pins the properties the whole design rests on:
that a chord arriving as a 5 ms arpeggio is one event, that an ornament is
collapsed to its principal, that a line committed in dribs is byte-identical to
the same line committed in one go (index stability -- without it the vote
accumulator is nonsense), that the hash is invariant under transposition and the
alignment invariant under tempo, and that the confidence gates actually refuse.

**Ground truth.** The two takes the brief names, replayed as streams, with the
note count each one needed asserted as a ceiling. These are the headline
numbers and they are meant to fail loudly if they get worse.

**Provenance.** One test asserts that the identification path calls no model and
opens no socket, because the entire point of the design is that it is
arithmetic. It is a grep, and a grep is exactly as strong as it looks; it is
here because the failure it guards against is somebody "improving" the matcher
by asking a model, and that would be visible in the import list.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from fp30x_studio.identify import (MIN_CONFIDENCE, STUB, Candidate, Clusterer,
                                   Event, Identifier, ListCorpus, Theme,
                                   ThemeIndex, Votes, collapse_trills,
                                   load_corpus, note_names, replay, score)
from fp30x_studio.identify.features import (CLUSTER_NS, Line, RegisterLine,
                                            ngrams_of)
from fp30x_studio.identify.identifier import replay_all
from fp30x_studio.identify.matcher import MIN_HITS, evidence
from fp30x_studio.identify import runner

MS = 1_000_000

TAKES = Path.home() / "Music" / "FP-30X Studio" / "takes"
JOPLIN = TAKES / "2026-08-17-piece.fp30"
SESSION = TAKES / "2026-08-17-session.fp30"
needs_takes = pytest.mark.skipif(
    not (JOPLIN.exists() and SESSION.exists()),
    reason="the 2026-08-17 takes are absent")


# -- note names --------------------------------------------------------------

def test_note_names_places_each_note_nearest_its_predecessor():
    assert note_names("C4 E4 G4") == (60, 64, 67)
    # B after C goes *down* a semitone, not up a major seventh.
    assert note_names("C4 B") == (60, 59)


def test_note_names_honours_accidentals_and_explicit_octaves():
    assert note_names("Bb3 F#5 C4") == (58, 78, 60)


def test_note_names_rejects_a_non_note():
    with pytest.raises(ValueError):
        note_names("H4")


# -- clustering --------------------------------------------------------------

def test_a_chord_arriving_as_an_arpeggio_is_one_event():
    """The measured shape: a struck chord reaches us as a ~5 ms spray."""
    c = Clusterer()
    onsets = [(i * 5 * MS, p) for i, p in enumerate((48, 55, 60, 64))]
    assert c.feed(onsets) == []          # nothing has closed it yet
    events = c.flush()
    assert len(events) == 1
    assert (events[0].lo, events[0].hi, events[0].n) == (48, 64, 4)


def test_notes_further_apart_than_the_window_are_separate_events():
    c = Clusterer()
    got = c.feed([(0, 60), (CLUSTER_NS + MS, 62), (2 * CLUSTER_NS + 2 * MS, 64)])
    assert [e.hi for e in got] == [60, 62]
    assert [e.hi for e in c.flush()] == [64]


# -- ornaments ---------------------------------------------------------------

def test_a_trill_collapses_to_its_principal():
    pitches = [60, 62, 60, 62, 60, 62, 60, 65]
    ns = [i * 60 * MS for i in range(len(pitches))]
    keep = collapse_trills(ns, pitches)
    assert [pitches[i] for i in keep] == [60, 65]


def test_a_four_note_alternation_is_melody_not_an_ornament():
    pitches = [60, 62, 60, 62, 67]
    ns = [i * 60 * MS for i in range(len(pitches))]
    assert collapse_trills(ns, pitches) == [0, 1, 2, 3, 4]


def test_an_alternation_too_slow_to_be_a_trill_is_left_alone():
    pitches = [60, 62, 60, 62, 60, 62, 60]
    ns = [i * 400 * MS for i in range(len(pitches))]   # 400 ms apart
    assert collapse_trills(ns, pitches) == list(range(len(pitches)))


def test_a_scale_is_not_a_trill_however_long():
    pitches = list(range(60, 72))
    ns = [i * 60 * MS for i in range(len(pitches))]
    assert collapse_trills(ns, pitches) == list(range(len(pitches)))


# -- the register filter -----------------------------------------------------

def test_the_register_line_drops_the_left_hand_showing_through():
    """A bass note between two melody notes must not enter the melody."""
    tune = [72, 74, 76, 74, 72, 71, 72, 74]
    events = [Event(i * 300 * MS, p, p) for i, p in enumerate(tune)]
    events.insert(5, Event(5 * 300 * MS - 100 * MS, 40, 40))
    mel, top = RegisterLine("mel"), Line("top", lambda e: e.hi)
    mel.feed(events, final=True)
    top.feed(events, final=True)
    assert 40 in top.pitches
    assert 40 not in mel.pitches
    assert mel.pitches == tune


# -- index stability, which the vote accumulator depends on ------------------

@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 50])
def test_a_line_committed_in_pieces_equals_the_line_committed_whole(chunk):
    pitches = [60, 62, 64, 65, 64, 65, 64, 65, 64, 65, 64, 67, 69, 71, 72, 71,
               69, 67, 65, 64, 62, 60]
    events = [Event(i * 200 * MS, p, p) for i, p in enumerate(pitches)]
    whole = Line("x", lambda e: e.hi)
    whole.feed(events, final=True)
    piecewise = Line("x", lambda e: e.hi)
    for i in range(0, len(events), chunk):
        piecewise.feed(events[i:i + chunk])
        # every position already committed must never change again
        assert whole.pitches[:len(piecewise.pitches)] == piecewise.pitches
    piecewise.feed([], final=True)
    assert piecewise.pitches == whole.pitches


def test_the_commit_lag_is_one_note_in_ordinary_texture():
    """A fixed lag would cost seconds of silence; the hold-back is minimal."""
    events = [Event(i * 300 * MS, 60 + i, 60 + i) for i in range(6)]
    line = Line("x", lambda e: e.hi)
    line.feed(events)
    assert len(line.pitches) == len(events) - 1


# -- hashing -----------------------------------------------------------------

def test_the_hash_is_invariant_under_transposition():
    tune = [67, 67, 65, 64, 62, 60, 59, 60, 62, 55]
    up = [p + 7 for p in tune]
    assert ngrams_of(tune, 4) == ngrams_of(up, 4)


def test_the_hash_ignores_time_entirely():
    """Tempo invariance is structural: no duration reaches the hash."""
    assert "ns" not in ngrams_of.__code__.co_varnames


def test_intervals_are_clamped_so_an_octave_displacement_cannot_dominate():
    grams = ngrams_of([60, 120, 60, 0, 60], 4)
    assert all(abs(d) <= 15 for _, g in grams for d in g)


# -- the index ---------------------------------------------------------------

def test_stop_hashes_are_dropped_once_the_corpus_is_big_enough():
    shared = tuple(range(60, 70))
    themes = [Theme(id=f"t{i}", composer="C", work=f"Work {i}", pitches=shared)
              for i in range(200)]
    idx = ThemeIndex(themes)
    assert idx.dropped > 0
    assert idx.n_hashes == 0     # every hash is in every theme; none survives


def test_a_small_corpus_keeps_its_hashes():
    """The floor exists so a ten-theme stub is not gutted by the same rule."""
    assert ThemeIndex(STUB).n_hashes > 100


def test_the_index_accepts_any_object_with_the_right_attributes():
    corpus = ListCorpus.of([
        {"id": "d", "composer": "X", "work": "W", "pitches": [60, 62, 64, 65, 67]},
    ])
    idx = ThemeIndex(corpus)
    assert idx.themes[0].label == "X, W"
    assert len(idx.postings) == 1


# -- voting ------------------------------------------------------------------

def _index_of_one(pitches):
    return ThemeIndex([Theme(id="t", composer="C", work="W", pitches=tuple(pitches))])


def test_ranking_is_skipped_entirely_until_something_could_win():
    """The gate that keeps a tick O(new n-grams) instead of O(take so far)."""
    idx = _index_of_one([60, 62, 64, 65, 67, 65, 64, 62, 60])
    v = Votes(idx)
    v.add([60, 62, 64, 65, 67])
    assert not v.worth_ranking
    assert v.best() == (None, 0)
    assert v.best(force=True)[0] is not None


def test_votes_added_incrementally_equal_votes_added_at_once():
    tune = [60, 62, 64, 65, 67, 65, 64, 62, 60, 59, 60, 62]
    idx = _index_of_one(tune)
    whole = Votes(idx)
    whole.add(tune)
    piece = Votes(idx)
    for k in range(1, len(tune) + 1):
        piece.add(tune[:k])
    assert piece.cells == whole.cells


def test_a_matching_phrase_piles_onto_one_diagonal():
    tune = [60, 62, 64, 65, 67, 65, 64, 62, 60]
    idx = _index_of_one(tune)
    v = Votes(idx)
    v.add([50, 50] + [p + 7 for p in tune])     # transposed, two notes late
    c, _ = v.best(force=True)
    assert abs(c.diagonal + 2) <= 1          # the slack window straddles it
    assert c.hits == len(ngrams_of(tune, 4))


def test_the_same_fragment_played_twice_far_apart_does_not_add_up():
    """Two hits at two different diagonals are two coincidences, not a match."""
    idx = _index_of_one([60, 62, 64, 65, 67, 65, 64, 62, 60])
    v = Votes(idx)
    v.add([60, 62, 64, 65, 67] + [80] * 40 + [60, 62, 64, 65, 67])
    c, _ = v.best(force=True)
    assert c.hits == 1
    assert score(c) == 0.0





# -- scoring gates -----------------------------------------------------------

def _cand(hits, span, second=0):
    return Candidate(theme=STUB.entries[0], hits=hits, diagonal=0, q_first=0,
                     q_last=span - 1, runner_up_hits=second)


def test_hits_smeared_across_the_query_are_refused_however_many_there_are():
    sparse = _cand(hits=12, span=90)
    assert sparse.density < 0.34
    assert score(sparse) == 0.0


def test_too_few_hits_is_never_an_identification():
    assert score(_cand(MIN_HITS - 1, MIN_HITS - 1)) == 0.0


def test_a_dense_clear_run_clears_the_bar():
    assert score(_cand(MIN_HITS, MIN_HITS)) >= MIN_CONFIDENCE


def test_a_tie_is_reported_as_no_answer():
    """Two arrangements of one tune must produce silence, not a coin toss."""
    assert score(_cand(12, 12, second=12)) == 0.0


def test_evidence_saturates_rather_than_growing_without_bound():
    assert evidence(2) == 0.0
    assert 0.0 < evidence(6) < evidence(12) < 1.0
    assert evidence(40) - evidence(30) < 0.01


def test_a_rival_too_small_to_be_an_answer_costs_the_winner_nothing():
    """Five aligned hits could never be reported, so it is not competition."""
    assert score(_cand(10, 10, MIN_HITS - 1)) == score(_cand(10, 10, 0))


def test_confidence_falls_as_a_real_rival_closes():
    assert score(_cand(12, 12, 0)) > score(_cand(12, 12, 8)) > score(_cand(12, 12, 11))


# -- end to end, synthetic ---------------------------------------------------

def _play(pitches, *, ns0=0, step_ns=250 * MS, transpose=0, doubled=False):
    """A stream of note-ons, optionally octave-doubled the way the rag is."""
    out = []
    for i, p in enumerate(pitches):
        t = ns0 + i * step_ns
        if doubled:
            out.append((t, p + transpose - 12))
            t += 5 * MS
        out.append((t, p + transpose))
    return out


def test_a_transposed_and_slowed_performance_is_still_identified():
    theme = STUB.entries[0]                      # the Joplin
    tune = list(theme.pitches) + [69, 67, 65, 64]
    ident = Identifier(STUB)
    v = ident.feed(_play(tune, transpose=+7, step_ns=600 * MS, doubled=True))
    assert v is not None and v.theme_id == theme.id


def test_a_piece_the_corpus_has_never_seen_gets_silence():
    ident = Identifier(STUB)
    tune = [60, 66, 71, 55, 68, 61, 74, 58, 63, 70, 54, 67, 72, 59, 65]
    assert ident.feed(_play(tune)) is None
    assert ident.finish() is None


def test_a_long_silence_starts_a_new_piece_on_fresh_evidence():
    ident = Identifier(STUB)
    ident.feed(_play(list(STUB.entries[0].pitches)))
    before = len(ident._segments)
    ident.feed(_play([60, 62, 64], ns0=10_000_000_000 + 20 * 250 * MS))
    assert len(ident._segments) == before + 1


def test_the_verdict_line_is_purple_and_names_what_it_needed():
    ident = Identifier(STUB)
    v = ident.feed(_play(list(STUB.entries[0].pitches) + [69, 67, 65, 64]))
    assert v is not None
    text = v.line_text()
    assert "\033[95m" in text and "\U0001f7e3" in text
    assert f"{v.notes} notes" in text
    assert "IDENTIFIED" in v.line_text(colour=False)


# -- the corpus seam ---------------------------------------------------------

def test_load_corpus_falls_back_to_the_stub_when_the_database_is_absent():
    """Absent, half-built or raising, it must still run. Failing at the piano
    is the only failure that actually costs anything."""
    c = load_corpus(path="/nonexistent/themes.sqlite")
    assert list(c.themes()) == STUB.entries


def test_every_stub_theme_says_where_it_came_from():
    for t in STUB.themes():
        assert t.source, f"{t.id} has no provenance"


def test_merging_two_corpora_keeps_the_first_of_each_id():
    a = ListCorpus.of([{"id": "x", "pitches": [60, 62, 64, 65, 67], "work": "A"}])
    b = ListCorpus.of([{"id": "x", "pitches": [1, 2, 3, 4, 5], "work": "B"},
                       {"id": "y", "pitches": [60, 61, 62, 63, 64], "work": "C"}])
    merged = a + b
    assert [t.id for t in merged.themes()] == ["x", "y"]
    assert merged.entries[0].work == "A"


# -- ground truth ------------------------------------------------------------

@needs_takes
def test_the_joplin_is_identified_from_a_stream_prefix():
    """*The Strenuous Life*, from the opening, against the stub corpus."""
    v, _ = replay(JOPLIN, STUB, chunk=2)
    assert v is not None
    assert v.theme_id == "joplin-strenuous-life"
    assert v.composer == "Scott Joplin"
    assert v.confidence >= MIN_CONFIDENCE
    assert v.notes <= 40, f"needed {v.notes} notes; it used to need 26"


@needs_takes
def test_the_chopin_segment_is_not_identified_from_the_stub_corpus():
    """The Chopin is *not* named, and that is the correct result.

    Until 2026-08-20 this asserted the opposite. The theme it matched had been
    derived from this very take, so a pass measured recall of one performance
    rather than identification of a piece -- data leakage, and the figure it
    produced (35 notes) was quoted for weeks as though it meant something.

    The stub is gone. The corpus now carries Op. 62 No. 1 from the Chopin
    Institute's scores, 85 indexed windows, and against those the segment
    produces no verdict at any length: the performance interpolates a note into
    the theme, and the matcher votes on 3-grams of intervals, so one insertion
    reframes every gram downstream.

    Asserting the silence keeps the finding from being undone by accident.
    """
    verdicts, _ = replay_all(SESSION, STUB, chunk=2)
    by_segment = {v.segment: v for v in verdicts}
    assert 3 not in by_segment, (
        f"segment 3 was identified as {by_segment[3].theme_id!r} -- if a theme "
        "derived from this take has re-entered the corpus, that is the bug")


@needs_takes
def test_every_piece_in_the_session_is_told_apart_from_every_other():
    verdicts, _ = replay_all(SESSION, STUB, chunk=2)
    got = {v.segment: v.theme_id for v in verdicts}
    # Segment 3 is absent by design: its theme was removed as circular (see
    # test_the_chopin_segment_is_not_identified_from_the_stub_corpus). The
    # discrimination claim is unaffected -- what it tests is that no segment
    # answers with another segment's theme.
    assert got == {
        0: "session-2026-08-17-seg0",
        1: "session-2026-08-17-seg1",
        2: "session-2026-08-17-seg2",
        4: "session-2026-08-17-seg4",
    }


@needs_takes
def test_removing_the_answer_from_the_corpus_produces_silence_not_a_guess():
    """The test that matters most: it must not reach for the nearest thing."""
    without = ListCorpus([t for t in STUB.entries
                          if t.id != "joplin-strenuous-life"])
    v, _ = replay(JOPLIN, without, chunk=2)
    assert v is None


@needs_takes
def test_a_tick_costs_about_a_millisecond_and_does_not_grow():
    """Incrementality, measured rather than asserted in a docstring.

    The 44-minute take is fed in the same tick sizes as the 6-minute one. If
    anything in the chain were re-deriving from the top of the take, the long
    take's later ticks would cost more; they do not.
    """
    ident = Identifier(STUB)
    ident.attach(SESSION)
    ident.store.ingest()
    rows = ident.store.db.execute(
        "SELECT ns, d1 FROM message WHERE kind = 'note_on' AND d2 > 0 "
        "ORDER BY seq").fetchall()
    onsets = [(r[0], r[1]) for r in rows]
    import time as _t
    costs = []
    for i in range(0, len(onsets), 12):
        t = _t.process_time()
        ident.feed(onsets[i:i + 12])
        costs.append(_t.process_time() - t)
    ident.close()
    first = sum(costs[:len(costs) // 4])
    last = sum(costs[-len(costs) // 4:])
    assert last < 4 * first + 0.05, (
        f"late ticks cost {last:.3f} s against {first:.3f} s early: "
        "something is re-deriving from the start of the take")


# -- against the real corpus -------------------------------------------------
#
# The tests above run on the stub, so they pin the algorithm without depending
# on a database another workstream owns and is still filling. These run on that
# database, and they are the ones that answer the actual question: can it name
# the piece out of tens of thousands of themes it has never been tuned against?

CORPUS_DB = Path.home() / "workspace" / "audio" / "corpus" / "themes.sqlite"
needs_corpus = pytest.mark.skipif(
    not CORPUS_DB.exists(), reason="the theme corpus has not been built")


@pytest.fixture(scope="module")
def real_index():
    if not CORPUS_DB.exists():
        pytest.skip("the theme corpus has not been built")
    return ThemeIndex(load_corpus())


@needs_corpus
@needs_takes
def test_the_joplin_is_named_out_of_the_whole_corpus(real_index):
    """*The Strenuous Life*, against every theme in the database.

    Unlike the stub test this is not circular in any degree: the corpus entry
    came from a score, not from Jake's playing.
    """
    if not any("Strenuous Life" in t.work and t.id.startswith("idc-")
               for t in real_index.themes):
        pytest.skip("the corpus build has not reached the Joplin yet")
    v, _ = replay(JOPLIN, index=real_index, chunk=2)
    assert v is not None
    assert v.composer == "Scott Joplin"
    assert "Strenuous Life" in v.work
    assert v.notes <= 60, f"needed {v.notes} notes"


@needs_corpus
@needs_takes
def test_the_chopin_nocturne_is_not_named_even_against_the_real_corpus(real_index):
    """Op. 62 No. 1 is in the corpus from scores, and still is not matched.

    85 windows of it are indexed from the Chopin Institute's editions, so this
    is not a coverage gap. The performance opens with an extra chord tone
    against the score's line, and interval 3-grams do not survive an insertion:
    5 of 13 carry through, which is under the confidence gate.

    It does not fall silent, which is worse. It answers *wrongly*: the segment
    comes back as Anonymous, "HOERT DIE LERCHE SIE SINGT", Op. 150, on 6 hits
    at margin 0.333 -- the lowest margin any verdict in this take produces, and
    still above the gate. So the failure mode here is a false positive, not a
    refusal, and `test_removing_the_answer_from_the_corpus_produces_silence_
    not_a_guess` does not generalise to a piece the matcher merely fails to
    align with.

    The honest headline number for this project is therefore the Joplin, whose
    corpus entry came from a score. This test exists so that stays true.
    """
    verdicts, _ = replay_all(SESSION, index=real_index, chunk=2)
    v = {x.segment: x for x in verdicts}
    if 3 in v:
        assert "Chopin" not in (v[3].composer or ""), (
            "the Chopin is being identified again -- check whether a theme "
            "derived from this take has re-entered the corpus")
        assert v[3].margin <= 0.4, (
            f"the wrong answer {v[3].work!r} is now arriving at margin "
            f"{v[3].margin} -- a confident false positive is a regression")


@needs_corpus
def test_the_corpus_is_grouped_by_answer_not_by_row(real_index):
    """Several rows per piece must not compete with each other.

    Found the hard way: with row identity as the key, the corpus's several
    entries for *The Strenuous Life* each vetoed the others and the correct
    answer came back at confidence 0.0005.
    """
    assert len(set(real_index.groups)) < len(real_index.themes)


# -- the live path -----------------------------------------------------------

FIXTURE_HEADER = (
    "# fp30x-capture v1\n# columns abs_ns hex_bytes\n"
    "# mach_timebase_numer 125\n# mach_timebase_denom 3\n"
    "# anchor_mach_ns 1000000000\n# anchor_unix_ns 1786981500000000000\n"
    "# started_utc 2026-08-19T10:00:00Z\n# source FP-30X Bluetooth\n"
    "# sources_connected 1\n")


def _fp30(pitches, *, step_ns=250 * MS) -> str:
    out = [FIXTURE_HEADER]
    ns = 1_000_000_000
    for p in pitches:
        out.append(f"{ns} 90 {p:02X} 50\n")
        out.append(f"{ns + 120 * MS} 80 {p:02X} 40\n")
        ns += step_ns
    return "".join(out)


def test_the_live_path_identifies_from_a_file_that_is_still_growing(tmp_path):
    """What the runner actually does: stat, ingest the new bytes, decide."""
    tune = list(STUB.entries[0].pitches) + [69, 67, 65, 64]
    text = _fp30(tune)
    take = tmp_path / "live.fp30"
    take.write_text(FIXTURE_HEADER)
    ident = Identifier(STUB)
    said = None
    body = text[len(FIXTURE_HEADER):].splitlines(keepends=True)
    for i in range(0, len(body), 4):
        with take.open("a") as fh:
            fh.write("".join(body[i:i + 4]))
        said = said or ident.update(take)
    ident.close()
    assert said is not None and said.theme_id == STUB.entries[0].id


def test_a_take_replaced_under_the_runner_does_not_resume_into_it(tmp_path):
    """The capture tool rotating its file must not corrupt the caches."""
    take = tmp_path / "live.fp30"
    take.write_text(_fp30([60, 62, 64, 65, 67, 69]))
    ident = Identifier(STUB)
    ident.update(take)
    assert ident.notes_seen == 6
    take.write_text(_fp30([72, 71, 69, 67]))       # a different file entirely
    ident.update(take)
    assert ident.notes_seen == 4
    ident.close()


# -- the runner --------------------------------------------------------------

def test_the_runner_finds_the_newest_take(tmp_path):
    import os

    old, new = tmp_path / "a.fp30", tmp_path / "b.fp30"
    old.write_bytes(b"# fp30x-capture v1\n")
    new.write_bytes(b"# fp30x-capture v1\n")
    os.utime(old, (1, 1))
    assert runner.newest_take(tmp_path) == new


def test_the_runner_says_nothing_at_all_when_there_is_nothing_to_say(tmp_path):
    out = io.StringIO()
    said = runner.watch(None, directory=tmp_path, every=0.0, limit=0.0,
                        corpus=STUB, out=out)
    assert said == 0
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1 and "watching" in lines[0]


def test_the_cli_reports_the_corpus(capsys):
    assert runner.main(["corpus", "--stub", "--sources"]) == 0
    out = capsys.readouterr().out
    assert "themes" in out and "joplin-strenuous-life" in out


# -- provenance --------------------------------------------------------------

def test_no_model_and_no_network_anywhere_in_the_identification_path():
    """The identification is arithmetic. Keep it that way."""
    banned = re.compile(
        r"\b(anthropic|openai|requests|urllib|httpx|socket|aiohttp)\b")
    pkg = Path(__file__).resolve().parents[1] / "fp30x_studio" / "identify"
    offenders = []
    for py in sorted(pkg.glob("*.py")):
        for i, ln in enumerate(py.read_text().splitlines(), 1):
            if ln.lstrip().startswith(("import ", "from ")) and banned.search(ln):
                offenders.append(f"{py.name}:{i}: {ln.strip()}")
    assert not offenders, offenders
