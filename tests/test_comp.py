"""Tests for :mod:`fp30x_studio.comp` — choosing a take per bar, and splicing.

The synthetic half builds bar maps whose answer is known by construction: a
trajectory that revisits bars (a rag is played AABBACCDD, so bar 5 happens
three times) must yield candidates that are each one real pass through the
music and never a splice of two visits to it.

The real half runs on the seven located passes of *Something Doing* and pins
the properties any comp must have — every group offers at least one candidate,
candidates for one group agree on roughly how long that music takes, and an
assembled selection contains each chosen note exactly once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fp30x_studio import comp
from fp30x_studio.performance import KeyTrack, N_KEYS, Performance, Strike

MAPS = Path("~/Music/FP-30X Studio/sessions/something-doing/measuremaps").expanduser()
CLIPS = Path("~/Music/FP-30X Studio/takes/clips").expanduser()


def perf_of(strikes, pedal=()):
    by = {}
    for s in strikes:
        by.setdefault(s.key, []).append(s)
    return Performance([KeyTrack(k, sorted(by.get(k, [])), check=False)
                        for k in range(N_KEYS)], pedal)


# -- synthetic --------------------------------------------------------------

def test_trajectory_orders_a_repeated_section_by_time():
    bars = {"1": [0.0, 10.0], "2": [2.0, 12.0], "3": [4.0]}
    assert comp.trajectory(bars) == [
        (0.0, 1), (2.0, 2), (4.0, 3), (10.0, 1), (12.0, 2)]


def test_an_occurrence_is_one_pass_through_the_bars():
    """AABA: bars 1-2 happen twice and must come back as two candidates,
    never as one window spanning both visits."""
    traj = comp.trajectory({"1": [0.0, 20.0], "2": [2.0, 22.0],
                            "3": [4.0], "4": [6.0]})
    occ = comp.occurrences(traj, [1, 2], min_fraction=1.0)
    assert len(occ) == 2
    (a0, a1, _), (b0, b1, _) = occ
    assert a0 == 0.0 and a1 <= 4.0, "the first visit swallowed the second"
    assert b0 == 20.0


def test_a_hesitation_does_not_stretch_the_clip():
    """Ending at 'the next event' made a four-bar group 16 s long in
    2026-09-19-joplin-b-5, where the player waits 6 s before the next bar."""
    traj = comp.trajectory({"1": [0.0], "2": [2.0], "3": [4.0], "4": [6.0],
                            "5": [20.0]})
    (t0, t1, _), = comp.occurrences(traj, [1, 2, 3, 4])
    assert t1 - t0 == pytest.approx(8.0), "the 14 s hesitation was included"


def test_an_occurrence_missing_its_last_bar_is_not_offered():
    traj = comp.trajectory({"1": [0.0], "2": [2.0], "3": [4.0]})
    assert comp.occurrences(traj, [1, 2, 3, 4]) == []


def test_a_seam_lands_between_onsets_not_on_one():
    p = perf_of([Strike(9.6, 10.4, 40, 70), Strike(10.4, 11.0, 42, 70)])
    t = comp.seams(p, 10.0)
    assert t is not None
    assert all(abs(t - s.t_on) > 0.01 for s in p.strikes())


def test_a_seam_ignores_the_pedal():
    """A rag is pedalled continuously. Requiring the damper up found an
    admissible seam at 2 of 40 boundaries and the search bought nothing."""
    p = perf_of([Strike(9.0, 9.2, 40, 70)], pedal=[(0.0, 60.0)])
    assert comp.seams(p, 10.0) is not None


def test_a_held_note_rings_past_the_seam_instead_of_being_cut():
    held = Strike(0.0, 9.0, 40, 70)          # still sounding at the cut
    c = comp.Candidate("p", 0, (1,), 0.0, 5.0, 1)
    strikes, _ = comp.assemble([(c, perf_of([held]))], tail=2.0)
    assert len(strikes) == 1
    # It sounds from its own onset until tail seconds past the cut: 5 + 2, and
    # the seam search may have nudged the window, so the invariant is the
    # duration and not the absolute time.
    assert strikes[0].duration == pytest.approx(7.0), "the tail was truncated at the seam"


def test_a_note_belongs_to_the_group_its_onset_falls_in():
    early = Strike(1.0, 2.0, 40, 70)
    late = Strike(6.0, 7.0, 42, 70)
    p = perf_of([early, late])
    c = comp.Candidate("p", 0, (1,), 0.0, 5.0, 1)
    strikes, _ = comp.assemble([(c, p)], tail=2.0)
    assert [s.key for s in strikes] == [40], "a note from the next group came along"


def test_groups_are_laid_end_to_end_without_stretching():
    a = comp.Candidate("p", 0, (1,), 10.0, 14.0, 1)
    b = comp.Candidate("p", 1, (2,), 50.0, 53.0, 1)
    p = perf_of([Strike(10.5, 10.8, 40, 70), Strike(50.5, 50.8, 42, 70)])
    strikes, _ = comp.assemble([(a, p), (b, p)], tail=0.0)
    on = sorted(s.t_on for s in strikes)
    assert on[0] == pytest.approx(0.5, abs=0.06)
    # the second group starts where the first ended, around 4 s in
    assert 4.0 <= on[1] <= 4.8


# -- real -------------------------------------------------------------------

@pytest.fixture(scope="module")
def maps():
    if not MAPS.exists():
        pytest.skip(f"{MAPS} not on this machine")
    m = comp.load_maps(MAPS)
    if not m:
        pytest.skip("no accepted measure maps")
    return m


def test_every_group_offers_a_candidate(maps):
    c = comp.candidates(maps)
    assert c
    for gi, row in c.items():
        assert row, f"group {gi} has no candidate"
        assert len({x.pass_id for x in row}) == len(row), "a pass offered twice"


def test_no_candidate_swallows_a_hesitation(maps):
    """A window must be about as long as its own bars take, at its own tempo.

    Passes genuinely differ in tempo -- 2026-09-20-joplin-8 runs bars 68-71 at
    1.89 s/bar against another pass's 4.05, and both are real -- so comparing
    candidates to each other proves nothing. What is not allowed is a window
    much longer than the bars inside it, which is what ending a clip at "the
    next event in the trajectory" produced: 16 s for four bars in
    2026-09-19-joplin-b-5, where the player pauses six seconds before bar 12.
    """
    for gi, row in comp.candidates(maps).items():
        for c in row:
            traj = comp.trajectory(maps[c.pass_id]["bars"])
            inside = [t for t, b in traj if c.t0 <= t < c.t1 and b in c.bars]
            if len(inside) < 3:
                continue
            steps = sorted(b - a for a, b in zip(inside, inside[1:]))
            spacing = steps[len(steps) // 2]
            assert c.duration <= 1.6 * spacing * len(c.bars), (
                f"group {gi} {c.pass_id}: {c.duration:.1f}s for {len(c.bars)} bars "
                f"spaced {spacing:.1f}s")


def test_assembling_every_group_uses_each_note_once(maps):
    from fp30x_studio.performance import Performance as P
    cands = comp.candidates(maps)
    perfs = {pid: P.from_raw_capture(CLIPS / f"sweep-{pid}.fp30") for pid in maps}
    if not all((CLIPS / f"sweep-{pid}.fp30").exists() for pid in maps):
        pytest.skip("sweep clips not on this machine")
    chosen = [cands[gi][0] for gi in sorted(cands)]
    strikes, pedal = comp.assemble([(c, perfs[c.pass_id]) for c in chosen])
    assert strikes and pedal
    assert len(set((s.key, round(s.t_on, 4)) for s in strikes)) == len(strikes)
    assert all(s.t_off >= s.t_on for s in strikes)
    # monotone in time overall: groups were laid down in order
    assert max(s.t_on for s in strikes) < sum(c.duration for c in chosen) + 5
