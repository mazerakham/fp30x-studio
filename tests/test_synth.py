"""Tests for :mod:`fp30x_studio.synth`.

Synthesis is unusually easy to write tests for that pass while the output is
rubbish, so these pin the things that are *claims* rather than the things that
are merely code: that the preset schema is exactly the one shared with the Web
Audio workbench, that the partial series is stretched and bandlimited, that the
release byte actually changes how fast a note dies, that the pedal integral has
the behaviour the damper argument depends on, and that the tine model is
different in kind from the string model rather than the same series retuned.

Nothing here asserts that the result sounds like a piano. It cannot; that is a
judgement only a listener can make.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from fp30x_studio.synth import engine, model, voices
from fp30x_studio.synth.model import PRESET_DIR, PRESET_KEYS, Preset, load_preset
from fp30x_studio.synth.score import Note, Score

TAKES = Path("~/Music/FP-30X Studio/takes").expanduser()
PIECE = TAKES / "2026-08-17-piece.fp30"
SR = 48000


def _rng():
    return np.random.default_rng(7)


def _flat_pedal(position: int, preset: Preset, end: float = 30.0):
    s = Score(name="t", duration=end, pedal=[(0.0, position)])
    return engine.damper_integral(s, preset, end)


# -- the shared schema ------------------------------------------------------

def test_shipped_presets_carry_exactly_the_shared_schema():
    for name in ("acoustic", "electric"):
        raw = json.loads((PRESET_DIR / f"{name}.json").read_text())
        keys = set(raw) - {"name", "note"}
        assert keys == set(PRESET_KEYS), (
            f"{name}.json disagrees with the schema the workbench also uses; "
            f"missing {set(PRESET_KEYS) - keys}, extra {keys - set(PRESET_KEYS)}")


def test_preset_round_trips_through_json_unchanged():
    for name in ("acoustic", "electric"):
        p = load_preset(name)
        assert Preset.from_json(p.to_json()) == p


def test_a_preset_exported_from_the_workbench_loads_unchanged():
    """The workbench wraps the shared parameters in an envelope and adds its
    own page-only controls. That whole payload has to land here as a preset,
    or the two implementations are not actually sharing anything."""
    exported = {
        "schema": "fp30x-timbre/1",
        "generated": "2026-08-17T22:41:03.117Z",
        "source": "docs/timbre-workbench.html",
        "params": {k: v for k, v in load_preset("acoustic").to_dict().items()
                   if k in PRESET_KEYS},
        "page_only": {"morph": 0.5, "layer": 0.0, "master_gain": 0.8,
                      "voice_cap": 32,
                      "note": "layer/morph/gain/cap are page controls"},
    }
    p = Preset.from_dict(exported)
    assert p.model == "string"
    for k in PRESET_KEYS:
        assert getattr(p, k) == getattr(load_preset("acoustic"), k)
    assert "timbre-workbench" in p.note


def test_preset_rejects_a_key_the_model_does_not_define():
    with pytest.raises(ValueError, match="does not define"):
        Preset.from_dict({"model": "string", "resonance": 0.5})


def test_the_two_presets_use_the_two_different_models():
    assert load_preset("acoustic").model == "string"
    assert load_preset("electric").model == "tine"


# -- the partial series -----------------------------------------------------

def test_inharmonicity_law_reproduces_the_measured_steinway():
    """The B law is checked against the notes it was fitted to, not against a
    remembered rule of thumb. Values are the per-note medians measured on the
    Iowa Steinway B (~/workspace/audio/timbre-data/iowa-fits.json); the
    tolerance is the fit's own residual scatter, a factor of 1.4."""
    p = load_preset("acoustic")
    measured = {48: 1.165e-4, 54: 1.805e-4, 60: 3.278e-4,
                66: 5.766e-4, 72: 1.045e-3, 84: 2.423e-3}
    for midi, want in measured.items():
        got = model.inharmonicity_at(midi, p.inharmonicity_B,
                                     p.inharmonicity_decades_per_octave,
                                     p.inharmonicity_floor)
        assert 1 / 1.4 < got / want < 1.4, (midi, got, want)


