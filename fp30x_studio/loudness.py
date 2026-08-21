"""Make a quiet render audible without letting the loud parts saturate.

The problem, measured rather than assumed
-----------------------------------------
The first render of the 2026-08-17 nocturne peaked at **-24.5 dBFS**. That is
not a property of the playing; it is fluidsynth's master gain, which defaults
to 0.2. So the first 24 dB are free: they cost nothing, change no shape, and
are taken by rendering again with ``-g`` rather than by amplifying a finished
16-bit file, which would lift the quantisation noise with the music.

What is left after that is real, and it is the reason a nocturne is hard to
hear at the start: between five-second blocks this take spans **38 dB**. Any
single number multiplied through it either leaves the opening inaudible or
saturates the climax.

The mapping
-----------
Let ``E(t)`` be the short-term level in dBFS. The output level is

    out(t) = P + (1 - alpha) * (E(t) - P)

a straight line in dB with slope ``1 - alpha`` through a pivot ``P``, so the
gain applied is

    g(t) = alpha * (P - E(t))

Everything follows from that one line:

* ``alpha = 0`` is pure linear gain -- dynamics untouched.
* ``alpha = 1`` flattens every moment to ``P`` -- no dynamics at all.
* Signal exactly at ``P`` is unchanged; below it is lifted, above it is held
  down. ``ratio = 1 / (1 - alpha)`` is the same number a compressor calls its
  ratio.

The map is **monotone increasing in E** for ``alpha < 1``, which is the
property worth stating: louder passages stay louder than quieter ones. Nothing
is inverted, no crescendo becomes a diminuendo. It compresses the dynamics; it
does not reorder them.

Two guards, both necessary in practice
--------------------------------------
A pure application of the line would boost silence by ``alpha * (P - E)``,
which is unbounded as ``E`` falls. Between phrases in a nocturne that means
lifting room tone and release tails into audibility. So the gain is capped at
``max_boost``, and below ``floor`` it is tapered smoothly to zero -- a gate
with a soft knee rather than a switch, because a hard one breathes audibly.

``E`` is smoothed asymmetrically before use: fast to rise, slow to fall. A
symmetric filter either lets a single loud chord duck the phrase around it or
lags far enough behind to miss it entirely. This asymmetry is what stops the
result pumping.

Saturation is then not argued about, it is prevented: after the gain is
applied the whole signal is scaled so its true peak sits at ``ceiling``, and
the result is asserted to contain no sample at or beyond full scale.

    python -m fp30x_studio.loudness in.wav out.wav --ratio 3 --pivot -20
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

HOP_S = 0.010
WIN_S = 0.100
ATTACK_S = 0.080
RELEASE_S = 0.600


def _db(x):
    return 20.0 * np.log10(np.maximum(x, 1e-12))


def read_wav(path: Path):
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: expected 16-bit PCM")
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float64) / 32768.0
    return a.reshape(-1, ch), sr


def write_wav(path: Path, x: np.ndarray, sr: int):
    q = np.clip(np.rint(x * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(q.tobytes())


def envelope_db(mono: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Short-term level in dBFS, on a 10 ms grid, asymmetrically smoothed."""
    hop, win = int(sr * HOP_S), int(sr * WIN_S)
    idx = np.arange(0, max(1, len(mono) - win), hop)
    power = np.array([np.mean(mono[i:i + win] ** 2) for i in idx])
    e = np.maximum(_db(np.sqrt(power)), -90.0)   # digital silence is not -999 dB
    # one-pole, fast up and slow down: a single loud chord must not duck the
    # phrase it sits in, and the recovery must not be audible as pumping.
    a_at = np.exp(-HOP_S / ATTACK_S)
    a_re = np.exp(-HOP_S / RELEASE_S)
    out = np.empty_like(e)
    s = e[0]
    for i, v in enumerate(e):
        c = a_at if v > s else a_re
        s = c * s + (1 - c) * v
        out[i] = s
    return idx, out


def limit(y: np.ndarray, sr: int, ceiling: float, lookahead_s: float = 0.005):
    """Hold the peak under `ceiling` by ducking only where it would exceed it.

    A global scale would pay for one loud transient across the whole file. This
    pays only locally: a running max over a short window sets the required gain
    reduction, that reduction is smoothed so it fades in and out rather than
    stepping, and everywhere the signal is already under the ceiling the gain
    is exactly 1.
    """
    limit_lin = 10 ** (ceiling / 20.0)
    w = max(1, int(sr * lookahead_s))
    mono = np.abs(y).max(axis=1)
    # running max via maximum filter, cheap enough with a strided reduction
    pad = np.pad(mono, (w, w), mode="edge")
    run = np.maximum.reduceat(pad, np.arange(0, len(pad), w))
    run = np.repeat(run, w)[w:w + len(mono)]
    need = np.minimum(1.0, limit_lin / np.maximum(run, 1e-9))
    # smooth the reduction so it is not heard as a click
    a = np.exp(-1.0 / (sr * 0.020))
    sm = np.empty_like(need)
    cur = need[0]
    for i, v in enumerate(need):
        cur = min(v, a * cur + (1 - a) * v)   # instant down, gentle up
        sm[i] = cur
    out = y * sm[:, None]
    return out, _db(sm.min())


