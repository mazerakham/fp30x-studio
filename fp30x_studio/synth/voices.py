"""The two voices, as sample-generating functions. One note in, one buffer out.

Both take the same call signature and the same :class:`~.model.Preset`, and both
return a mono ``float32`` buffer starting at the strike. Everything after the
strike that is not in the preset is in :mod:`.model` as a named constant.

What the damper does, and why it is an integral
------------------------------------------------
The pedal is a half-damper sensor sending a continuous position, and it moves
*after* notes are released -- on the open take, 780 of 804 releases happened
with the dampers already off the strings, so the note that decides when a note
stops is usually a CC64 message that arrives later. Modelling damping as a
one-shot envelope at ``t_off`` would therefore get the piece qualitatively
wrong: it would cut notes the player was holding with the pedal, and it would
fail to cut notes the player released early and then lifted the pedal on.

So damping is a *rate*, and the envelope is its integral. With damper contact

    d(t) = clip(1 - damper_cc64_scale * cc64(t) / 127, 0, 1)

which is piecewise constant between CC messages, the post-release gain is

    g(t) = exp( -(D(t) - D(t_off)) / tau_release ),    D(t) = int_0^t d(u) du

and ``tau_release`` is set per note by the release velocity. D is piecewise
linear with a breakpoint at each CC64 message, so it is computed exactly once
per take at the breakpoints and interpolated -- there is no discretisation
error in it at all. Pedal fully down gives ``d = 0``, ``g = 1``, and the note
simply carries on with its natural decay; pedal up gives ``d = 1`` and the note
falls at its own release rate.
"""

from __future__ import annotations

import numpy as np

from .model import (
    FM_INDEX_DECAY_S,
    HAMMER_STRIKE_POINT,
    UNISON_DEPTH,
    UNISON_DETUNE_CENTS,
    UNISON_LOWEST_NOTE,
    SECOND_STAGE_MIX,
    SECOND_STAGE_RATIO,
    TINE_DECAY_HALVING_SEMITONES,
    VELOCITY_AMP_EXPONENT,
    Preset,
    decay_scale_at,
    inharmonicity_at,
    partial_frequencies,
    velocity_norm,
)

__all__ = [
    "note_frequency",
    "release_tau",
    "render_string",
    "render_tine",
    "VOICES",
    "TAIL_FLOOR",
    "MAX_TAIL_S",
    "TRUNCATION_FADE_S",
]

#: A note is rendered until its slowest surviving component falls this far
#: below its own peak. -66 dB is below the noise floor of the recording chain
#: this is going to sit next to and saves rendering minutes of inaudible tail.
TAIL_FLOOR = 5.0e-4

#: Hard cap on one note's length. A pedalled bass note on a real piano rings
#: about this long; past it the tail is doing nothing but costing samples.
MAX_TAIL_S = 22.0


def note_frequency(note: int) -> float:
    """Equal temperament, A4 = 440 Hz. The FP-30X ships at A440 and was not
    retuned, so there is nothing subtler to do here."""
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def release_tau(velocity_off: int, preset: Preset) -> float:
    """Damper fall time in seconds, from the release byte.

    This is the parameter the release velocity buys us. It is a real, measured,
    near-independent second channel -- 117 distinct values on the piece take,
    3.68 bits against the strike byte's 3.14, mutual information 0.074 of a
    possible 2.16 -- and nothing else in the signal chain uses it. A key
    released fast drops the damper onto the string fast and the note stops
    abruptly; a key let up slowly lays the felt on and the note bleeds away.

    Linear in the byte between the two preset endpoints, because there is no
    evidence in the capture for any particular curve and a straight line is the
    honest default.
    """
    v = min(max(velocity_off, 0), 127) / 127.0
    ms = preset.release_ms_slow + (preset.release_ms_fast - preset.release_ms_slow) * v
    return max(ms, 1.0) / 1000.0