def test_inharmonicity_floors_below_the_bass_break():
    """The exponential does not extend into the wound strings. Measured B is
    flat at ~1.4e-4 from MIDI 30 to 48; extrapolating the treble law gives
    2.3e-5 at C1, sixteen times too small."""
    p = load_preset("acoustic")
    at = lambda m: model.inharmonicity_at(m, p.inharmonicity_B,
                                          p.inharmonicity_decades_per_octave,
                                          p.inharmonicity_floor)
    assert at(24) == pytest.approx(p.inharmonicity_floor)
    assert at(36) == pytest.approx(p.inharmonicity_floor)
    assert at(84) > 10 * p.inharmonicity_floor


def test_rolloff_steepens_with_pitch():
    """One rolloff for the keyboard was the largest error in the guessed
    preset: measured, the bass is nearly flat and the treble is steep."""
    p = load_preset("acoustic")
    at = lambda m: model.rolloff_at(m, p.partial_amp_rolloff, p.rolloff_per_octave)
    assert at(24) < 1.0
    assert at(96) > 4.0
    assert at(69) == pytest.approx(p.partial_amp_rolloff)


def test_damper_leaks_at_full_pedal_and_has_an_escapement():
    """Two defects the old linear map had. It reached exactly zero at cc64=127
    -- and cc64 was 127 for ~90% of his playing time, so the damper was not
    weak then, it was off -- and it had no escapement, when the mechanism has a
    knee partway down the travel."""
    d0 = model.damper_contact(0)
    d_full = model.damper_contact(127)
    assert d0 == pytest.approx(1.0)
    assert 0 < d_full < 0.1                     # leaks, never switches off
    # flat through the top of the travel, then a knee
    assert model.damper_contact(20) == pytest.approx(d0)
    assert model.damper_contact(70) < d0
    assert model.damper_contact(70) > model.damper_contact(90)


def test_partials_are_stretched_sharp_and_bandlimited():
    f0 = model.note_frequency = voices.note_frequency
    f = voices.note_frequency(48)
    n, freqs = model.partial_frequencies(f, 32, 3e-4, SR / 2)
    assert np.all(freqs < SR / 2)
    assert np.all(freqs >= n * f - 1e-9)
    # A stiff string is measurably sharp by the top of the series, and that
    # stretch is the whole point of carrying B at all.
    assert freqs[-1] / (n[-1] * f) > 1.01


def test_no_partial_of_the_top_note_aliases():
    f = voices.note_frequency(89)
    _, freqs = model.partial_frequencies(f, 64, 1e-3, SR / 2)
    assert freqs.size >= 1
    assert freqs.max() < SR / 2


# -- release velocity, the point of the exercise ----------------------------

def test_release_tau_is_monotone_and_hits_both_preset_endpoints():
    p = load_preset("acoustic")
    taus = [voices.release_tau(v, p) for v in range(128)]
    assert all(b <= a for a, b in zip(taus, taus[1:])), "faster release must damp faster"
    assert taus[127] == pytest.approx(p.release_ms_fast / 1000.0)
    assert taus[0] == pytest.approx(p.release_ms_slow / 1000.0)


@pytest.mark.parametrize("preset_name", ["acoustic", "electric"])
def test_release_velocity_changes_how_much_sound_survives_the_release(preset_name):
    """The same note, released fast and released slowly, with the pedal up."""
    p = load_preset(preset_name)
    ped_t, ped_D = _flat_pedal(0, p)
    out = {}
    for vrel in (1, 127):
        buf = voices.VOICES[p.model](60, 80, vrel, 0.0, 1.0, p, SR,
                                     ped_t, ped_D, _rng())
        tail = buf[int(1.05 * SR): int(1.35 * SR)]
        out[vrel] = float(np.sqrt(np.mean(tail.astype(np.float64) ** 2)))
    assert out[1] > out[127] * 2.0, (
        f"release velocity is doing nothing audible: {out}")


# -- the damper integral ----------------------------------------------------

