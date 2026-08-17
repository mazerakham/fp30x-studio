"""Tests for :mod:`fp30x_studio.rawcapture`, the reader for native captures.

Every fixture is a ``.fp30x`` file written inline by the test, so the suite
runs with no piano attached and no native binary built. The cases that matter
are the ones a real capture produces and a naive reader gets wrong: several
messages inside one packet, a file with no trailer because the laptop slept
mid-take, a torn final line, and a source that never stamped anything.
"""

from __future__ import annotations

import textwrap

import pytest

from fp30x_studio import inspect_capture, rawcapture
from fp30x_studio.performance import Performance

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

TRAILER = ("# end packets 4 dropped 0 truncated 0 ts_zero 0 "
           "stopped_utc 2026-08-17T15:45:10Z\n")


def write(tmp_path, body, header=HEADER, trailer=TRAILER, name="t.fp30x"):
    p = tmp_path / name
    p.write_text(header + textwrap.dedent(body) + (trailer or ""))
    return p


# -- message framing --------------------------------------------------------

@pytest.mark.parametrize("status,length", [
    (0x90, 3), (0x80, 3), (0xB0, 3), (0xA0, 3), (0xE0, 3),
    (0xC0, 2), (0xD0, 2),
    (0xF1, 2), (0xF2, 3), (0xF3, 2), (0xF6, 1),
    (0xF8, 1), (0xFE, 1), (0xFF, 1),
])
def test_message_length(status, length):
    assert rawcapture.message_length(status) == length


def test_sysex_has_no_fixed_length():
    assert rawcapture.message_length(0xF0) is None


def test_data_byte_is_not_a_status_byte():
    with pytest.raises(ValueError):
        rawcapture.message_length(0x40)


def test_split_single_message():
    assert rawcapture.split_messages(bytes([0x90, 0x3C, 0x64])) == [
        bytes([0x90, 0x3C, 0x64])]


def test_split_several_messages_in_one_packet():
    """CoreMIDI may deliver several complete messages in a single packet."""
    raw = bytes([0x90, 0x3C, 0x64, 0x90, 0x40, 0x50, 0xB0, 0x40, 0x7F])
    assert rawcapture.split_messages(raw) == [
        bytes([0x90, 0x3C, 0x64]),
        bytes([0x90, 0x40, 0x50]),
        bytes([0xB0, 0x40, 0x7F]),
    ]


def test_split_mixed_lengths_and_realtime():
    raw = bytes([0xF8, 0xC0, 0x05, 0x90, 0x3C, 0x64, 0xFE])
    assert rawcapture.split_messages(raw) == [
        bytes([0xF8]), bytes([0xC0, 0x05]),
        bytes([0x90, 0x3C, 0x64]), bytes([0xFE]),
    ]


def test_split_sysex():
    raw = bytes([0xF0, 0x41, 0x10, 0x42, 0xF7, 0x90, 0x3C, 0x64])
    assert rawcapture.split_messages(raw) == [
        bytes([0xF0, 0x41, 0x10, 0x42, 0xF7]),
        bytes([0x90, 0x3C, 0x64]),
    ]


def test_split_never_discards_bytes():
    """An unterminated or malformed tail is kept, not silently dropped."""
    raw = bytes([0x90, 0x3C])
    assert b"".join(rawcapture.split_messages(raw)) == raw


# -- file parsing -----------------------------------------------------------