def _damper_gain(t_abs: np.ndarray, t_off: float, tau: float,
                 ped_t: np.ndarray, ped_D: np.ndarray) -> np.ndarray:
    """``exp(-(D(t) - D(t_off)) / tau)``, clamped to 1 before the release."""
    D = np.interp(t_abs, ped_t, ped_D)
    D0 = float(np.interp(t_off, ped_t, ped_D))
    g = np.exp(-np.maximum(D - D0, 0.0) / tau)
    g[t_abs < t_off] = 1.0
    return g


def _tail_length(env_tau: float, tau_rel: float, t_on: float, t_off: float,
                 ped_t: np.ndarray, ped_D: np.ndarray, sr: int) -> int:
    """How many samples are worth rendering, given natural decay *and* damper.

    Probed on a 20 ms grid rather than solved, because the damper term depends
    on the pedal trace and has no closed form.
    """
    cap = min(MAX_TAIL_S, env_tau * np.log(1.0 / TAIL_FLOOR))
    grid = np.arange(0.0, cap + 0.02, 0.02)
    nat = np.exp(-grid / env_tau)
    damp = _damper_gain(t_on + grid, t_off, tau_rel, ped_t, ped_D)
    alive = np.nonzero(nat * damp > TAIL_FLOOR)[0]
    end = cap if alive.size == 0 else min(cap, grid[alive[-1]] + 0.05)
    return max(int(end * sr), int(0.01 * sr))


#: A note that hits :data:`MAX_TAIL_S` is still audible when it is cut, so the
#: cut is faded. Without this the mix picks up a click 22 s after every pedalled
#: bass note -- a synthesis artefact that has nothing to do with the model.
TRUNCATION_FADE_S = 0.25


def _fade_truncated_tail(out: np.ndarray, sr: int) -> np.ndarray:
    """Raised-cosine fade over the last :data:`TRUNCATION_FADE_S`."""
    m = min(out.size, int(TRUNCATION_FADE_S * sr))
    if m > 8:
        out[-m:] *= 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, m)))
    return out


def _hammer(n: int, gain: float, vnorm: float, preset: Preset, sr: int,
            rng: np.random.Generator) -> np.ndarray:
    """The thump of felt on wire: a short, one-pole-darkened noise burst.

    Not a model of the hammer, and not claimed to be. It is there because a
    purely additive attack has no broadband component at all and reads as an
    organ stop; hard strikes get a brighter, louder burst than soft ones.
    """
    if preset.hammer_noise <= 0 or n <= 0:
        return np.zeros(0, dtype=np.float64)
    length = min(n, int(sr * max(preset.attack_ms, 1.0) * 4e-3))
    if length < 4:
        return np.zeros(0, dtype=np.float64)
    from scipy.signal import lfilter

    x = rng.standard_normal(length)
    # Brighter with velocity: the pole moves from 0.75 (dull) to 0.35 (sharp).
    a = 0.75 - 0.40 * vnorm
    y = lfilter([1.0 - a], [1.0, -a], x)
    t = np.arange(length) / sr
    env = np.exp(-t / (max(preset.attack_ms, 1.0) * 1.2e-3))
    return y * env * (preset.hammer_noise * gain * (0.35 + 0.65 * vnorm) * 3.0)


# ---------------------------------------------------------------------------
# String
# ---------------------------------------------------------------------------

