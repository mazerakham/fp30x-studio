"""FPN edits: the Python side of the contract the player also implements."""
from fp30x_studio import fpn

DOC = {"format": "fpn", "version": 1,
       "notes": [[0, 0.0, 1.0, 60, 80], [1, 10.0, 2.0, 62, 80],
                 [2, 14.0, 1.0, 64, 80], [3, 30.0, 1.0, 65, 80]],
       "pedal": [[12.0, 127], [20.0, 0]], "edits": []}


def test_no_edits_is_identity():
    notes, pedal = fpn.apply(DOC)
    assert notes == DOC["notes"] and pedal == DOC["pedal"]


def test_delete_removes_by_id_and_leaves_the_original():
    d = dict(DOC, edits=[{"op": "delete", "ids": [1, 3]}])
    notes, _ = fpn.apply(d)
    assert [n[0] for n in notes] == [0, 2]
    assert len(d["notes"]) == 4


def test_speed_compresses_the_window_and_pulls_the_rest_earlier():
    d = dict(DOC, edits=[{"op": "speed", "a": 10.0, "b": 20.0, "k": 2.0}])
    notes, pedal = fpn.apply(d)
    on = {n[0]: n[1] for n in notes}
    assert on == {0: 0.0, 1: 10.0, 2: 12.0, 3: 25.0}
    assert dict((n[0], n[2]) for n in notes)[1] == 1.0     # held half as long
    assert pedal == [[11.0, 127], [15.0, 0]]


def test_edits_apply_in_order():
    d = dict(DOC, edits=[{"op": "speed", "a": 10.0, "b": 20.0, "k": 2.0},
                         {"op": "speed", "a": 10.0, "b": 15.0, "k": 5.0}])
    notes, _ = fpn.apply(d)
    assert {n[0]: n[1] for n in notes}[3] == 21.0


def test_fp30_parser_handles_running_status(tmp_path):
    f = tmp_path / "t.fp30"
    f.write_text("# header\n1000000000 90 3C 40 3E 40\n2000000000 80 3C 00\n"
                 "2500000000 B0 40 7F\n3000000000 90 3E 00\n")
    notes, pedal = fpn.read_fp30(f)
    assert [(n[3], n[1], n[2]) for n in notes] == [(60, 0.0, 1.0), (62, 0.0, 2.0)]
    assert pedal == [[1.5, 127]]
