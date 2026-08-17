"""Tests for :mod:`fp30x_studio.pipeline`.

Two halves.

The synthetic half writes ``.fp30`` fixtures inline and needs no piano. It pins
the behaviour that is easy to get quietly wrong: that the incremental path and
the whole-file path agree byte for byte, that a torn line stops the offset
rather than being skipped, that every message leaves the pairing layer with a
role, and that a repaired interval is still marked as repaired.

The real half runs against the takes captured on 2026-08-17 and pins their
measured numbers as regression fixtures. It skips cleanly where those files are
not present. It includes one test that is really a test of a *bug*: the
packet-level parse that reads only the first MIDI message of each CoreMIDI
packet, which is what produced the phantom 37.97 s note. That parse is
reproduced exactly, so that if anyone reintroduces it the difference is visible
as a failing assertion rather than as a plausible-looking number.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from fp30x_studio import rawcapture
from fp30x_studio.performance import KeyTrack, Performance, Strike
from fp30x_studio.pipeline import (DEFECT_CLASSES, Pairer, TakeStore,
                                   integrity_report, queries)
from fp30x_studio.pipeline.integrity import lattice
from fp30x_studio.pipeline.pairing import Msg, PairingResult, classify, framed

HEADER = """\
# fp30x-capture v1
# columns abs_ns hex_bytes
# mach_timebase_numer 125
# mach_timebase_denom 3
# anchor_mach_ns 1000000000
# anchor_unix_ns 1786981500000000000
# started_utc 2026-08-17T15:45:00Z
# source FP-30X Bluetooth
# sources_connected 1
"""

TRAILER = ("# end packets 0 dropped 0 truncated 0 ts_zero 0 "
           "stopped_utc 2026-08-17T15:45:10Z\n")


def write(tmp_path, body, header=HEADER, trailer=TRAILER, name="t.fp30"):
    p = tmp_path / name
    p.write_text(header + textwrap.dedent(body) + (trailer or ""))
    return p


def store(tmp_path, path, **kw) -> TakeStore:
    return TakeStore(path, index=tmp_path / (Path(path).name + ".sqlite3"), **kw)


def msgs(*specs) -> list[Msg]:
    """``(ns, 'HEX HEX HEX')`` pairs -> framed messages, numbered from zero."""
    out: list[Msg] = []
    for i, (ns, hexs) in enumerate(specs):
        data = bytes(int(b, 16) for b in hexs.split())
        out.extend(framed(i, ns, len(out), data))
    return out


# -- framing and classification ---------------------------------------------

@pytest.mark.parametrize("hexs,kind,d1,d2", [
    ("90 3C 64", "note_on", 0x3C, 0x64),
    ("80 3C 40", "note_off", 0x3C, 0x40),
    ("A0 3C 20", "polytouch", 0x3C, 0x20),
    ("B0 40 7F", "control_change", 0x40, 0x7F),
    ("D0 40", "aftertouch", 0x40, None),
    ("C0 05", "program_change", 0x05, None),
    ("E0 00 40", "pitchwheel", 0x00, 0x40),
    ("FE", "active_sensing", None, None),
])
def test_classify(hexs, kind, d1, d2):
    raw = bytes(int(b, 16) for b in hexs.split())
    got_kind, _, got_d1, got_d2 = classify(raw)
    assert (got_kind, got_d1, got_d2) == (kind, d1, d2)


def test_truncated_message_is_undecodable_not_an_exception():
    """Bytes that arrived but cannot be read must still be counted."""
    assert classify(bytes([0x90, 0x3C]))[0] == "undecodable"
    assert classify(bytes([0x40]))[0] == "undecodable"


def test_a_packet_yields_every_message_it_carries():
    """The bug that produced the phantom 37.97 s note was dropping these."""
    m = msgs((0, "90 3C 64 90 40 50 B0 40 7F"))
    assert [x.kind for x in m] == ["note_on", "note_on", "control_change"]
    assert [x.seq for x in m] == [0, 1, 2]
    assert {x.ns for x in m} == {0}


# -- pairing: the no-silent-drop guarantee ----------------------------------

def test_a_clean_pair_is_one_trusted_interval():
    r = Pairer().feed(msgs((0, "90 3C 64"), (250_000_000, "80 3C 40")))
    assert len(r.intervals) == 1
    iv = r.intervals[0]
    assert iv.note == 60 and iv.key == 39
    assert iv.velocity_on == 100 and iv.velocity_off == 64
    assert iv.closure == "note_off" and iv.trusted
    assert [role for _, role in r.roles] == ["interval_on", "interval_off"]


def test_every_message_leaves_with_exactly_one_role():
    m = msgs((0, "90 3C 64"), (1, "80 3E 40"), (2, "B0 40 7F"),
             (3, "B0 07 64"), (4, "FE"), (5, "90 3C"))
    r = Pairer().feed(m)
    assert len(r.roles) == len(m) == r.n_messages
    assert [role for _, role in r.roles] == [
        "interval_on", "orphan_note_off", "pedal", "control", "other",
        "undecodable"]


def test_audit_refuses_a_result_that_lost_a_message():
    bad = PairingResult(roles=[(0, "other")], n_messages=2)
    with pytest.raises(AssertionError, match="every message must leave"):
        bad.audit()


def test_orphan_note_off_is_a_defect_and_is_not_discarded():
    r = Pairer().feed(msgs((0, "80 3C 40")))
    assert not r.intervals
    assert [d.cls for d in r.defects] == ["orphan_note_off"]
    assert r.roles == [(0, "orphan_note_off")]


def test_restrike_closes_the_open_interval_at_the_new_onset():
    """The physical reading: the hammer has already reset the string."""
    r = Pairer().feed(msgs((0, "90 3C 64"), (500_000_000, "90 3C 70"),
                           (900_000_000, "80 3C 40")))
    assert [d.cls for d in r.defects] == ["restrike_before_note_off"]
    a, b = sorted(r.intervals, key=lambda i: i.ns_on)
    assert a.ns_off == b.ns_on == 500_000_000
    assert a.closure == "restrike" and not a.trusted
    assert a.velocity_off is None            # never observed, never invented
    assert b.closure == "note_off" and b.trusted


def test_the_restrike_repair_satisfies_the_disjointness_invariant():
    """performance.py's invariant is not relaxed; the repair is chosen to hold it."""
    r = Pairer().feed(msgs((0, "90 3C 64"), (500_000_000, "90 3C 70"),
                           (900_000_000, "80 3C 40")))
    strikes = [Strike(t_on=i.ns_on / 1e9, t_off=i.ns_off / 1e9, key=i.key,
                      velocity=i.velocity_on) for i in r.intervals]
    KeyTrack(39, strikes, check=True, strict=False)      # a.e. disjoint: holds
    with pytest.raises(Exception):
        KeyTrack(39, strikes, check=True, strict=True)   # they meet at a point