def test_pedal_down_almost_but_not_entirely_defeats_the_damper():
    """It used to be *entirely*, and that was the defect. cc64 sat at 127 for
    86.6-91.4% of the playing time on his takes, so a damper that switches off
    at the top of the travel is switched off for almost the whole piece and
    every pedalled note runs to the model's tail limit. Felt leaks; the residual
    is small but it is not zero."""
    p = load_preset("acoustic")
    ped_t, ped_D = _flat_pedal(127, p)
    rate = float(np.interp(1.0, ped_t, ped_D))
    assert 0.0 < rate < 0.1, "full pedal must leak, not switch the damper off"
    assert rate == pytest.approx(p.pedal_leak, rel=1e-9)


def test_pedal_up_integrates_at_unit_rate():
    p = load_preset("acoustic")
    ped_t, ped_D = _flat_pedal(0, p)
    assert float(np.interp(5.0, ped_t, ped_D)) == pytest.approx(5.0, rel=1e-9)


def test_half_pedal_is_a_middle_damping_rate_not_a_switch():
    p = load_preset("acoustic")
    rates = []
    for pos in (0, 32, 64, 96, 127):
        t, D = _flat_pedal(pos, p)
        rates.append(float(np.interp(1.0, t, D)))
    assert rates == sorted(rates, reverse=True)
    assert 0.0 < rates[2] < rates[0], "CC64 = 64 must not behave like CC64 = 0"
    assert rates[2] == pytest.approx(
        model.damper_contact(64, p.pedal_engage, p.pedal_knee, p.pedal_leak,
                             p.damper_cc64_scale), rel=1e-9)


def test_the_damper_takes_the_top_of_the_spectrum_first():
    """The bug this pass exists to fix. The damper used to be one scalar
    exponential applied to the summed buffer, so a note under the felt kept its
    high partials at full relative weight all the way to silence -- which is
    the one thing a real string certainly does not do. With the per-partial
    n^q law the damped tail must be darker than the free tail."""
    p = load_preset("acoustic")
    ped_t, ped_D = _flat_pedal(0, p)          # pedal up: the damper is on

    def centroid(buf):
        x = buf.astype(np.float64)
        sp = np.abs(np.fft.rfft(x * np.hanning(x.size)))
        fr = np.fft.rfftfreq(x.size, 1.0 / SR)
        return float((sp * fr).sum() / max(sp.sum(), 1e-30))

    damped = voices.render_string(60, 90, 100, 0.0, 0.20, p, SR,
                                  ped_t, ped_D, _rng())
    free = voices.render_string(60, 90, 100, 0.0, 60.0, p, SR,
                                ped_t, ped_D, _rng())
    a, b = int(0.30 * SR), int(0.45 * SR)
    assert damped.size > b and free.size > b
    assert centroid(damped[a:b]) < centroid(free[a:b]) * 0.95


def test_a_brief_pedal_lift_bites_even_though_it_lasts_15_ms():
    """769 of his pedal excursions were 15 ms median, 30 ms maximum -- the
    FP-30X never reports a sustained intermediate position. Under a pure
    dwell integral a 15 ms landing contributes nothing; physically it takes
    real energy out. The contact term is what makes it count."""
    p = load_preset("acoustic")

    class _S:
        duration = 4.0
        pedal = [(0.0, 127), (1.000, 0), (1.015, 127)]

    t, D = engine.damper_integral(_S(), p, 4.0)
    got = float(np.interp(2.0, t, D))
    dwell = 0.015 * 1.0 + (2.0 - 0.015) * p.pedal_leak
    assert got > dwell, "a 15 ms felt landing must dump more than its dwell"
    assert got == pytest.approx(dwell + p.pedal_contact_bite, rel=1e-6)


def test_a_note_released_under_full_pedal_keeps_ringing():
    p = load_preset("acoustic")
    down = voices.render_string(60, 80, 127, 0.0, 0.5, p, SR, *_flat_pedal(127, p),
                                rng=_rng())
    up = voices.render_string(60, 80, 127, 0.0, 0.5, p, SR, *_flat_pedal(0, p),
                              rng=_rng())
    assert down.size > up.size * 3, (
        "with the pedal down the note must outlive its own release; "
        f"got {down.size / SR:.2f} s vs {up.size / SR:.2f} s")


