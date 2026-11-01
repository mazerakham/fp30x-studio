"""The stretch builder, on hand-made windows. No scores, no alignment."""
from fp30x_studio.align.locate import build, confidence


def w(seg, i0, t0, t1, work, bars, score=60.0, margin=10.0, rate=0.7, alt=None):
    d = {"seg": seg, "i0": i0, "t0": t0, "t1": t1, "work": work, "bars": bars,
         "score": score, "margin": margin, "rate": rate}
    if alt:
        d["alt"] = alt
    return d


def test_forward_run_is_one_stretch():
    ws = [w(0, 0, 0, 30, "Op. 55 No. 2", [1, 5]), w(0, 60, 15, 45, "Op. 55 No. 2", [4, 9]),
          w(0, 120, 30, 60, "Op. 55 No. 2", [8, 14])]
    st = build({"windows": ws})
    assert len(st) == 1 and (st[0]["lo"], st[0]["hi"]) == (1, 14)


def test_going_back_starts_a_new_pass():
    ws = [w(0, 0, 0, 30, "Op. 55 No. 1", [86, 93]), w(0, 60, 15, 45, "Op. 55 No. 1", [89, 99]),
          w(0, 120, 30, 60, "Op. 55 No. 1", [68, 73])]
    st = build({"windows": ws})
    assert [(s["lo"], s["hi"]) for s in st] == [(86, 99), (68, 73)]


def test_change_of_work_starts_a_new_stretch():
    ws = [w(0, 0, 0, 30, "Op. 55 No. 1", [89, 98]), w(0, 60, 15, 45, "Op. 9 No. 2", [30, 34])]
    assert [s["work"] for s in build({"windows": ws})] == ["Op. 55 No. 1", "Op. 9 No. 2"]


def test_weak_windows_do_not_move_the_bars():
    ws = [w(0, 0, 0, 30, "Op. 55 No. 2", [30, 32]),
          w(0, 60, 15, 45, "Op. 15 No. 1", [31, 34], margin=1.1, rate=0.19),
          w(0, 120, 30, 60, "Op. 55 No. 2", [33, 36])]
    st = build({"windows": ws})
    assert len(st) == 1 and st[0]["unsure_windows"] == 1
    # 0.19 of its own events matched is under RATE_MIN, so it is not even "unsure".
    assert confidence(ws[1]) == "none"


def test_reprise_is_resolved_by_continuity():
    ws = [w(0, 0, 0, 30, "Op. 48 No. 2", [80, 88]),
          w(0, 60, 15, 45, "Op. 48 No. 2", [5, 12], score=50.0,
            alt={"score": 48.0, "bars": [88, 95], "b0": 0, "b1": 0})]
    st = build({"windows": ws})
    assert len(st) == 1 and st[0]["hi"] == 95


def test_unresolvable_reprise_is_flagged_not_guessed():
    ws = [w(0, 0, 0, 30, "Op. 48 No. 2", [5, 12], score=50.0,
            alt={"score": 48.0, "bars": [88, 95], "b0": 0, "b1": 0})]
    st = build({"windows": ws})
    assert any("or" in n for n in st[0]["notes"])