def test_a_key_still_down_stays_open_until_the_stream_really_ends():
    p = Pairer()
    r = p.feed(msgs((0, "90 3C 64")))
    assert not r.intervals and p.open_notes == [60]
    f = p.finish(2_000_000_000)
    assert len(f.intervals) == 1
    assert f.intervals[0].closure == "end_of_take"
    assert f.intervals[0].ns_off == 2_000_000_000
    assert [d.cls for d in f.defects] == ["held_at_end"]


def test_a_long_interval_is_flagged_and_never_repaired():
    r = Pairer(implausible_s=1.0).feed(
        msgs((0, "90 3C 64"), (5_000_000_000, "80 3C 40")))
    assert [d.cls for d in r.defects] == ["implausible_duration"]
    assert r.intervals[0].trusted            # flagged, but still a real note-off
    assert r.intervals[0].ns_off == 5_000_000_000


def test_a_backwards_timestamp_is_a_defect():
    r = Pairer().feed(msgs((1_000_000_000, "90 3C 64"), (5, "80 3C 40")))
    assert "non_monotone_timestamp" in {d.cls for d in r.defects}
    assert r.intervals[0].ns_off >= r.intervals[0].ns_on   # b < a is impossible


def test_pairer_state_round_trips():
    p = Pairer()
    p.feed(msgs((0, "90 3C 64"), (1, "90 40 50")))
    q = Pairer.from_json(p.to_json())
    assert q.open_notes == p.open_notes == [60, 64]
    assert q.last_ns == p.last_ns
    r = q.feed(msgs((2_000_000_000, "80 3C 40")))
    assert r.intervals[0].ns_on == 0 and r.intervals[0].trusted