def render_string(note: int, velocity_on: int, velocity_off: int,
                  t_on: float, t_off: float, preset: Preset, sr: int,
                  ped_t: np.ndarray, ped_D: np.ndarray,
                  rng: np.random.Generator) -> np.ndarray:
    """A struck string, additively.

    Partials at ``f_n = n f0 sqrt(1 + B n^2)`` with B taken from the preset at
    A4 and stretched with pitch. Amplitude ``a_n = n^-rolloff`` tilted by
    velocity: ``a_n *= n^(velocity_brightness * (v - 1/2))``, so a hard strike
    does not merely scale the same spectrum up, it moves the centroid. Decay
    ``tau_n = tau_1 n^-exponent`` in two stages, so the top of the spectrum is
    gone within a second while the fundamental is still going.
    """
    f0 = note_frequency(note)
    nyq = sr / 2.0
    B = inharmonicity_at(note, preset.inharmonicity_B)
    n_idx, freqs = partial_frequencies(f0, preset.partials, B, nyq)
    if n_idx.size == 0:
        return np.zeros(0, dtype=np.float32)

    v = velocity_norm(velocity_on)
    gain = v ** VELOCITY_AMP_EXPONENT

    amps = n_idx ** (-preset.partial_amp_rolloff)
    amps = amps * n_idx ** (preset.velocity_brightness * (v - 0.5))
    # The hammer cannot drive a partial that has a node where it strikes.
    amps = amps * np.abs(np.sin(n_idx * np.pi * HAMMER_STRIKE_POINT))
    amps = amps / np.sum(amps)

    # Unison beating, as an amplitude term per partial (see model.py).
    depth = UNISON_DEPTH * min(max((note - UNISON_LOWEST_NOTE) / 12.0, 0.0), 1.0)
    detune = UNISON_DETUNE_CENTS / 1200.0 * np.log(2.0)
    beat_phase = rng.uniform(0.0, 2.0 * np.pi, size=n_idx.size)

    tau1 = preset.partial_decay_base * decay_scale_at(note)
    taus = tau1 * n_idx ** (-preset.partial_decay_exponent)

    tau_rel = release_tau(velocity_off, preset)
    slowest = tau1 * SECOND_STAGE_RATIO
    n_total = _tail_length(slowest, tau_rel, t_on, t_off, ped_t, ped_D, sr)

    out = np.zeros(n_total, dtype=np.float64)
    atk = max(preset.attack_ms, 0.2) * 1e-3
    phases = rng.uniform(0.0, 2.0 * np.pi, size=n_idx.size)

    for k in range(n_idx.size):
        a, tau, f = amps[k], taus[k], freqs[k]
        if a < TAIL_FLOOR * 0.1:
            continue
        # This partial's own tail: high partials are both quieter and shorter,
        # so most of them cost a fraction of the note's length.
        span = tau * SECOND_STAGE_RATIO * np.log(a / (TAIL_FLOOR * 0.2) + 1.0)
        m = min(n_total, max(int(span * sr), 1))
        t = np.arange(m, dtype=np.float64) / sr
        env = ((1.0 - SECOND_STAGE_MIX) * np.exp(-t / tau)
               + SECOND_STAGE_MIX * np.exp(-t / (tau * SECOND_STAGE_RATIO)))
        # One rise time for every partial. The hammer's contact time does
        # lowpass the excitation, but that is a change of *amplitude*, and it
        # is already carried by the velocity tilt above; staggering the rises
        # per partial would smear the attack over tens of milliseconds and a
        # piano attack is not smeared.
        env *= 1.0 - np.exp(-t / atk)
        if depth > 0.0:
            env *= 1.0 - depth + depth * np.cos(
                np.pi * detune * f * t + beat_phase[k])
        out[:m] += a * env * np.sin(2.0 * np.pi * f * t + phases[k])

    h = _hammer(n_total, gain, v, preset, sr, rng)
    if h.size:
        out[:h.size] += h

    t_abs = t_on + np.arange(n_total, dtype=np.float64) / sr
    out *= _damper_gain(t_abs, t_off, tau_rel, ped_t, ped_D)
    return _fade_truncated_tail(out * gain, sr).astype(np.float32)


# ---------------------------------------------------------------------------
# Tine
# ---------------------------------------------------------------------------