def test_lifting_the_pedal_after_the_release_damps_the_note_then():
    """780 of 804 releases on the open take happened with the pedal already
    down, so the moment that ends a note is usually a later CC64 message."""
    p = load_preset("acoustic")
    s = Score(name="t", duration=10.0, pedal=[(0.0, 127), (2.0, 0)])
    ped_t, ped_D = engine.damper_integral(s, p, 10.0)
    buf = voices.render_string(60, 80, 100, 0.0, 0.5, p, SR, ped_t, ped_D, _rng())
    before = buf[int(1.5 * SR): int(1.9 * SR)].astype(np.float64)
    after = buf[int(2.3 * SR): int(2.7 * SR)].astype(np.float64)
    assert np.sqrt((before ** 2).mean()) > 20 * np.sqrt((after ** 2).mean())


# -- velocity moves the timbre, not only the level --------------------------

def _centroid(buf: np.ndarray, sr: int = SR) -> float:
    x = buf[: sr // 2].astype(np.float64)
    if x.size < 1024:
        return 0.0
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    f = np.fft.rfftfreq(x.size, 1 / sr)
    return float((spec * f).sum() / max(spec.sum(), 1e-30))


def test_a_harder_strike_is_brighter_not_only_louder():
    p = load_preset("acoustic")
    ped_t, ped_D = _flat_pedal(0, p)
    soft = voices.render_string(60, 20, 64, 0.0, 2.0, p, SR, ped_t, ped_D, _rng())
    hard = voices.render_string(60, 96, 64, 0.0, 2.0, p, SR, ped_t, ped_D, _rng())
    c_soft, c_hard = _centroid(soft), _centroid(hard)
    assert c_hard > c_soft * 1.25, (
        f"velocity_brightness is not moving the centroid: {c_soft:.0f} -> {c_hard:.0f} Hz")
    # and it is genuinely louder as well
    assert np.abs(hard).max() > np.abs(soft).max()


def test_velocity_is_normalised_to_the_range_the_action_actually_sends():
    assert model.VELOCITY_FULL_SCALE == 103.0
    assert model.velocity_norm(103) == pytest.approx(1.0)
    assert model.velocity_norm(127) == pytest.approx(1.0), "must clamp, not exceed"


# -- the tine is a different instrument, not a retuned string ---------------

def test_the_tine_is_near_sinusoidal_when_struck_softly_and_not_when_struck_hard():
    p = load_preset("electric")
    ped_t, ped_D = _flat_pedal(0, p)

    def fundamental_fraction(vel: int) -> float:
        buf = voices.render_tine(60, vel, 64, 0.0, 2.0, p, SR, ped_t, ped_D, _rng())
        x = buf[: SR // 2].astype(np.float64)
        spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
        f = np.fft.rfftfreq(x.size, 1 / SR)
        f0 = voices.note_frequency(60)
        near = np.abs(f - f0) < 25.0
        return float(spec[near].sum() / max(spec.sum(), 1e-30))

    soft, hard = fundamental_fraction(8), fundamental_fraction(100)
    assert soft > hard * 1.5, (
        f"the FM index is not tracking velocity: {soft:.3f} vs {hard:.3f}")


def test_the_tine_has_a_strong_partial_where_no_harmonic_of_a_string_is():
    p = load_preset("electric")
    ped_t, ped_D = _flat_pedal(0, p)
    buf = voices.render_tine(60, 90, 64, 0.0, 2.0, p, SR, ped_t, ped_D, _rng())
    x = buf[: SR].astype(np.float64)
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    f = np.fft.rfftfreq(x.size, 1 / SR)
    f0 = voices.note_frequency(60)
    f_bell = p.bell_partial * f0
    in_bell = spec[np.abs(f - f_bell) < 20.0].max()
    # nearest integer harmonics either side, which is where a string would put
    # its energy and the bar does not
    h6 = spec[np.abs(f - 6 * f0) < 20.0].max()
    h7 = spec[np.abs(f - 7 * f0) < 20.0].max()
    assert in_bell > 3 * max(h6, h7), (
        "the bell partial should not be sitting on a harmonic")


def test_the_tine_sustains_longer_than_the_string_at_the_same_pitch():
    a, e = load_preset("acoustic"), load_preset("electric")
    s = voices.render_string(60, 80, 1, 0.0, 60.0, a, SR, *_flat_pedal(0, a),
                             rng=_rng())
    t = voices.render_tine(60, 80, 1, 0.0, 60.0, e, SR, *_flat_pedal(0, e),
                           rng=_rng())
    def survives(buf, at=8.0):
        """RMS at ``at`` seconds, against the note's own peak."""
        w = SR // 4
        head = buf[:w].astype(np.float64)
        late = buf[int(at * SR): int(at * SR) + w].astype(np.float64)
        return float(np.sqrt((late ** 2).mean()) / np.sqrt((head ** 2).mean()))

    assert survives(t) > survives(s) * 2.0, (
        f"the tine should still be ringing when the string has gone: "
        f"{survives(t):.4f} vs {survives(s):.4f} of their own attacks at 8 s")


# -- the mixer --------------------------------------------------------------

def _tiny_score() -> Score:
    return Score(
        name="tiny", duration=4.0,
        notes=[Note(60, 0.0, 1.0, 80, 90, True),
               Note(64, 0.005, 1.0, 60, 40, True),   # the 5 ms lattice, as captured
               Note(67, 0.010, 1.2, 95, 120, True)],
        pedal=[(0.0, 0), (0.5, 127), (2.5, 0)])


@pytest.mark.parametrize("preset_name", ["acoustic", "electric"])
def test_a_mix_is_finite_normalised_and_never_clips(preset_name):
    mix, stats = engine.render(_tiny_score(), load_preset(preset_name),
                               sample_rate=SR)
    assert np.isfinite(mix).all()
    assert mix.shape[1] == 2
    assert stats["peak"] == pytest.approx(engine.TARGET_PEAK, rel=1e-4)
    assert np.abs(mix).max() < 1.0
    assert stats["n_notes"] == 3


def test_onsets_land_on_the_captured_times_not_a_tidied_up_grid():
    """Chords arrive as ~5 ms arpeggios on the BLE link. That is what is
    rendered; nothing here pulls them back together."""
    s = _tiny_score()
    mix, _ = engine.render(s, load_preset("acoustic"), sample_rate=SR)
    mono = np.abs(mix).max(axis=1)
    first = int(np.argmax(mono > 1e-4))
    assert first <= int(0.002 * SR), "the first onset moved"
    assert s.notes[1].t_on - s.notes[0].t_on == pytest.approx(0.005)


def test_wav_round_trips_at_the_declared_rate(tmp_path):
    import soundfile as sf

    mix, _ = engine.render(_tiny_score(), load_preset("electric"), sample_rate=44100)
    p = engine.write_wav(tmp_path / "x.wav", mix, 44100)
    back, sr = sf.read(str(p), dtype="float32")
    assert sr == 44100
    assert back.shape == mix.shape
    assert np.abs(back).max() < 1.0


# -- against the real take --------------------------------------------------

@pytest.mark.skipif(not PIECE.exists(), reason="the 2026-08-17 piece take is not here")
def test_the_score_read_from_the_piece_matches_what_was_captured():
    from fp30x_studio.synth.score import read_score

    s = read_score(PIECE)
    assert len(s.notes) == 1730
    assert s.duration == pytest.approx(353.11, abs=0.01)
    assert (min(n.note for n in s.notes), max(n.note for n in s.notes)) == (29, 89)
    assert s.peak_polyphony() == 8
    assert all(n.trusted for n in s.notes), "every interval was closed by a note-off"
    # The phantom 37.97 s note came from reading one message per CoreMIDI
    # packet. Nothing in this take is anywhere near that long.
    assert max(n.duration for n in s.notes) < 20.0
    # Release velocity is a real, wide, near-independent channel.
    assert len({n.velocity_off for n in s.notes}) > 100
    # CC64 is a continuous sensor, not a switch.
    positions = {p for _, p in s.pedal}
    assert len(positions) > 100
    assert max(n.velocity_on for n in s.notes) <= model.VELOCITY_FULL_SCALE