# -- the incremental reader --------------------------------------------------

def test_scan_leaves_a_trailing_fragment_alone(tmp_path):
    """A half-written line is the normal state of a live capture file."""
    p = tmp_path / "live.fp30"
    p.write_text(HEADER + "1000000000 90 3C 64\n1001000000 80 3C")
    s = rawcapture.scan(p)
    assert len(s.records) == 1
    assert s.next_offset == len(HEADER) + len("1000000000 90 3C 64\n")
    with p.open("a") as fh:
        fh.write(" 40\n")
    s2 = rawcapture.scan(p, start=s.next_offset)
    assert len(s2.records) == 1 and s2.records[0].data == bytes([0x80, 0x3C, 0x40])


def test_scan_stops_at_a_torn_line_and_does_not_step_over_it(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1001000000 ZZ NOPE
        1002000000 80 3C 40
        """, trailer=None)
    s = rawcapture.scan(p)
    assert s.torn is True
    assert len(s.records) == 1
    again = rawcapture.scan(p, start=s.next_offset)
    assert again.torn is True and not again.records


def test_read_is_unchanged_by_the_refactor(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1250000000 80 3C 40
        """)
    cap = rawcapture.read(p)
    assert len(cap) == 2 and cap.complete and cap.duration == pytest.approx(0.25)


# -- incremental ingest ------------------------------------------------------

BODY = "".join(f"{1_000_000_000 + i * 5_000_000} "
               f"{'90' if i % 2 == 0 else '80'} 3C "
               f"{'64' if i % 2 == 0 else '40'}\n" for i in range(200))


def test_incremental_ingest_equals_whole_file_ingest(tmp_path):
    whole = write(tmp_path, BODY, name="whole.fp30")
    live = tmp_path / "live.fp30"
    live.write_text("")

    a = store(tmp_path, whole)
    a.ingest()

    b = store(tmp_path, live)
    blob = whole.read_bytes()
    for i in range(1, 12):                       # 11 uneven appends, cutting lines
        cut = len(blob) * i // 11
        prev = len(blob) * (i - 1) // 11
        with live.open("ab") as fh:
            fh.write(blob[prev:cut])
        b.ingest()

    def snap(s):
        return (s.count("packet"), s.count("message"), s.count("interval"),
                s.count("defect"), s.census(), s.role_counts(),
                s.closure_counts(), [tuple(r) for r in s.intervals()])
    assert snap(a) == snap(b)
    assert b.checkpoint["byte_offset"] == whole.stat().st_size


def test_a_second_ingest_of_an_unchanged_file_does_nothing(tmp_path):
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    again = st.ingest()
    assert (again.new_packets, again.new_messages, again.new_intervals) == (0, 0, 0)
    assert not again.full_reingest


def test_a_rewritten_file_forces_a_full_reingest(tmp_path):
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    # Rewritten, and *longer*, so the size check cannot fire and the head digest
    # is the only thing standing between us and a corrupt resume.
    p.write_text(HEADER + "".join(
        f"{9_000_000_000 + i * 5_000_000} 90 3E 64\n" for i in range(400)))
    assert p.stat().st_size > st.checkpoint["byte_offset"]
    res = st.ingest()
    assert res.full_reingest and "head changed" in res.reingest_reason
    assert st.count("packet") == 400


