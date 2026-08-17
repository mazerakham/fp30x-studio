"""Tests for :mod:`fp30x_studio.take_analysis`, on synthetic fixtures only.

The page this module writes is a claim about a real take, so the parts of it
that could be quietly wrong are the ones tested here: the lattice detector (a
robust gcd, not a plain one), the modulus of continuity (which is computed
exactly and must therefore agree with a closed form), the Fano curve (which
must return 0 on a perfectly regular process and 1 on Poisson), and the
note-on/note-off pairing that recovers release velocity.

No piano and no capture file is needed: the ``.fp30x`` fixtures are written
inline, exactly as in :mod:`tests.test_rawcapture`.
"""

from __future__ import annotations

import textwrap

import numpy as np
import pytest

from fp30x_studio import rawcapture
from fp30x_studio.performance import (
    N_KEYS,
    KeyTrack,
    Performance,
    Strike,
    key_index,
)
from fp30x_studio.take_analysis import (
    CANTOR_EXPONENT,
    fano_curve,
    modulus_of_continuity,
    timing_lattice,
    velocity_channels,
)

MS = 1_000_000  # nanoseconds


def write_capture(tmp_path, packets, name="t.fp30x", trailer=True):
    """``[(ns, "90 3C 40"), ...]`` -> a parsed :class:`RawCapture`."""
    lines = [
        "# fp30x-capture v1",
        "# columns abs_ns hex_bytes",
        "# mach_timebase_numer 125",
        "# mach_timebase_denom 3",
        "# anchor_mach_ns 0",
        "# anchor_unix_ns 1786981500000000000",
        "# source test",
    ]
    lines += [f"{ns} {hexs}" for ns, hexs in packets]
    if trailer:
        lines.append(f"# end packets {len(packets)} dropped 0 truncated 0 ts_zero 0")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rawcapture.read(p)


def one_strike(t_on, t_off, velocity=100, note=60) -> Performance:
    k = key_index(note)
    tracks = [KeyTrack(i, [Strike(t_on, t_off, k, velocity)] if i == k else ())
              for i in range(N_KEYS)]
    return Performance(tracks, t_start=0.0, t_end=max(t_off, 1.0))


# --------------------------------------------------------------------------
# the lattice detector
# --------------------------------------------------------------------------

def test_lattice_finds_the_step(tmp_path):
    """A source emitting only on a 5 ms grid is reported as a 5 ms lattice."""
    packets = [(i * 5 * MS, "90 3C 40") for i in range(1, 60)]
    lat = timing_lattice(write_capture(tmp_path, packets))
    assert lat.step_ns == 5 * MS
    assert lat.off_lattice == 0
    assert lat.lattice_fraction == 1.0
    assert lat.min_gap_ms == 5
    assert lat.ceiling_rate == pytest.approx(200.0)


def test_lattice_survives_a_single_defector(tmp_path):
    """One off-grid packet must not collapse the step to 1 ns.

    A plain ``gcd`` over the gaps would; the detector takes the largest step
    that explains 99% of them and reports the residue instead of hiding it.
    """
    packets = [(i * 5 * MS, "90 3C 40") for i in range(1, 400)]
    packets.append((packets[-1][0] + 1 * MS, "80 3C 40"))
    packets.sort()
    lat = timing_lattice(write_capture(tmp_path, packets))
    assert lat.step_ns == 5 * MS
    assert lat.off_lattice == 1
    assert 0.99 < lat.lattice_fraction < 1.0


def test_lattice_reports_a_finer_grid_when_there_is_one(tmp_path):
    """The detector must not manufacture a 5 ms floor that is not there."""
    packets = [(i * MS, "90 3C 40") for i in range(1, 60)]
    assert timing_lattice(write_capture(tmp_path, packets)).step_ns == MS


def test_bundled_packets_are_counted(tmp_path):
    """Several messages in one packet are the only simultaneity a link admits."""
    packets = [(5 * MS, "90 3C 40 90 40 50"), (10 * MS, "80 3C 40")]
    lat = timing_lattice(write_capture(tmp_path, packets))
    assert lat.bundled_packets == 1
    assert lat.distinct_stamps == 2


# --------------------------------------------------------------------------
# the modulus of continuity
# --------------------------------------------------------------------------

def test_modulus_matches_the_closed_form_for_one_strike():
    """For ``f = P 1_[a,b]``, ``omega(delta) = P min(delta, b - a)`` exactly.

    Normalised by ``F(T) = P (b - a)`` this is ``min(delta / (b - a), 1)``,
    with no dependence on ``P`` at all -- the whole thing is homogeneous of
    degree 1 in the velocity.
    """
    perf = one_strike(0.25, 0.75, velocity=100)
    deltas = np.array([0.01, 0.1, 0.25, 0.5, 0.6])
    m = modulus_of_continuity(perf, deltas)
    expected = np.minimum(m["delta"] / 0.5, 1.0)
    assert m["omega"] == pytest.approx(expected, abs=1e-12)


