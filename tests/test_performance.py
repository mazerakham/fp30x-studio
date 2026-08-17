"""Tests for :mod:`fp30x_studio.performance`, run against synthetic MIDI only.

Every fixture in this file is generated in-process by
:func:`fp30x_studio.performance.synthesize_midi`, so the suite is reproducible
on a machine with no piano attached.  The adversarial cases -- zero-length
notes, simultaneous strikes, legato overlap on one key, a pedal held across a
phrase, orphan note-ons and note-offs, out-of-range notes -- are the ones a
real capture produces and a naive parser gets wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from fp30x_studio import core
from fp30x_studio.performance import (
    N_KEYS,
    DisjointnessError,
    KeyTrack,
    ParseError,
    Performance,
    Strike,
    key_index,
    midi_note,
    quantisation_step,
    synthesize_midi,
)

# MIDI note numbers used throughout.
A0, C4, E4, G4, C5, C8 = 21, 60, 64, 67, 72, 108
TICKS, BPM = 960, 120.0
TICK = quantisation_step(TICKS, BPM)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def parse(strikes, pedal=(), **kw) -> Performance:
    """Synthesise a MIDI stream, then parse it back. The full round trip."""
    mid = synthesize_midi(strikes, pedal, ticks_per_beat=TICKS, bpm=BPM,
                          note_off_as_zero_velocity=kw.pop("zero_vel_off", False))
    t = 0.0
    msgs = []
    for msg in mid:
        t += msg.time
        if not msg.is_meta:
            msgs.append((t, msg))
    return Performance.from_messages(msgs, **kw)


def perf_from_strikes(strikes, pedal=(), **kw) -> Performance:
    """Build a Performance directly, bypassing MIDI: exact times, no quantisation."""
    by_key: dict[int, list[Strike]] = {}
    for s in strikes:
        by_key.setdefault(s.key, []).append(s)
    tracks = [KeyTrack(k, by_key.get(k, ()), **kw) for k in range(N_KEYS)]
    return Performance(tracks, pedal)


def aggregate_on_grid(perf: Performance, ts: np.ndarray) -> np.ndarray:
    """Scalar aggregate ``sum_k f_k(t)`` on a grid, for quadrature comparison."""
    out = np.zeros(ts.size)
    for s in perf.strikes():
        out[(ts >= s.t_on) & (ts <= s.t_off)] += s.velocity
    return out


def phrase() -> tuple[list[tuple[int, float, float, int]], list[tuple[float, float]]]:
    """A deterministic synthetic phrase with a silence in the middle.

    Two chords, a monophonic run, a rest, then a repeated note; the pedal is
    held across the run only.  Chosen so the cumulative function has genuine
    plateaus (the rest, and the lead-in) and clear rises.
    """
    notes = [
        # C major triad, struck together, held 0.8 s
        (C4, 0.50, 1.30, 90), (E4, 0.50, 1.30, 82), (G4, 0.50, 1.30, 76),
        # a run, legato-ish, under the pedal
        (C5, 1.60, 1.80, 100), (C5 + 2, 1.78, 1.98, 96),
        (C5 + 4, 1.96, 2.16, 104), (C5 + 5, 2.14, 2.34, 88),
        # silence from 2.34 to 3.20
        # a repeated note: struck, released, struck again immediately
        (G4, 3.20, 3.60, 70), (G4, 3.60, 4.00, 118),
        # a low pedal tone under it
        (A0, 3.20, 4.00, 64),
    ]
    pedal = [(1.55, 2.40)]
    return notes, pedal


# --------------------------------------------------------------------------
# the indicator model
# --------------------------------------------------------------------------

def test_single_strike_is_a_scaled_indicator():
    s = Strike(t_on=1.0, t_off=2.0, key=key_index(C4), velocity=64)
    assert s(0.999) == 0.0
    assert s(1.0) == 64.0  # closed interval: the endpoint is in
    assert s(1.5) == 64.0
    assert s(2.0) == 64.0
    assert s(2.001) == 0.0
    assert s.duration == 1.0
    assert s.energy() == 64.0
    assert s.name == "C4" == core.note_name(C4)


def test_total_variation_of_one_strike_is_twice_the_velocity():
    p = perf_from_strikes([Strike(1.0, 2.0, key_index(C4), 64)])
    assert p.tracks[key_index(C4)].total_variation() == pytest.approx(128.0)
    assert p.total_variation("l1") == pytest.approx(128.0)
    assert p.support_measure() == pytest.approx(1.0)


def test_degenerate_strike_is_the_zero_element_of_L1():
    """``P * 1_[a,a]`` has null support: zero variation, zero energy, no plateau break."""
    p = perf_from_strikes([Strike(1.0, 1.0, key_index(C4), 90)])
    assert p.n_strikes == 1
    assert p.strikes()[0].is_degenerate
    assert p.total_variation("l1") == 0.0
    assert p.support_measure() == 0.0
    assert p.energy() == 0.0
    assert p.cumulative(5.0) == 0.0
    assert p.tracks[key_index(C4)].jump_measure() == []


def test_zero_length_notes_survive_the_midi_round_trip():
    notes = [(C4, 1.0, 1.0, 90), (E4, 1.0, 1.0, 45), (G4, 2.0, 2.5, 70)]
    p = parse(notes)
    assert p.report.degenerate == 2
    assert p.n_strikes == 3
    degenerate = [s for s in p.strikes() if s.is_degenerate]
    assert {s.note for s in degenerate} == {C4, E4}
    assert p.energy() == pytest.approx(70 * 0.5, abs=1e-2)


# --------------------------------------------------------------------------
# the disjointness invariant
# --------------------------------------------------------------------------

def test_overlapping_intervals_on_one_key_raise():
    with pytest.raises(DisjointnessError, match="not a.e. disjoint"):
        KeyTrack(key_index(C4), [Strike(0.0, 1.0, key_index(C4), 80),
                                 Strike(0.5, 1.5, key_index(C4), 80)])


def test_touching_intervals_pass_weak_and_fail_strict():
    """``[0,1]`` and ``[1,2]`` meet in a null set: a.e. disjoint, not disjoint."""
    strikes = [Strike(0.0, 1.0, key_index(C4), 80), Strike(1.0, 2.0, key_index(C4), 90)]
    track = KeyTrack(key_index(C4), strikes)  # weak grade: fine
    track.check_disjoint(strict=False)
    with pytest.raises(DisjointnessError, match="not strictly disjoint"):
        track.check_disjoint(strict=True)


def test_disjointness_holds_across_the_whole_synthetic_phrase():
    notes, pedal = phrase()
    p = parse(notes, pedal)
    p.check_disjoint()                 # weak grade, the parser's guarantee
    p.with_sustain().check_disjoint()  # and it survives pedal extension


def test_the_same_key_at_different_times_is_not_a_violation():
    p = perf_from_strikes([Strike(0.0, 1.0, key_index(C4), 80),
                           Strike(2.0, 3.0, key_index(C4), 80)])
    p.check_disjoint(strict=True)


def test_simultaneous_strikes_on_different_keys_are_fine():
    """Disjointness is a per-key invariant; a chord is not a violation."""
    chord = [Strike(0.5, 1.5, key_index(n), 90) for n in (C4, E4, G4)]
    p = perf_from_strikes(chord)
    p.check_disjoint(strict=True)
    assert p.polyphony_at(1.0) == 3
    assert p.max_polyphony() == 3
    np.testing.assert_allclose(p.evaluate(1.0).sum(), 270.0)


# --------------------------------------------------------------------------
# parsing: pairing, orphans, retrigger, range
# --------------------------------------------------------------------------

def test_legato_overlap_on_one_key_is_truncated_at_the_new_onset():
    """A re-strike resets the string, so the previous interval ends there."""
    msgs = _raw([(0.0, "on", C4, 80), (1.0, "on", C4, 100), (2.0, "off", C4, 0)])
    p = Performance.from_messages(msgs)
    assert p.report.retrigger_truncated == 1
    ss = p.tracks[key_index(C4)].strikes
    assert len(ss) == 2
    assert (ss[0].t_on, ss[0].t_off, ss[0].velocity) == (0.0, 1.0, 80)
    assert (ss[1].t_on, ss[1].t_off, ss[1].velocity) == (1.0, 2.0, 100)
    p.check_disjoint()  # weak grade holds by construction


def test_retrigger_policy_raise_refuses_instead():
    msgs = _raw([(0.0, "on", C4, 80), (1.0, "on", C4, 100), (2.0, "off", C4, 0)])
    with pytest.raises(ParseError, match="re-struck"):
        Performance.from_messages(msgs, retrigger_policy="raise")


def test_orphan_note_on_is_truncated_at_the_end_by_default():
    msgs = _raw([(0.0, "on", C4, 80), (1.0, "on", E4, 80), (1.5, "off", E4, 0)])
    p = Performance.from_messages(msgs)
    assert p.report.orphan_note_on == 1
    assert p.tracks[key_index(C4)].strikes[0].t_off == 1.5
    assert not p.report.clean


def test_orphan_note_on_can_be_dropped_or_refused():
    msgs = _raw([(0.0, "on", C4, 80), (1.0, "off", E4, 0)])
    dropped = Performance.from_messages(msgs, orphan_policy="drop")
    assert dropped.n_strikes == 0
    with pytest.raises(ParseError, match="still held"):
        Performance.from_messages(msgs, orphan_policy="raise")


def test_orphan_note_off_is_discarded():
    msgs = _raw([(0.0, "off", C4, 0), (1.0, "on", E4, 80), (2.0, "off", E4, 0)])
    p = Performance.from_messages(msgs)
    assert p.report.orphan_note_off == 1
    assert p.n_strikes == 1
    assert p.strikes()[0].note == E4


def test_note_on_with_zero_velocity_closes_the_key():
    """The running-status convention: ``note_on`` velocity 0 means release."""
    p = parse([(C4, 0.0, 1.0, 80)], zero_vel_off=True)
    assert p.n_strikes == 1
    assert p.report.n_note_off == 1
    assert p.strikes()[0].t_off == pytest.approx(1.0, abs=2 * TICK)


def test_notes_off_the_88_key_range_are_dropped_or_refused():
    msgs = _raw([(0.0, "on", 20, 80), (0.5, "off", 20, 0),
                 (1.0, "on", 109, 80), (1.5, "off", 109, 0),
                 (2.0, "on", C4, 80), (2.5, "off", C4, 0)])
    p = Performance.from_messages(msgs)
    assert p.report.out_of_range == 4
    assert p.n_strikes == 1
    with pytest.raises(ParseError, match="off the 88-key range"):
        Performance.from_messages(msgs, range_policy="raise")


def test_key_index_covers_exactly_the_88_keys():
    assert key_index(A0) == 0 and key_index(C8) == N_KEYS - 1
    assert midi_note(0) == A0 and midi_note(N_KEYS - 1) == C8
    for bad in (A0 - 1, C8 + 1):
        with pytest.raises(ValueError):
            key_index(bad)


def test_midi_round_trip_recovers_the_intervals():
    notes, pedal = phrase()
    p = parse(notes, pedal)
    assert p.n_strikes == len(notes)
    got = {(s.note, round(s.t_on, 4), round(s.t_off, 4), s.velocity)
           for s in p.strikes()}
    for note, a, b, vel in notes:
        match = [g for g in got if g[0] == note and abs(g[1] - a) <= 2 * TICK]
        assert match, f"no strike recovered for note {note} at {a}"
        assert any(abs(g[2] - b) <= 2 * TICK and g[3] == vel for g in match)


# --------------------------------------------------------------------------
# functionals
# --------------------------------------------------------------------------

def test_cumulative_closed_form_agrees_with_quadrature():
    """(5) is exact; check it against trapezoidal quadrature of the step function."""
    notes, _ = phrase()
    p = parse(notes)
    grid = np.arange(0.0, p.t_end + 1e-9, 1e-5)
    f = aggregate_on_grid(p, grid)
    for t in (0.0, 0.75, 1.30, 1.9, 2.34, 3.0, 3.75, p.t_end):
        j = int(np.searchsorted(grid, t))
        quad = np.trapezoid(f[:j + 1], grid[:j + 1]) if j else 0.0
        assert p.cumulative(t) == pytest.approx(quad, abs=0.05)


def test_cumulative_per_key_sums_to_the_aggregate():
    notes, _ = phrase()
    p = parse(notes)
    for t in np.linspace(0.0, p.t_end, 25):
        assert sum(p.cumulative(t, key=k) for k in range(N_KEYS)) == \
            pytest.approx(p.cumulative(t))


def test_cumulative_is_monotone_and_ends_at_the_total_energy():
    notes, _ = phrase()
    p = parse(notes)
    ts, fs = p.cumulative_curve()
    assert np.all(np.diff(fs) >= -1e-12)
    assert fs[0] == pytest.approx(0.0)
    assert fs[-1] == pytest.approx(p.energy())
    assert p.energy() == pytest.approx(sum(v * (b - a) for _, a, b, v in notes),
                                       abs=0.05)


def test_the_cumulative_polyline_is_exact_not_sampled():
    """Between breakpoints F is affine, so linear interpolation must be exact."""
    notes, _ = phrase()
    p = parse(notes)
    ts, fs = p.cumulative_curve()
    mids = (ts[:-1] + ts[1:]) / 2
    interp = np.interp(mids, ts, fs)
    exact = np.array([p.cumulative(t) for t in mids])
    np.testing.assert_allclose(interp, exact, atol=1e-9)


def test_plateaus_are_exactly_the_silences():
    notes, _ = phrase()
    p = parse(notes)
    plats = p.plateaus()
    # the rest between 2.34 and 3.20 is the one interior plateau
    assert any(abs(a - 2.34) <= 2 * TICK and abs(b - 3.20) <= 2 * TICK
               for a, b in plats)
    # F really is constant there, and strictly increasing just outside
    for a, b in plats:
        assert p.cumulative(a) == pytest.approx(p.cumulative(b))
    span = p.t_end - p.t_start
    assert p.plateau_measure() + p.support_measure() == pytest.approx(span, abs=1e-9)


def test_support_measure_is_a_union_not_a_sum():
    """Three keys held over the same second occupy one second of support."""
    chord = [Strike(0.0, 1.0, key_index(n), 90) for n in (C4, E4, G4)]
    p = perf_from_strikes(chord)
    assert p.support_measure() == pytest.approx(1.0)
    assert sum(tr.support_measure() for tr in p.tracks) == pytest.approx(3.0)


def test_total_variation_norm_ordering_and_l1_decomposition():
    notes, _ = phrase()
    p = parse(notes)
    l1, l2, linf = (p.total_variation(n) for n in ("l1", "l2", "linf"))
    assert l1 >= l2 >= linf > 0
    # l1 of the vector measure decomposes over components exactly
    assert l1 == pytest.approx(sum(tr.total_variation() for tr in p.tracks))
    # a chord's three simultaneous onsets are one atom in R^88
    chord = perf_from_strikes([Strike(0.0, 1.0, key_index(n), 90)
                               for n in (C4, E4, G4)])
    assert chord.total_variation("l1") == pytest.approx(6 * 90)
    assert chord.total_variation("l2") == pytest.approx(2 * 90 * np.sqrt(3))
    assert chord.total_variation("linf") == pytest.approx(2 * 90)


def test_polyphony_counts_and_steps():
    notes, _ = phrase()
    p = parse(notes)
    assert p.polyphony_at(1.0) == 3          # the triad
    assert p.polyphony_at(2.8) == 0          # the rest
    assert p.polyphony_at(3.4) == 2          # G4 over the A0 pedal tone
    ts, counts = p.polyphony_steps()
    assert counts[-1] == 0                   # everything has been released
    assert counts.max() >= 3
    assert p.max_polyphony() >= 3
    # the segment values agree with the closed-interval count in segment interiors
    for j in range(len(ts) - 1):
        mid = (ts[j] + ts[j + 1]) / 2
        assert p.polyphony_at(mid) == counts[j]


def test_the_direct_sum_vector_lives_in_R88():
    notes, _ = phrase()
    p = parse(notes)
    v = p.evaluate(1.0)
    assert v.shape == (N_KEYS,)
    assert np.count_nonzero(v) == 3
    assert v[key_index(C4)] == 90
    ts = np.linspace(0.0, p.t_end, 200)
    many = p.evaluate_many(ts)
    assert many.shape == (200, N_KEYS)
    for i, t in enumerate(ts):
        np.testing.assert_allclose(many[i], p.evaluate(t))


# --------------------------------------------------------------------------
# sustain pedal (CC64)
# --------------------------------------------------------------------------

def test_pedal_held_across_a_phrase_extends_every_release_inside_it():
    notes = [(C4, 0.0, 0.2, 80), (E4, 0.3, 0.5, 80), (G4, 0.6, 0.8, 80)]
    pedal = [(0.1, 1.5)]
    p = parse(notes, pedal)
    assert len(p.pedal) == 1
    assert p.report.pedal_events == 2
    sounding = p.with_sustain()
    assert sounding.sounding and not p.sounding
    for s in sounding.strikes():
        assert s.t_off == pytest.approx(1.5, abs=2 * TICK)
    # sound outlasts the keys, so support and energy both grow
    assert sounding.support_measure() > p.support_measure()
    assert sounding.energy() > p.energy()
    sounding.check_disjoint()


def test_a_release_outside_the_pedal_span_is_untouched():
    notes = [(C4, 0.0, 0.5, 80), (E4, 2.0, 2.5, 80)]
    pedal = [(0.1, 1.0)]
    p = parse(notes, pedal)
    s = p.with_sustain()
    c4 = s.tracks[key_index(C4)].strikes[0]
    e4 = s.tracks[key_index(E4)].strikes[0]
    assert c4.t_off == pytest.approx(1.0, abs=2 * TICK)   # extended
    assert e4.t_off == pytest.approx(2.5, abs=2 * TICK)   # not extended


def test_restriking_under_the_pedal_truncates_the_ringing_note():
    """The hammer resets the string, so the invariant survives pedal extension."""
    notes = [(C4, 0.0, 0.2, 80), (C4, 0.6, 0.8, 110)]
    pedal = [(0.0, 3.0)]
    p = parse(notes, pedal)
    s = p.with_sustain()
    ss = s.tracks[key_index(C4)].strikes
    assert len(ss) == 2
    assert ss[0].t_off == pytest.approx(ss[1].t_on, abs=2 * TICK)
    assert ss[1].t_off == pytest.approx(3.0, abs=2 * TICK)
    s.check_disjoint()


def test_an_unclosed_pedal_is_reported_and_closed():
    mid = synthesize_midi([(C4, 0.0, 1.0, 80)], ticks_per_beat=TICKS, bpm=BPM)
    import mido
    mid.tracks[0].append(mido.Message("control_change", control=64, value=127, time=0))
    t = 0.0
    msgs = []
    for m in mid:
        t += m.time
        if not m.is_meta:
            msgs.append((t, m))
    p = Performance.from_messages(msgs)
    assert p.report.pedal_unclosed == 1
    assert len(p.pedal) == 1


def test_with_sustain_is_the_identity_when_there_is_no_pedal():
    notes, _ = phrase()
    p = parse(notes)
    s = p.with_sustain()
    assert [tuple(x) for x in s.to_array()] == [tuple(x) for x in p.to_array()]


# --------------------------------------------------------------------------
# export and reporting
# --------------------------------------------------------------------------

def test_export_to_structured_array_and_records():
    notes, pedal = phrase()
    p = parse(notes, pedal)
    arr = p.to_array()
    assert arr.shape == (len(notes),)
    assert arr.dtype.names == ("key", "note", "name", "t_on", "t_off",
                               "duration", "velocity", "energy")
    assert np.all(arr["duration"] >= 0)
    assert np.all(arr["t_on"][:-1] <= arr["t_on"][1:])   # performance order
    np.testing.assert_allclose(arr["energy"],
                               arr["velocity"] * arr["duration"], atol=1e-12)
    assert arr["name"][0] == core.note_name(int(arr["note"][0]))
    recs = p.to_records()
    assert len(recs) == len(notes) and recs[0]["velocity"] == arr["velocity"][0]


def test_a_clean_stream_reports_clean_and_a_messy_one_does_not():
    clean = parse([(C4, 0.0, 1.0, 80), (E4, 1.0, 2.0, 80)])
    assert clean.report.clean and not clean.report.warnings
    messy = Performance.from_messages(_raw([(0.0, "on", C4, 80),
                                            (1.0, "on", C4, 90)]))
    assert not messy.report.clean
    assert messy.report.warnings


def test_summary_mentions_the_headline_functionals():
    notes, pedal = phrase()
    text = parse(notes, pedal).summary()
    for token in ("max polyphony", "support measure", "variation", "energy"):
        assert token in text


def test_an_empty_performance_is_well_defined():
    p = Performance.from_messages([])
    assert p.n_strikes == 0 and p.active_keys() == []
    assert p.total_variation("l1") == 0.0
    assert p.support_measure() == 0.0
    assert p.max_polyphony() == 0
    assert p.cumulative(1.0) == 0.0
    assert p.evaluate(1.0).shape == (N_KEYS,)
    assert p.to_array().shape == (0,)
    p.check_disjoint(strict=True)


# --------------------------------------------------------------------------
# raw message construction (no MIDI file, exact times)
# --------------------------------------------------------------------------

def test_cantor_phrase_has_the_middle_thirds_structure():
    """Depth 4 leaves 2^4 intervals; the plateaus inherit the gap hierarchy."""
    from fp30x_studio.figures import cantor_phrase, demo_performance

    notes, pedal = cantor_phrase(depth=4, span=24.0)
    assert len(notes) == 3 * 2 ** 4
    p = demo_performance(depth=4, span=24.0)
    p.check_disjoint()
    assert p.n_strikes == len(notes)
    # the removed middle third of [0, 24] is one plateau, and the widest one
    widest = max(p.plateaus(), key=lambda ab: ab[1] - ab[0])
    assert widest[0] == pytest.approx(8.0, abs=0.3)
    assert widest[1] == pytest.approx(16.0, abs=0.3)
    # every plateau really is flat, and F rises strictly across the whole span
    for a, b in p.plateaus():
        assert p.cumulative(a) == pytest.approx(p.cumulative(b))
    assert p.cumulative(p.t_end) > p.cumulative(p.t_start) == 0.0


def test_the_figure_renders_to_a_png(tmp_path):
    from fp30x_studio.figures import render

    out = render(tmp_path / "fig.png", dpi=60)
    assert out.exists() and out.stat().st_size > 20_000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def _raw(spec):
    """``[(t, "on"|"off", note, velocity)]`` -> ``[(t, mido.Message)]``, unquantised."""
    import mido
    out = []
    for t, kind, note, vel in spec:
        if kind == "on":
            out.append((t, mido.Message("note_on", note=note, velocity=vel)))
        else:
            out.append((t, mido.Message("note_off", note=note, velocity=64)))
    return out