def test_a_truncated_file_forces_a_full_reingest(tmp_path):
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    blob = p.read_bytes()
    p.write_bytes(blob[:len(blob) // 3])
    res = st.ingest()
    assert res.full_reingest and "shrank" in res.reingest_reason


def test_keys_down_are_not_closed_until_the_trailer_proves_the_stream_ended(tmp_path):
    p = tmp_path / "open.fp30"
    p.write_text(HEADER + "1000000000 90 3C 64\n")
    st = store(tmp_path, p)
    res = st.ingest()
    assert res.open_notes == [60] and not res.complete
    assert st.count("interval") == 0             # no invented release
    with p.open("a") as fh:
        fh.write("1500000000 90 40 64\n" + TRAILER)
    res = st.ingest()
    assert res.complete and res.finished
    assert st.closure_counts() == {"end_of_take": 2}
    assert st.defect_counts()["held_at_end"] == 2


def test_accounting_is_complete_after_ingest(tmp_path):
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    assert st.accounted
    assert st.count("accounting") == st.count("message") == 200


# -- integrity ---------------------------------------------------------------

def test_lattice_recognises_an_exact_grid():
    L = lattice([0, 5_000_000, 10_000_000, 25_000_000])
    assert (L.n_gaps, L.n_on_lattice) == (3, 3)
    assert L.fraction == 1.0 and L.phase_intact


def test_lattice_calls_a_phase_restoring_excursion_jitter():
    """1 ms late then 4 ms early: the grid phase survives, nothing was lost."""
    L = lattice([0, 5_000_000, 11_000_000, 15_000_000, 20_000_000])
    assert L.n_on_lattice == 2 and len(L.runs) == 1
    assert L.runs[0][3] is True and L.phase_intact


def test_lattice_reports_a_permanent_phase_slip():
    L = lattice([0, 5_000_000, 11_000_000, 16_000_000])
    assert len(L.runs) == 1 and L.runs[0][3] is False
    assert not L.phase_intact


def test_the_loss_caveat_is_printed_with_the_loss_figure(tmp_path):
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    text = integrity_report(st).text()
    assert "never reaches CoreMIDI" in text
    assert "inferred loss" in text


def test_inferred_loss_counts_both_directions(tmp_path):
    p = write(tmp_path, """\
        1000000000 80 3C 40
        1005000000 90 3E 64
        1010000000 90 3E 70
        1015000000 80 3E 40
        """)
    st = store(tmp_path, p)
    st.ingest()
    r = integrity_report(st)
    assert r.loss.inferred_lost_note_ons == 1     # from the orphan release
    assert r.loss.inferred_lost_note_offs == 1    # from the re-strike
    assert r.loss.inferred_lost == 2
    assert r.verdict.startswith("SUSPECT")


def test_every_defect_class_appears_in_the_report_even_at_zero(tmp_path):
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    r = integrity_report(st)
    assert set(r.defects) == set(DEFECT_CLASSES)


# -- queries -----------------------------------------------------------------

def test_a_new_measurement_is_a_small_function(tmp_path):
    @queries.query("test-only-mean-velocity", "mean strike velocity")
    def _mean(store_):
        v = [r["velocity_on"] for r in store_.intervals()]
        return queries.Result("mean", ("statistic", "value"),
                              [("mean", sum(v) / len(v))])
    try:
        p = write(tmp_path, BODY)
        st = store(tmp_path, p)
        st.ingest()
        assert queries.run(st, "test-only-mean-velocity").rows == [("mean", 100.0)]
    finally:
        queries.QUERIES.pop("test-only-mean-velocity")


def test_a_timing_sensitive_query_shouts_on_an_untrusted_take(tmp_path):
    p = write(tmp_path, "1000000000 90 3C 64\n1250000000 80 3C 40\n",
              trailer="# end packets 2 dropped 0 truncated 0 ts_zero 2 "
                      "stopped_utc x\n")
    st = store(tmp_path, p)
    st.ingest()
    assert not st.timing_trusted
    assert "TIMING NOT TRUSTED" in queries.run(st, "durations").notes[0]
    # a query that does not depend on timing is left alone
    assert not any("TIMING NOT TRUSTED" in n
                   for n in queries.run(st, "census").notes)


def test_unknown_query_names_itself(tmp_path):
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    with pytest.raises(KeyError, match="census"):
        queries.run(st, "no-such-query")


# -- the bridge back to performance.py ---------------------------------------

def test_to_performance_matches_the_untouched_parser(tmp_path):
    p = write(tmp_path, """\
        1000000000 B0 40 7F
        1000000000 90 3C 64
        1250000000 80 3C 40
        1300000000 90 40 50
        1900000000 80 40 30
        2000000000 B0 40 00
        """)
    st = store(tmp_path, p)
    st.ingest()
    assert st.to_performance().to_records() == \
        Performance.from_raw_capture(p).to_records()


def test_trusted_only_drops_the_repaired_intervals(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1500000000 90 3C 70
        1900000000 80 3C 40
        """)
    st = store(tmp_path, p)
    st.ingest()
    assert st.to_performance().n_strikes == 2
    assert st.to_performance(trusted_only=True).n_strikes == 1


# -- the real takes ----------------------------------------------------------

TAKES = Path.home() / "Music" / "FP-30X Studio" / "takes"

#: Measured on 2026-08-17 from the real files. Regression fixtures: a change to
#: any of these numbers is a change to what the pipeline believes about the
#: hardware, and has to be argued for rather than absorbed.
REAL = {
    "2026-08-17-piece": dict(
        packets=4861, messages=5173, note_on=1730, note_off=1730,
        control_change=1713, intervals=1730, trusted=1730,
        multi_message_packets=293, max_msgs_per_packet=3,
        lattice_on=4819, lattice_gaps=4860, runs=20, peak_polyphony=8,
        defects={c: 0 for c in DEFECT_CLASSES},
    ),
    "2026-08-17-open": dict(
        packets=2185, messages=2240, note_on=804, note_off=804,
        control_change=632, intervals=804, trusted=804,
        multi_message_packets=53, max_msgs_per_packet=3,
        lattice_on=2182, lattice_gaps=2184, runs=1, peak_polyphony=7,
        defects={c: 0 for c in DEFECT_CLASSES},
    ),
    "2026-08-17-wiggle": dict(
        packets=482, messages=499, note_on=125, note_off=123,
        control_change=251, intervals=125, trusted=123,
        multi_message_packets=14, max_msgs_per_packet=3,
        lattice_on=479, lattice_gaps=481, runs=1, peak_polyphony=6,
        defects={c: 0 for c in DEFECT_CLASSES} | {"held_at_end": 2},
    ),
}

real_take = pytest.mark.parametrize("name", sorted(REAL))
needs_takes = pytest.mark.skipif(
    not all((TAKES / f"{n}.fp30").exists() for n in REAL),
    reason="the 2026-08-17 takes are not on this machine")


@pytest.fixture(scope="module")
def ingested(tmp_path_factory):
    d = tmp_path_factory.mktemp("index")
    out = {}
    for name in REAL:
        path = TAKES / f"{name}.fp30"
        if not path.exists():
            continue
        st = TakeStore(path, index=d / f"{name}.sqlite3")
        st.ingest()
        out[name] = st
    return out


@needs_takes
@real_take
def test_real_take_counts(ingested, name):
    st, want = ingested[name], REAL[name]
    assert st.count("packet") == want["packets"]
    assert st.count("message") == want["messages"]
    assert st.census()["control_change"] == want["control_change"]
    assert st.count("interval") == want["intervals"]
    assert st.accounted


@needs_takes
@real_take
def test_real_take_defect_counts(ingested, name):
    st, want = ingested[name], REAL[name]
    got = {c: 0 for c in DEFECT_CLASSES} | st.defect_counts()
    assert got == want["defects"]


@needs_takes
@real_take
def test_real_take_pairs_completely(ingested, name):
    """No orphan releases, no re-strikes: on this link nothing was lost."""
    st, want = ingested[name], REAL[name]
    r = integrity_report(st)
    assert (r.loss.note_on, r.loss.note_off) == (want["note_on"], want["note_off"])
    assert r.loss.inferred_lost == 0
    assert r.loss.balance == want["defects"].get("held_at_end", 0)
    assert r.trusted_intervals == want["trusted"]


@needs_takes
@real_take
def test_real_take_lattice(ingested, name):
    st, want = ingested[name], REAL[name]
    L = integrity_report(st).lattice
    assert (L.n_on_lattice, L.n_gaps) == (want["lattice_on"], want["lattice_gaps"])
    assert len(L.runs) == want["runs"]
    assert L.phase_intact, "every off-lattice excursion must restore the grid phase"


@needs_takes
@real_take
def test_real_take_has_no_aftertouch_of_either_kind(ingested, name):
    c = ingested[name].census()
    assert c.get("polytouch", 0) == 0
    assert c.get("aftertouch", 0) == 0


@needs_takes
@real_take
def test_real_take_is_hardware_timestamped(ingested, name):
    assert ingested[name].meta["timing_grade"] == "hardware"
    assert ingested[name].timing_trusted


@needs_takes
@real_take
def test_real_take_multi_message_packets(ingested, name):
    """Chords arrive as multi-message packets. Reading one message loses them."""
    st, want = ingested[name], REAL[name]
    r = integrity_report(st)
    assert r.multi_message_packets == want["multi_message_packets"]
    assert r.max_messages_per_packet == want["max_msgs_per_packet"]


@needs_takes
@real_take
def test_real_take_polyphony(ingested, name):
    perf = ingested[name].to_performance()
    assert perf.max_polyphony() == REAL[name]["peak_polyphony"]


@needs_takes
@real_take
def test_real_take_agrees_with_the_untouched_parser(ingested, name):
    """The materialised pairing is not a second implementation of the parser."""
    st = ingested[name]
    ref = Performance.from_capture(rawcapture.read(TAKES / f"{name}.fp30"))
    assert st.to_performance().to_records() == ref.to_records()


@needs_takes
@real_take
def test_real_take_incremental_matches_whole_file(ingested, name, tmp_path):
    """Replay the real file in nine uneven appends and demand the same index."""
    src = TAKES / f"{name}.fp30"
    blob = src.read_bytes()
    live = tmp_path / f"live-{name}.fp30"
    live.write_bytes(b"")
    st = TakeStore(live, index=tmp_path / f"live-{name}.sqlite3")
    for i in range(1, 10):
        with live.open("ab") as fh:
            fh.write(blob[len(blob) * (i - 1) // 9:len(blob) * i // 9])
        st.ingest()
    ref = ingested[name]
    assert [tuple(r) for r in st.intervals()] == \
        [tuple(r) for r in ref.intervals()]
    assert st.defect_counts() == ref.defect_counts()
    assert st.census() == ref.census()


# -- the bug this pipeline exists to prevent ---------------------------------

#: What a parse that reads only the *first* MIDI message of each CoreMIDI packet
#: reports on the real takes. These are not correct numbers; they are the wrong
#: numbers, recorded so that the difference stays visible.
FIRST_MESSAGE_ONLY = {
    "2026-08-17-piece": dict(note_on=1520, orphan_note_off=195, restrike=47,
                             phantom_s=[62.60, 52.73, 37.97]),
    "2026-08-17-open": dict(note_on=777, orphan_note_off=27, restrike=16,
                            phantom_s=[35.02, 17.95, 14.06]),
}


def _first_message_only(cap):
    """The naive packet-level parse, reproduced exactly."""
    held: dict[int, float] = {}
    orphan = restrike = note_on = 0
    durations: list[float] = []
    for rec in cap.records:
        parts = rawcapture.split_messages(rec.data)
        if not parts:
            continue
        raw = parts[0]                        # <-- the bug: the rest are dropped
        if len(raw) < 3 or (raw[0] & 0xF0) not in (0x80, 0x90):
            continue
        t, note = cap.seconds(rec.ns), raw[1]
        if (raw[0] & 0xF0) == 0x90 and raw[2] > 0:
            note_on += 1
            if note in held:
                restrike += 1
                durations.append(t - held.pop(note))
            held[note] = t
        elif note in held:
            durations.append(t - held.pop(note))
        else:
            orphan += 1
    return note_on, orphan, restrike, sorted(durations, reverse=True)


@needs_takes
@pytest.mark.parametrize("name", sorted(FIRST_MESSAGE_ONLY))
def test_the_naive_packet_parse_invents_defects_the_pipeline_does_not(
        ingested, name):
    """Pins the false reading and the true one side by side.

    Reading one message per packet loses the second and third messages of every
    multi-message packet -- which is where chords are. The lost note-offs then
    look like the link dropping messages under density, and the longest phantom
    interval on the piece take was the 37.97 s note that started this.

    The full parse finds no defect of any kind on the same bytes.
    """
    cap = rawcapture.read(TAKES / f"{name}.fp30")
    note_on, orphan, restrike, durations = _first_message_only(cap)
    want = FIRST_MESSAGE_ONLY[name]
    assert note_on == want["note_on"]
    assert orphan == want["orphan_note_off"]
    assert restrike == want["restrike"]
    assert durations[:3] == pytest.approx(want["phantom_s"], abs=0.01)

    st = ingested[name]
    r = integrity_report(st)
    assert r.loss.note_on > note_on           # the full parse finds more notes
    assert r.loss.inferred_lost == 0          # and no loss at all
    assert max((iv["ns_off"] - iv["ns_on"]) / 1e9
               for iv in st.intervals()) < 15.0


# -- the poll-loop takes must be marked, not mixed in ------------------------

POLL_LOOP = sorted(TAKES.glob("2026-08-16*.mid")) if TAKES.exists() else []


@pytest.mark.skipif(not POLL_LOOP, reason="the 2026-08-16 .mid takes are absent")
def test_poll_loop_takes_are_marked_untrustworthy(tmp_path):
    st = TakeStore(POLL_LOOP[0], index=tmp_path / "poll.sqlite3")
    st.ingest()
    assert st.meta["timing_grade"] == "poll-loop"
    assert not st.timing_trusted
    assert "2.08 ms" in st.meta["timing_note"]
    assert "TIMING NOT TRUSTED" in queries.run(st, "onsets").notes[0]


@pytest.mark.skipif(not POLL_LOOP, reason="the 2026-08-16 .mid takes are absent")
def test_poll_loop_takes_still_answer_message_type_questions(tmp_path):
    """The 2 ms floor cannot manufacture or hide a message type."""
    st = TakeStore(POLL_LOOP[0], index=tmp_path / "poll.sqlite3")
    st.ingest()
    assert st.census().get("polytouch", 0) == 0
    assert st.census().get("aftertouch", 0) == 0
    assert st.accounted


# -- provenance --------------------------------------------------------------

def test_provenance_entry_carries_the_numbers_and_the_caveat(tmp_path):
    from fp30x_studio.pipeline.provenance import append_provenance, entry_for
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    text = entry_for(st)
    assert "5 ms lattice" in text and "Inferred link loss" in text
    assert "never reaches CoreMIDI" in text
    target = tmp_path / "PROVENANCE.md"
    assert append_provenance(st, path=target) is True
    assert append_provenance(st, path=target) is False   # once per take
    assert target.read_text().count("<!-- fp30x-pipeline:") == 1


def test_pairer_state_is_small_no_matter_how_long_the_take(tmp_path):
    """What resumes an ingest is the set of keys down, not the take's history."""
    p = write(tmp_path, BODY)
    st = store(tmp_path, p)
    st.ingest()
    assert len(st.checkpoint["pairer_state"]) < 512
    assert json.loads(st.checkpoint["pairer_state"])["open"] == {}
