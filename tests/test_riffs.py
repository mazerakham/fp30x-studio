"""The riff splicer may delete, but only what it says it deleted."""
from fp30x_studio import riffs as R


def _line(times, p=72, v=80):
    return [R.N(t, 0.3, p, v) for t in times]


def test_flutter_is_dropped_and_grid_is_steady():
    # quarter pulse 0.5 s, with a grace note 60 ms before beat 3
    ns = _line([0, 0.5, 0.9, 1.0, 1.5, 2.0])
    ns[2] = R.N(0.9, 0.05, 74, 30)   # 100 ms early: its own onset, not a chord
    ev, u, rep = R.splice(ns, 0, 3)
    assert rep["flutters"] == 1
    assert [p for p, _ in ev] == [0, 1, 2, 3, 4]
    assert abs(u - 0.5) < 0.02


def test_hesitation_is_spliced_down():
    ev, _, rep = R.splice(_line([0, 0.5, 1.0, 3.2, 3.7]), 0, 4)
    assert rep["splices"] == 1 and rep["spliced_s"] > 1
    assert [p for p, _ in ev] == [0, 1, 2, 2 + R.MAX_HOLD, 3 + R.MAX_HOLD]


def test_brushed_key_leaves_the_chord():
    ns = [R.N(0, 0.4, 72, 90), R.N(0.01, 0.1, 73, 20), R.N(0.5, 0.4, 74, 90)]
    ev, _, rep = R.splice(ns, 0, 1)
    assert [n.p for n in ev[0][1]] == [72] and rep["ghosts"] == 1


def test_edits_round_trip_from_the_page_line():
    assert R.parse_edits("riff 3a: cut bars 2,4; drop riff 6b; riff 1: cut bar 1") == {
        "3a": [2, 4], "6b": "drop", "1": [1]}


def test_cut_bars_closes_the_gap():
    r = R.Riff("x", 0, 9, [(float(i), [R.N(i * .5, .3, 72, 80)]) for i in range(12)],
               0.5, False, 4, 0.0, 3)
    c = R.cut_bars(r, [2])
    assert c.bars == 2 and len(c.events) == 8
    assert [p for p, _ in c.events] == [0, 1, 2, 3, 4, 5, 6, 7]


def _steady(n_beats, pulse=0.5):
    return [R.N(i * pulse, 0.3, 48 if i % 4 == 0 else 72, 80) for i in range(n_beats)]


def test_long_window_splits_into_riffs_of_at_most_eight_bars():
    for beats in (40, 100, 127, 131):       # 10, 25, ~32 and ~33 bars of 4/4
        rs = R.build("7", _steady(beats), 0, beats * 0.5 + 1)
        assert len(rs) > 1 and all(1 <= r.bars <= R.MAX_BARS for r in rs)
        assert [r.name for r in rs] == ["7" + R.SUFFIX[j] for j in range(len(rs))]
        assert sum(len(r.events) for r in rs) == beats


def test_cut_first_bar_with_a_pickup():
    r = R.Riff("x", 0, 9, [(float(i), [R.N(i * .5, .3, 72, 80)]) for i in range(10)],
               0.5, False, 4, 3.0, 4)       # pickup: first onset on beat 4
    c = R.cut_bars(r, [1])
    assert c.shift == 0 and c.bars == 3 and len(c.events) == 9


def test_score_fills_every_bar():
    r = R.build("s", _steady(22), 0, 12)[0]
    sc, _ = R.to_score(r)
    for part in sc.parts:
        ms = list(part.getElementsByClass("Measure"))
        assert len(ms) == r.bars
        assert all(m.duration.quarterLength == r.meter for m in ms)
