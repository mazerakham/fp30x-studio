"""Put the piano in a hall, by convolution with a synthesised impulse response.

Why convolution rather than a network of delays
-----------------------------------------------
The classic algorithmic reverbs -- Schroeder's comb-and-allpass network, and
Moorer's extension of it in *About This Reverberation Business* (Computer Music
Journal, 1979) -- were built when convolving a long impulse response in real
time was out of reach. Nothing here is real time. A ten-minute render can be
convolved with a two-second response in a few seconds of FFT, and convolution
has the property the delay networks were approximating: the result is exactly
what a room with that response would have done to the signal, with no metallic
ringing from a comb filter whose poles landed on a note.

So the only real question is where the impulse response comes from. A measured
one from an actual hall would be ideal and is what commercial convolution
reverbs ship. Failing that, a synthetic response built the way Moorer describes
gets most of the way:

* **Exponentially decaying noise.** The late tail of a real hall is diffuse --
  so many overlapping reflections that it is statistically indistinguishable
  from noise with a decaying envelope. That is what the tail is made of here.
* **Frequency-dependent decay.** Air and soft furnishings absorb treble faster
  than bass, so a hall's RT60 is not one number. Three bands are built with
  their own decay times -- lows ring longest, highs die first -- which is most
  of what makes a synthetic response sound like a room rather than a spring.
* **Pre-delay.** The direct sound arrives before any reflection. The gap sets
  the apparent size of the room and, more usefully, keeps the attack of each
  note clear of the wash.
* **Early reflections.** A handful of discrete taps in the first 80 ms, before
  the diffuse tail takes over. These carry the impression of walls at specific
  distances.
* **Decorrelated stereo.** Independent noise per channel. Correlated noise
  collapses the hall into a point between the speakers.

    python -m fp30x_studio.reverb in.wav out.wav --wet 0.28 --rt60 2.1
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

#: RT60 multipliers per band. Treble dies first; this ratio is what stops a
#: synthetic response sounding like a metal plate.
BANDS = ((0, 500, 1.30), (500, 3000, 1.00), (3000, 20000, 0.45))
PREDELAY_S = 0.025
#: (time, gain) for the discrete early reflections, before the tail.
EARLY = ((0.011, 0.62), (0.019, -0.48), (0.031, 0.41), (0.043, -0.34),
         (0.057, 0.28), (0.071, -0.22))


def _read(path: Path):
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: expected 16-bit PCM")
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float64) / 32768.0
    return a.reshape(-1, ch), sr


def _write(path: Path, x: np.ndarray, sr: int):
    q = np.clip(np.rint(x * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1]); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(q.tobytes())


def impulse_response(sr: int, rt60: float, seed: int) -> np.ndarray:
    """One channel of synthetic hall, as described above."""
    rng = np.random.default_rng(seed)
    n = int(sr * (rt60 * 1.15 + PREDELAY_S + 0.1))
    t = np.arange(n) / sr

    tail = np.zeros(n)
    noise = rng.standard_normal(n)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    for lo, hi, mult in BANDS:
        band = np.fft.irfft(np.where((freqs >= lo) & (freqs < hi), spec, 0), n=n)
        # -60 dB over rt60*mult seconds
        tail += band * 10 ** (-3.0 * t / (rt60 * mult))

    ir = np.zeros(n)
    pre = int(sr * PREDELAY_S)
    ir[pre:] += tail[:n - pre]
    for dt, g in EARLY:
        i = pre + int(sr * dt)
        if i < n:
            ir[i] += g
    # fade the diffuse onset in over the early-reflection window so the tail
    # does not start abruptly underneath them
    ramp = int(sr * 0.080)
    ir[pre:pre + ramp] *= np.linspace(0.15, 1.0, ramp)
    return ir / np.abs(ir).max()


def convolve(x: np.ndarray, ir: np.ndarray, block: int = 1 << 16) -> np.ndarray:
    """Overlap-add FFT convolution. Long signal, short kernel, no scipy."""
    n_ir = len(ir)
    fft_n = 1 << int(np.ceil(np.log2(block + n_ir - 1)))
    H = np.fft.rfft(ir, fft_n)
    out = np.zeros(len(x) + n_ir - 1)
    for start in range(0, len(x), block):
        seg = x[start:start + block]
        y = np.fft.irfft(np.fft.rfft(seg, fft_n) * H, fft_n)[:len(seg) + n_ir - 1]
        out[start:start + len(y)] += y
    return out


def apply(x: np.ndarray, sr: int, *, wet: float, rt60: float) -> np.ndarray:
    ch = x.shape[1]
    wet_sig = np.zeros((len(x) + int(sr * (rt60 * 1.15 + 0.2)), ch))
    for c in range(ch):
        ir = impulse_response(sr, rt60, seed=1000 + c)   # decorrelated per side
        y = convolve(x[:, c], ir)
        wet_sig[:len(y), c] = y
    # match the wet signal's level to the dry before mixing, so `wet` means a
    # proportion and not an arbitrary number that changes with rt60
    dry_rms = np.sqrt(np.mean(x ** 2))
    wet_rms = np.sqrt(np.mean(wet_sig ** 2))
    if wet_rms > 0:
        wet_sig *= dry_rms / wet_rms
    out = np.zeros_like(wet_sig)
    out[:len(x)] += (1.0 - wet) * x
    out += wet * wet_sig
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fp30x_studio.reverb")
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--wet", type=float, default=0.28,
                    help="0 = dry, 1 = only the hall (default 0.28)")
    ap.add_argument("--rt60", type=float, default=2.1,
                    help="mid-band decay to -60 dB, seconds (default 2.1)")
    ap.add_argument("--ceiling", type=float, default=-1.0)
    a = ap.parse_args(argv)

    x, sr = _read(Path(a.src))
    y = apply(x, sr, wet=a.wet, rt60=a.rt60)
    pk = np.abs(y).max()
    y *= 10 ** (a.ceiling / 20.0) / pk
    assert np.abs(y).max() < 1.0
    _write(Path(a.dst), y, sr)
    print(f"{Path(a.dst).name}  wet {a.wet}  rt60 {a.rt60}s  "
          f"tail +{(len(y)-len(x))/sr:.1f}s  peak {20*np.log10(np.abs(y).max()):.1f} dBFS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