def compress(x: np.ndarray, sr: int, *, ratio: float, pivot: float,
             max_boost: float, floor: float, knee: float, ceiling: float,
             target_rms: float = -20.0):
    mono = x.mean(axis=1)
    idx, e = envelope_db(mono, sr)
    alpha = 1.0 - 1.0 / ratio

    g = alpha * (pivot - e)                 # the line
    g = np.minimum(g, max_boost)            # never boost more than this
    g = np.maximum(g, -60.0)

    # soft gate: taper the boost away below `floor` over a `knee` dB region,
    # so release tails and room tone are not lifted into the foreground.
    t = np.clip((e - (floor - knee)) / knee, 0.0, 1.0)
    g = g * t

    gain = 10 ** (g / 20.0)
    full = np.interp(np.arange(len(x)), idx + int(sr * WIN_S) / 2, gain,
                     left=gain[0], right=gain[-1])
    y = x * full[:, None]

    # Makeup, then limit -- and in that order, because the obvious alternative
    # is wrong. Scaling the whole file so its peak lands on the ceiling undoes
    # the compression: one boosted quiet transient sets the peak and drags
    # everything else back down with it. Measured on the 2026-08-17 nocturne,
    # that produced a "compressed" file 4.4 dB *quieter* than the linear one.
    rms = np.sqrt(np.mean(y ** 2))
    if rms > 0:
        y = y * 10 ** ((target_rms - _db(rms)) / 20.0)
    y, reduction = limit(y, sr, ceiling)

    return y, {
        "limiter_reduction_db": round(float(reduction), 2),
        "alpha": alpha,
        "ratio": ratio,
        "boost_applied_db": (float(np.min(g)), float(np.max(g))),
        "peak_before_db": float(_db(np.abs(x).max())),
        "peak_after_db": float(_db(np.abs(y).max())),
        "rms_before_db": float(_db(np.sqrt(np.mean(x ** 2)))),
        "rms_after_db": float(_db(np.sqrt(np.mean(y ** 2)))),
        "env_range_before_db": float(np.max(e) - np.min(e)),
    }


def normalize(x: np.ndarray, ceiling: float):
    """Pure linear gain to the same ceiling -- the control for an A/B."""
    pk = np.abs(x).max()
    return x * (10 ** (ceiling / 20.0) / pk) if pk > 0 else x


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fp30x_studio.loudness")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--ratio", type=float, default=3.0,
                    help="1 = no compression, higher = flatter (default 3)")
    ap.add_argument("--pivot", type=float, default=-20.0,
                    help="dBFS level left unchanged (default -20)")
    ap.add_argument("--max-boost", type=float, default=24.0)
    ap.add_argument("--floor", type=float, default=-62.0,
                    help="below this the boost tapers off (default -62 dBFS)")
    ap.add_argument("--knee", type=float, default=12.0)
    ap.add_argument("--ceiling", type=float, default=-1.0)
    ap.add_argument("--target-rms", type=float, default=-20.0,
                    help="makeup gain aims the overall RMS here (default -20 dBFS)")
    ap.add_argument("--linear-only", action="store_true",
                    help="pure normalisation, no dynamics -- the A/B control")
    a = ap.parse_args(argv)

    x, sr = read_wav(Path(a.src))
    if a.linear_only:
        y = normalize(x, a.ceiling)
        stats = {"mode": "linear only",
                 "peak_before_db": float(_db(np.abs(x).max())),
                 "peak_after_db": float(_db(np.abs(y).max())),
                 "rms_before_db": float(_db(np.sqrt(np.mean(x ** 2)))),
                 "rms_after_db": float(_db(np.sqrt(np.mean(y ** 2))))}
    else:
        y, stats = compress(x, sr, ratio=a.ratio, pivot=a.pivot,
                            max_boost=a.max_boost, floor=a.floor,
                            knee=a.knee, ceiling=a.ceiling,
                            target_rms=a.target_rms)

    # saturation is prevented, then checked
    assert np.abs(y).max() < 1.0, "clipped"
    write_wav(Path(a.dst), y, sr)

    print(Path(a.dst).name)
    for k, v in stats.items():
        print(f"  {k:22s} {v if not isinstance(v, float) else round(v, 2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