def test_header_and_records(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1001000000 80 3C 40
        """)
    cap = rawcapture.read(p)
    assert cap.version == 1
    assert cap.timebase == (125, 3)
    assert cap.source == "FP-30X Bluetooth"
    assert len(cap) == 2
    assert cap.records[0].data == bytes([0x90, 0x3C, 0x64])
    assert cap.records[0].hex == "90 3C 64"


def test_origin_first_puts_zero_at_first_event(tmp_path):
    p = write(tmp_path, """\
        2000000000 90 3C 64
        2001500000 80 3C 40
        """)
    cap = rawcapture.read(p)
    assert cap.seconds(cap.records[0].ns) == 0.0
    assert cap.duration == pytest.approx(0.0015)


def test_origin_header_preserves_leading_silence(tmp_path):
    p = write(tmp_path, """\
        2000000000 90 3C 64
        """)
    cap = rawcapture.read(p, origin="header")
    # anchor_mach_ns is 1e9, the event is at 2e9, so one second of silence.
    assert cap.seconds(cap.records[0].ns) == pytest.approx(1.0)


def test_wall_clock_mapping(tmp_path):
    p = write(tmp_path, "1000000000 90 3C 64\n")
    cap = rawcapture.read(p)
    assert cap.unix_ns(1000000000) == 1786981500000000000
    assert cap.unix_ns(1000000000 + 5_000_000_000) == 1786981505000000000


def test_clean_stop_is_reported(tmp_path):
    p = write(tmp_path, "1000000000 90 3C 64\n")
    assert rawcapture.read(p).complete is True


def test_missing_trailer_means_incomplete(tmp_path):
    """A laptop that sleeps mid-take leaves no trailer. Say so, don't assume."""
    p = write(tmp_path, "1000000000 90 3C 64\n", trailer=None)
    cap = rawcapture.read(p)
    assert cap.complete is False
    assert len(cap) == 1  # the data before the cut is still good


def test_torn_final_line_stops_the_parse(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1001000000 80 3C
        1002000000 ZZ
        """, trailer=None)
    cap = rawcapture.read(p)
    assert len(cap) == 2
    assert cap.complete is False


def test_dropped_and_ts_zero_surface_from_the_trailer(tmp_path):
    p = write(tmp_path, "1000000000 90 3C 64\n",
              trailer="# end packets 1 dropped 7 truncated 0 ts_zero 1 "
                      "stopped_utc 2026-08-17T15:45:10Z\n")
    cap = rawcapture.read(p)
    assert cap.n_dropped == 7
    assert cap.n_ts_zero == 1


def test_unstamped_source_is_not_advertised_as_hardware_timed(tmp_path):
    """If the source stamped nothing, this file is no better than the poll loop."""
    p = write(tmp_path, "1000000000 90 3C 64\n",
              trailer="# end packets 1 dropped 0 truncated 0 ts_zero 1 "
                      "stopped_utc 2026-08-17T15:45:10Z\n")
    assert rawcapture.read(p).hardware_timestamped is False


def test_partially_stamped_source_counts_as_hardware_timed(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1001000000 80 3C 40
        """, trailer="# end packets 2 dropped 0 truncated 0 ts_zero 1 "
                     "stopped_utc 2026-08-17T15:45:10Z\n")
    assert rawcapture.read(p).hardware_timestamped is True


def test_empty_capture_is_valid(tmp_path):
    p = write(tmp_path, "", trailer="# end packets 0 dropped 0 truncated 0 "
                                    "ts_zero 0 stopped_utc x\n")
    cap = rawcapture.read(p)
    assert len(cap) == 0
    assert cap.duration == 0.0
    assert cap.hardware_timestamped is False
    assert cap.messages == []


# -- the shared interface with the analysis layer ---------------------------

def test_messages_have_the_capture_shape(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1002000000 80 3C 40
        """)
    msgs = rawcapture.read(p).messages
    assert [m.type for _, m in msgs] == ["note_on", "note_off"]
    assert msgs[0][0] == 0.0
    assert msgs[1][0] == pytest.approx(0.002)
    assert msgs[0][1].note == 60 and msgs[0][1].velocity == 100


def test_packet_with_several_messages_shares_one_timestamp(tmp_path):
    """The stamp applies to the packet's first byte; the rest were simultaneous."""
    p = write(tmp_path, "1000000000 90 3C 64 90 40 50\n")
    msgs = rawcapture.read(p).messages
    assert len(msgs) == 2
    assert msgs[0][0] == msgs[1][0] == 0.0
    assert [m.note for _, m in msgs] == [60, 64]


def test_undecodable_bytes_do_not_break_a_take(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1001000000 F4
        1002000000 80 3C 40
        """)
    msgs = rawcapture.read(p).messages
    assert [m.type for _, m in msgs] == ["note_on", "note_off"]


def test_performance_consumes_a_raw_capture(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1250000000 80 3C 40
        """)
    perf = Performance.from_raw_capture(p)
    assert perf.n_strikes == 1
    s = perf.strikes()[0]
    assert s.note == 60 and s.velocity == 100
    assert s.duration == pytest.approx(0.25)


def test_from_capture_and_from_raw_capture_agree(tmp_path):
    """One interface: the analysis layer cannot tell the two front ends apart."""
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1250000000 80 3C 40
        """)
    cap = rawcapture.read(p)
    a = Performance.from_capture(cap)
    b = Performance.from_raw_capture(p)
    assert a.to_records() == b.to_records()


def test_sub_millisecond_intervals_survive(tmp_path):
    """The whole point: two events 100 us apart stay 100 us apart."""
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1000100000 90 40 64
        1000200000 80 3C 40
        1000300000 80 40 40
        """)
    perf = Performance.from_raw_capture(p)
    ons = sorted(s.t_on for s in perf.strikes())
    assert ons[1] - ons[0] == pytest.approx(100e-6, abs=1e-9)


def test_pedal_is_parsed(tmp_path):
    p = write(tmp_path, """\
        1000000000 B0 40 7F
        1000000000 90 3C 64
        1500000000 80 3C 40
        2000000000 B0 40 00
        """)
    perf = Performance.from_raw_capture(p)
    assert len(perf.pedal) == 1
    assert perf.pedal[0][1] - perf.pedal[0][0] == pytest.approx(1.0)


# -- byte-level census ------------------------------------------------------

def test_inspect_capture_reads_the_native_format(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1001000000 D0 40
        1002000000 80 3C 40
        """)
    cap = inspect_capture.load(p)
    assert cap.source == "fp30x"
    assert cap.census()["note_on"] == 1
    assert cap.census()["aftertouch"] == 1


def test_inspect_capture_pressure_report_on_native_format(tmp_path):
    p = write(tmp_path, """\
        1000000000 90 3C 64
        1002000000 80 3C 40
        """)
    rep = inspect_capture.pressure_report([inspect_capture.load(p)])
    assert rep["n_notes"] == 1
    assert rep["pressure_messages"] == 0
    assert "discrete-event" in rep["verdict"]