def test_modulus_never_exceeds_the_lipschitz_bound():
    """``omega(delta) <= L delta`` is the theorem; violating it is a bug here."""
    strikes = [Strike(0.0, 0.4, 0, 90), Strike(0.1, 0.9, 5, 40),
               Strike(1.5, 1.7, 5, 120), Strike(2.0, 4.0, 12, 15)]
    by_key: dict[int, list[Strike]] = {}
    for s in strikes:
        by_key.setdefault(s.key, []).append(s)
    perf = Performance([KeyTrack(k, by_key.get(k, ())) for k in range(N_KEYS)],
                       t_start=0.0, t_end=5.0)
    m = modulus_of_continuity(perf, np.array([0.01, 0.05, 0.2, 1.0, 3.0]))
    assert np.all(m["omega"] <= m["lipschitz"] + 1e-12)


def test_lipschitz_constant_is_the_peak_of_the_sum():
    """``L = max_t sum_k f_k(t)``: two overlapping strikes add."""
    perf = Performance(
        [KeyTrack(k, [Strike(0.0, 1.0, k, 60)] if k in (0, 1) else ())
         for k in range(N_KEYS)], t_start=0.0, t_end=2.0)
    m = modulus_of_continuity(perf, np.array([0.1]))
    assert m["L"][0] == 120.0
    assert m["hold"][0] == pytest.approx(1.0)


def test_cantor_reference_is_the_devils_staircase_exponent():
    perf = one_strike(0.0, 1.0)
    m = modulus_of_continuity(perf, np.array([0.1]))
    assert CANTOR_EXPONENT == pytest.approx(np.log(2) / np.log(3))
    assert m["cantor"][0] == pytest.approx((0.1 / 1.0) ** CANTOR_EXPONENT)


# --------------------------------------------------------------------------
# the Fano curve
# --------------------------------------------------------------------------

def test_fano_is_zero_for_a_perfectly_regular_process():
    """One onset per window, every window: zero variance, so Fano = 0."""
    onsets = np.arange(0.0, 200.0, 1.0)
    f = fano_curve(onsets, np.array([1.0]))
    assert f["fano"][0] == pytest.approx(0.0, abs=1e-9)


def test_fano_is_about_one_for_the_matched_poisson_control():
    onsets = np.sort(np.random.default_rng(7).uniform(0, 200, 800))
    f = fano_curve(onsets, np.array([0.5, 1.0, 2.0]), seed=7)
    assert np.all(np.abs(f["fano_poisson"] - 1.0) < 0.5)


def test_fano_skips_windows_too_wide_to_estimate():
    """Fewer than three bins is not an estimate; the row is dropped, not faked."""
    onsets = np.arange(0.0, 10.0, 0.5)
    f = fano_curve(onsets, np.array([0.5, 100.0]))
    assert f["width"].tolist() == [0.5]


# --------------------------------------------------------------------------
# the two velocity channels
# --------------------------------------------------------------------------

def test_release_velocity_is_recovered_from_the_note_off(tmp_path):
    cap = write_capture(tmp_path, [
        (5 * MS, "90 3C 40"),    # C4 on, velocity 64
        (10 * MS, "90 40 20"),   # E4 on, velocity 32
        (20 * MS, "80 3C 0A"),   # C4 off, release velocity 10
        (30 * MS, "80 40 7F"),   # E4 off, release velocity 127
    ])
    v = velocity_channels(cap, Performance.from_capture(cap))
    assert v.strike.tolist() == [64.0, 32.0]
    assert v.release.tolist() == [10.0, 127.0]
    assert v.duration == pytest.approx([0.015, 0.020])
    assert not v.pedal_at_release.any()


def test_note_on_with_zero_velocity_closes_the_note(tmp_path):
    """The running-status convention, which the parser also honours."""
    cap = write_capture(tmp_path, [(5 * MS, "90 3C 40"),
                                   (25 * MS, "90 3C 00")])
    v = velocity_channels(cap, Performance.from_capture(cap))
    assert v.strike.tolist() == [64.0]
    assert v.release.tolist() == [0.0]


def test_pedal_state_at_release_is_recorded(tmp_path):
    cap = write_capture(tmp_path, [
        (5 * MS, "B0 40 7F"),    # pedal down
        (10 * MS, "90 3C 40"),
        (20 * MS, "80 3C 40"),   # released while the damper is off the string
        (30 * MS, "B0 40 00"),
    ])
    v = velocity_channels(cap, Performance.from_capture(cap))
    assert v.pedal_at_release.tolist() == [True]


def test_mutual_information_is_zero_on_independent_channels(tmp_path):
    """Deterministically independent bytes must show no shared information."""
    packets = []
    t = 5
    for i in range(64):
        strike = 8 + (i % 8) * 15
        release = 8 + (i // 8) * 15
        packets.append((t * MS, f"90 3C {strike:02X}"))
        packets.append(((t + 5) * MS, f"80 3C {release:02X}"))
        t += 10
    cap = write_capture(tmp_path, packets)
    v = velocity_channels(cap, Performance.from_capture(cap))
    mi, cap_bits = v.mutual_information(bins=8)
    assert mi == pytest.approx(0.0, abs=1e-9)
    assert cap_bits > 2.0
