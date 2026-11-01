"""Tests for :mod:`fp30x_studio.stumbles`, :mod:`~.motifs`, :mod:`~.slicekit`
and the block cutter in :mod:`~.sessionpage`.

Two halves, as elsewhere in this repo.

The synthetic half builds performances whose answer is known by construction: a
grab is a short strike followed by a held neighbour, so a performance with
exactly one of those must yield exactly one candidate, and excising it must
remove exactly one strike. The register filter is pinned the same way -- a line
with a bass note dropped into a rest must not report that drop as a melodic
interval.

The real half runs on the 2026-09-23 composing session and skips cleanly where
the take is absent. It pins the properties that must hold of any take (blocks
tile the playing, excision is conservative) rather than the specific counts,
which would change with a constant and teach nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fp30x_studio import motifs, sessionpage, slicekit, stumbles
from fp30x_studio.performance import KeyTrack, N_KEYS, Performance, Strike

TAKE = Path("~/Music/FP-30X Studio/takes/2026-09-23-compose-a.fp30").expanduser()


def perf_of(strikes, pedal=()):
    """A Performance from a flat list of strikes, one KeyTrack per key."""
    by = {}
    for s in strikes:
        by.setdefault(s.key, []).append(s)
    return Performance([KeyTrack(k, sorted(by.get(k, [])), check=False)
                        for k in range(N_KEYS)], pedal)


# -- synthetic: the shapes, by construction -------------------------------

def test_grab_is_found_and_only_the_wrong_note_is_removed():
    good = Strike(0.0, 0.8, 40, 70)          # a note held properly before
    bad = Strike(1.0, 1.04, 44, 60)          # 40 ms, abandoned
    fix = Strike(1.10, 1.70, 45, 72)         # the neighbour, held 600 ms
    p = perf_of([good, bad, fix])

    found = stumbles.find(p)
    grabs = [c for c in found if c.kind == "grab"]
    assert len(grabs) == 1
    assert grabs[0].strikes == (bad,)
    assert grabs[0].keeps == (fix,)

    cleaned = stumbles.excise(p, grabs)
    assert cleaned.n_strikes == p.n_strikes - 1
    assert set(cleaned.strikes()) == {good, fix}


def test_a_fast_run_of_held_notes_is_not_a_stumble():
    """Every note short but none abandoned: no correction shape, no candidate."""
    run = [Strike(i * 0.13, i * 0.13 + 0.12, 40 + i, 70) for i in range(8)]
    assert stumbles.find(perf_of(run)) == []


def test_excision_leaves_a_rest_and_never_shifts_what_follows():
    bad = Strike(1.0, 1.04, 44, 60)
    after = Strike(2.0, 2.5, 50, 70)
    p = perf_of([bad, after, Strike(1.1, 1.7, 45, 72)])
    cleaned = stumbles.excise(p, [c for c in stumbles.find(p) if c.removable])
    kept = [s for s in cleaned.strikes() if s.key == 50]
    assert kept == [after], "a later onset moved when a hole was closed"


def test_pedal_survives_excision():
    p = perf_of([Strike(1.0, 1.04, 44, 60), Strike(1.1, 1.7, 45, 72)],
                pedal=[(0.0, 5.0)])
    assert stumbles.excise(p, stumbles.find(p)).pedal == ((0.0, 5.0),)


def test_dissonance_alone_is_never_flagged():
    """He plays G A B-flat C-sharp D E-flat F-sharp. None of it is an error."""
    scale = [55, 57, 58, 61, 62, 63, 66]
    held = [Strike(i * 0.5, i * 0.5 + 0.45, n - 21, 70)
            for i, n in enumerate(scale)]
    assert stumbles.find(perf_of(held)) == []


def test_skyline_ignores_the_left_hand_showing_through_a_rest():
    line = [Strike(i * 0.3, i * 0.3 + 0.25, 60 - 21 + i, 70) for i in range(6)]
    bass = Strike(1.9, 2.3, 36 - 21, 70)      # two octaves below the melody
    got = [p for _, _, p in motifs.skyline(perf_of(line + [bass]))]
    assert 36 not in got, "the bass was admitted to the melodic line"


def test_a_transposed_repeat_counts_as_the_same_figure():
    shape = [0, 2, 4, 5, 7]
    strikes = []
    for j, base in enumerate((60, 67)):        # the same figure, a fifth apart
        for i, d in enumerate(shape):
            t = j * 10 + i * 0.4
            strikes.append(Strike(t, t + 0.35, base + d - 21, 70))
    found = motifs.find(perf_of(strikes), min_n=4)
    assert found, "no recurring figure found"
    top = found[0]
    assert len(top.occurrences) == 2
    assert len(top.transpositions()) == 2, "the transposition was not matched"


def test_window_chases_a_pedal_that_began_before_it():
    p = perf_of([Strike(10.0, 10.5, 40, 70)], pedal=[(2.0, 30.0)])
    _, pedal = slicekit.window(p, 9.0, 11.0)
    assert pedal and pedal[0][0] == pytest.approx(0.0), \
        "an excerpt opening mid-pedal would render dry"


def test_window_keeps_a_note_already_sounding_at_its_start():
    p = perf_of([Strike(5.0, 12.0, 40, 70)])
    strikes, _ = slicekit.window(p, 8.0, 10.0)
    assert len(strikes) == 1 and strikes[0].t_on == pytest.approx(0.0)


# -- real: the 2026-09-23 session ------------------------------------------

@pytest.fixture(scope="module")
def session():
    if not TAKE.exists():
        pytest.skip(f"{TAKE} not on this machine")
    return Performance.from_raw_capture(TAKE)


def test_blocks_are_short_and_in_order(session):
    bs = sessionpage.blocks(session)
    assert bs
    assert all(b - a <= sessionpage.BLOCK_MAX + 1e-6 for a, b in bs)
    assert all(bs[i][1] <= bs[i + 1][0] + 1e-9 for i in range(len(bs) - 1))


def test_blocks_cover_every_note(session):
    bs = sessionpage.blocks(session)
    for s in session.strikes():
        assert any(a - 1e-6 <= s.t_on <= b + 1e-6 for a, b in bs), \
            f"{s.name} at {s.t_on:.2f}s fell between blocks"


def test_excising_every_candidate_removes_under_two_percent(session):
    """A detector that would delete a tenth of a take is not a stumble detector."""
    cands = [c for c in stumbles.find(session) if c.removable]
    cleaned = stumbles.excise(session, cands)
    removed = session.n_strikes - cleaned.n_strikes
    assert removed == sum(len(c.strikes) for c in cands)
    assert removed / session.n_strikes < 0.02