def render_tine(note: int, velocity_on: int, velocity_off: int,
                t_on: float, t_off: float, preset: Preset, sr: int,
                ped_t: np.ndarray, ped_D: np.ndarray,
                rng: np.random.Generator) -> np.ndarray:
    """A Rhodes-style tine. Not the string model retuned -- a different machine.

    There is no ``n^-rolloff`` series here. A tine is a cantilever bar struck
    near its root, and what comes off it is:

    * a nearly sinusoidal fundamental, coloured by an FM modulator at a
      non-integer ratio whose index collapses with its own time constant
      (:data:`~.model.FM_INDEX_DECAY_S`) -- this is the bark-then-mellow that
      makes the timbre recognisable;
    * one strong inharmonic bell partial six-ish times the fundamental, from the
      bar's second transverse mode, decaying faster than the fundamental;
    * on hard strikes only, a percussive tick of the hammer tip on the tine.

    The velocity axis is different in kind too: on the string it tilts a partial
    series, here it drives the FM index, so a soft note is very nearly a pure
    sine and a hard one is full of inharmonic sidebands. The amplitude change is
    almost incidental.
    """
    f0 = note_frequency(note)
    nyq = sr / 2.0
    v = velocity_norm(velocity_on)
    gain = v ** VELOCITY_AMP_EXPONENT

    tau = preset.partial_decay_base * decay_scale_at(
        note, TINE_DECAY_HALVING_SEMITONES)
    tau_rel = release_tau(velocity_off, preset)
    n_total = _tail_length(tau * SECOND_STAGE_RATIO, tau_rel, t_on, t_off,
                           ped_t, ped_D, sr)

    t = np.arange(n_total, dtype=np.float64) / sr
    atk = max(preset.attack_ms, 0.2) * 1e-3

    # Index, and the bandlimit on it. Significant FM sidebands run to about
    # (I + 2) either side of the carrier; past Nyquist they fold back as
    # inharmonic grit, so the index is capped rather than allowed to alias.
    i0 = preset.fm_index * v ** 1.5
    i_max = max(0.0, (nyq / f0 - 1.0) / max(preset.fm_ratio, 1e-6) - 2.0)
    idx = min(i0, i_max) * np.exp(-t / FM_INDEX_DECAY_S)

    env = ((1.0 - SECOND_STAGE_MIX) * np.exp(-t / tau)
           + SECOND_STAGE_MIX * np.exp(-t / (tau * SECOND_STAGE_RATIO)))
    env *= 1.0 - np.exp(-t / atk)

    w0 = 2.0 * np.pi * f0 * t
    out = env * np.sin(w0 + idx * np.sin(preset.fm_ratio * w0))

    f_bell = preset.bell_partial * f0
    if preset.bell_amp > 0 and f_bell < nyq * 0.98:
        tau_bell = tau * 0.45
        bell = np.exp(-t / tau_bell) * (1.0 - np.exp(-t / (atk * 0.5)))
        out += (preset.bell_amp * (0.45 + 0.55 * v)) * bell * np.sin(
            2.0 * np.pi * f_bell * t + rng.uniform(0.0, 2.0 * np.pi))

    # The bark: only hard strikes get it, and it is gone in a few tens of ms.
    if preset.bark_amp > 0 and preset.bark_ms > 0:
        m = min(n_total, int(preset.bark_ms * 6e-3 * sr))
        if m > 4:
            tb = t[:m]
            benv = np.exp(-tb / (preset.bark_ms * 1e-3))
            f_bark = min(f0 * 11.0, nyq * 0.9)
            tick = np.sin(2.0 * np.pi * f_bark * tb) + 0.5 * rng.standard_normal(m)
            out[:m] += (preset.bark_amp * v ** 3.0) * benv * tick

    t_abs = t_on + t
    out *= _damper_gain(t_abs, t_off, tau_rel, ped_t, ped_D)
    return _fade_truncated_tail(out * gain * 0.55, sr).astype(np.float32)


VOICES = {"string": render_string, "tine": render_tine}
