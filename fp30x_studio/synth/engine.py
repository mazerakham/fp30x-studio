"""Mixing one take through one preset, and writing the result to a WAV.

The engine is deliberately dumb: it builds the exact damper integral once, calls
a voice per note, and adds the result into a stereo buffer at the sample index
the capture says the note started on. There is no quantisation and no chord
grouping. The FP-30X's BLE link puts every event on a 5.000 ms lattice and
delivers chords as ~5 ms arpeggios; that spread is in the performance as played
and as captured, so it is rendered, not tidied away.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .model import PAN_WIDTH, Preset, damper_contact
from .score import Score
from .voices import VOICES

__all__ = ["RenderResult", "damper_integral", "render", "write_wav"]

#: Peak the mix is normalised to. Leaves 0.5 dB of headroom, so no sample can
#: round up into a clip on the way out to int16.
TARGET_PEAK = 0.945

#: There is no compressor, limiter or soft knee. The chain from strike velocity
#: to output sample is linear, so the 40 dB the velocity curve spreads between
#: the softest and loudest strike he played survives to the file. A tanh knee
#: was tried and thrown out: normalised the way it has to be, it lifted a
#: passage 12 dB below peak to 4 dB below peak, which is to say it deleted most
#: of the dynamics the velocity model exists to reproduce. Peak polyphony is 8
#: and the crest factor comes out around 14 dB, so straight peak normalisation
#: has plenty of room and needs no help.
LINEAR_CHAIN = True


@dataclass(slots=True)
class RenderResult:
    path: Path
    preset: str
    model: str
    take: str
    sample_rate: int
    seconds: float
    n_notes: int
    peak_before_norm: float
    peak: float
    rms: float
    gain_db: float
    crest_db: float
    wall_s: float

    def line(self) -> str:
        return (f"{self.path}: {self.seconds:.2f} s @ {self.sample_rate} Hz stereo, "
                f"{self.n_notes} notes, model={self.model}, "
                f"peak {self.peak:.4f} ({20 * np.log10(self.peak):+.2f} dBFS), "
                f"rms {self.rms:.4f} ({20 * np.log10(self.rms):+.2f} dBFS), "
                f"crest {self.crest_db:.1f} dB, "
                f"normalisation {self.gain_db:+.2f} dB, rendered in "
                f"{self.wall_s:.1f} s")


def damper_integral(score: Score, preset: Preset, end: float
                    ) -> tuple[np.ndarray, np.ndarray]:
    """``(t, D)`` breakpoints for the damping the pedal trace applies.

    ``D`` has two parts, and the second one is the reason this is not just an
    integral any more::

        D(t) = int_0^t d(u) du  +  bite * (number of felt landings before t)

    The integral is the *dwell* term: felt resting on wire for a while. It is
    exact, because ``d`` is piecewise constant between CC64 messages and so
    ``D`` is piecewise linear with a breakpoint at each of them.

    The sum is the *contact* term, and it exists because the dwell term alone
    gets his playing qualitatively wrong. Measured on the takes: 769 excursions
    into the middle of the pedal's travel in fifteen minutes, median duration
    15 ms, maximum 30 ms. The FP-30X never reports a sustained intermediate
    position -- every one of those is a sample caught while the foot was moving.
    Integrated, a 15 ms dip contributes essentially nothing and the note carries
    on untouched; physically, felt that touches wire for 15 ms takes real energy
    out of it, and takes it disproportionately out of the top of the spectrum.
    So each landing adds a step of ``pedal_contact_bite`` to ``D``, which the
    voices then weight by ``n^q`` like every other damping term. That is what
    makes a flutter-pedal clear the treble and leave the bass, instead of doing
    nothing at all.

    A landing is a crossing of ``d = 1/2`` upward, i.e. the felt coming back
    onto the strings, counted once per crossing rather than once per message.
    """
    ts = [0.0]
    ds = []
    last = 0
    for t, pos in score.pedal:
        t = max(t, 0.0)
        if t > ts[-1]:
            ts.append(t)
            ds.append(damper_contact(last, preset.pedal_engage, preset.pedal_knee,
                                     preset.pedal_leak, preset.damper_cc64_scale))
        last = pos
    ts.append(max(end, ts[-1]) + 60.0)
    ds.append(damper_contact(last, preset.pedal_engage, preset.pedal_knee,
                             preset.pedal_leak, preset.damper_cc64_scale))

    t_arr = np.asarray(ts, dtype=np.float64)
    d_arr = np.clip(np.asarray(ds, dtype=np.float64), 0.0, 1.0)
    D = np.concatenate([[0.0], np.cumsum(d_arr * np.diff(t_arr))])

    bite = max(float(preset.pedal_contact_bite), 0.0)
    if bite > 0.0 and d_arr.size > 1:
        # Upward crossings of d = 1/2, as a step added to every later
        # breakpoint. np.interp reads D between breakpoints, so a step here is
        # a genuine jump discontinuity in the damping the voices see.
        engaged = d_arr >= 0.5
        landings = np.zeros(d_arr.size, dtype=np.float64)
        landings[1:] = (engaged[1:] & ~engaged[:-1]).astype(np.float64)
        if engaged[0]:
            landings[0] = 1.0
        D[1:] += bite * np.cumsum(landings)

    return t_arr, D


def render(score: Score, preset: Preset, *, sample_rate: int = 48000,
           tail_s: float = 6.0, seed: int = 20260817,
           progress=None) -> tuple[np.ndarray, dict]:
    """Mix the whole take. Returns ``(stereo float32 [n, 2], stats)``."""
    voice = VOICES.get(preset.model)
    if voice is None:
        raise ValueError(f"unknown model {preset.model!r}; have {sorted(VOICES)}")

    sr = int(sample_rate)
    total = int((score.duration + tail_s) * sr) + sr
    left = np.zeros(total, dtype=np.float64)
    right = np.zeros(total, dtype=np.float64)

    ped_t, ped_D = damper_integral(score, preset, score.duration + tail_s)
    rng = np.random.default_rng(seed)

    t0 = time.time()
    rendered = 0
    for i, n in enumerate(score.notes):
        buf = voice(n.note, n.velocity_on, n.velocity_off, n.t_on, n.t_off,
                    preset, sr, ped_t, ped_D, rng)
        if buf.size == 0:
            continue
        start = int(round(n.t_on * sr))
        end = min(start + buf.size, total)
        if end <= start:
            continue
        seg = buf[: end - start].astype(np.float64)
        # Pan linearly with pitch across the 88 keys, equal-power.
        pos = PAN_WIDTH * ((n.note - 21) / 87.0 * 2.0 - 1.0)
        ang = (pos + 1.0) * (np.pi / 4.0)
        left[start:end] += seg * np.cos(ang)
        right[start:end] += seg * np.sin(ang)
        rendered += 1
        if progress and (i + 1) % 200 == 0:
            progress(i + 1, len(score.notes), time.time() - t0)

    mix = np.stack([left, right], axis=1)
    peak_raw = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak_raw > 0:
        mix *= TARGET_PEAK / peak_raw
    peak = peak_raw

    stats = {
        "n_notes": rendered,
        "peak_before_norm": peak_raw,
        "peak": float(np.max(np.abs(mix))) if mix.size else 0.0,
        "rms": float(np.sqrt(np.mean(mix ** 2))) if mix.size else 0.0,
        "gain_db": float(20 * np.log10(TARGET_PEAK / peak)) if peak > 0 else 0.0,
        "crest_db": float(20 * np.log10(
            np.max(np.abs(mix)) / max(np.sqrt(np.mean(mix ** 2)), 1e-30)))
        if mix.size else 0.0,
        "wall_s": time.time() - t0,
        "seconds": mix.shape[0] / sr,
    }
    return mix.astype(np.float32), stats


def write_wav(path: str | Path, mix: np.ndarray, sample_rate: int,
              subtype: str = "PCM_24") -> Path:
    import soundfile as sf

    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(p), mix, sample_rate, subtype=subtype)
    return p
