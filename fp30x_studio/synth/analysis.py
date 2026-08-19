"""Fitting the instrument model to recorded single notes, instead of guessing it.

Everything in :mod:`.model` used to be a number somebody chose. This module is
the other half: it takes isolated recorded notes, pulls the partials out of
them, and returns the coefficients the model actually asks for -- ``B`` per
register, the amplitude rolloff and how velocity moves it, the per-partial
decay exponent, the two-stage decay, and the unison beat rate.

Method, in one paragraph
------------------------
A struck string is a sum of exponentially decaying sinusoids, so the estimator
is a heterodyne filter bank rather than an STFT peak tracker. For each partial
we already have a frequency estimate for, multiply the signal by
``exp(-2 pi i f_n t)``, lowpass at ``f0 / 2.4`` and decimate: what is left is
that one partial's complex envelope at baseband, sampled densely enough to see
both its decay and any beating between the strings of the unison. The partial
frequencies themselves come from a long zero-padded DFT of a window just after
the onset, peak-picked near ``n f0`` and refined by quadratic interpolation on
the log magnitude, then ``f0`` and ``B`` are recovered together by regressing
``(f_n / n)^2`` on ``n^2`` -- which is exactly linear in the stiff-string law
``f_n = n f0 sqrt(1 + B n^2)``, so no iteration is needed and the residual is a
direct test of whether the law holds at all.

The decay rate of each partial is the slope of ``log |envelope|`` against time,
fitted by weighted least squares over the span where the partial is at least
6 dB above the tail noise floor. Weighting by amplitude matters: the tail of a
decaying partial is mostly measurement noise, and an unweighted fit on the log
of it is dominated by the part of the signal that is not the partial.

What this module does not do
----------------------------
It does not decide anything. It returns numbers and their scatter; the choice
of what goes in a preset is made in the fitting CLI and written down there with
the measurement next to it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = [
    "NOTE_RE",
    "note_to_midi",
    "PartialFit",
    "NoteFit",
    "load_mono",
    "find_onset",
    "estimate_f0_and_B",
    "partial_envelope",
    "fit_decay",
    "fit_two_stage",
    "beat_frequency",
    "fit_strike_point",
    "analyse_note",
    "fit_rolloff",
]

#: ``Piano.mf.Gb4.aiff`` -> dynamic ``mf``, note ``Gb4``.
NOTE_RE = re.compile(r"^(?P<inst>[A-Za-z0-9_]+)\.(?P<dyn>[a-z]+)\.(?P<note>[A-G]b?#?-?\d)\.")

_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_to_midi(name: str) -> int:
    """``Gb4`` / ``F#4`` / ``C1`` -> MIDI number, with C4 = 60."""
    m = re.match(r"^([A-G])(b|#)?(-?\d+)$", name)
    if not m:
        raise ValueError(f"cannot parse note name {name!r}")
    pc = _PC[m.group(1)]
    if m.group(2) == "b":
        pc -= 1
    elif m.group(2) == "#":
        pc += 1
    return pc + 12 * (int(m.group(3)) + 1)


@dataclass(slots=True)
class PartialFit:
    """One partial of one note."""

    n: int
    freq: float           #: Hz, measured
    amp0: float           #: linear amplitude extrapolated back to the onset
    alpha: float          #: 1/s, early decay rate, fitted over the first 25 dB
    tau: float            #: s, = 1/alpha
    r2: float             #: goodness of the log-linear decay fit
    span_s: float         #: how long the fitted window lasted
    snr_db: float
    alpha_full: float = float("nan")   #: same fit run to the noise floor
    r2_full: float = float("nan")


@dataclass(slots=True)
class NoteFit:
    """One recorded note, reduced to the model's own parameters."""

    path: str
    midi: int
    dynamic: str
    f0: float
    f0_nominal: float
    B: float
    B_stderr: float
    inharm_resid_cents: float
    n_partials_measured: int
    n_partials_audible: int      #: partials within 60 dB of the strongest
    rolloff: float
    rolloff_r2: float
    decay_exponent: float
    decay_exponent_stderr: float
    decay_exponent_r2: float
    tau1: float
    two_stage_ratio: float
    two_stage_mix: float
    beat_hz: float
    beat_cents: float
    beat_strength: float
    beat_depth: float
    strike_point: float
    strike_point_corr: float
    attack_ms: float
    partials: list[PartialFit] = field(default_factory=list)


# ---------------------------------------------------------------------------
# I/O and onset
# ---------------------------------------------------------------------------

def load_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """Mono float64 and its sample rate. Stereo is averaged, not picked.

    The Iowa piano was recorded with one mic over the bass strings and one over
    the treble; either channel alone tilts the spectrum by where the mic was,
    and the average is the closer thing to what the instrument radiated.
    """
    import soundfile as sf

    x, sr = sf.read(str(path), dtype="float64", always_2d=True)
    return x.mean(axis=1), int(sr)


def find_onset(x: np.ndarray, sr: int, floor_db: float = -40.0) -> int:
    """Index of the strike: the last sample below ``floor_db`` before the peak.

    Backing up from the peak rather than forward from zero is deliberate --
    these files have room tone and, in a few of them, the tail of the previous
    note in the chromatic scale, and a forward scan finds that instead.
    """
    env = np.abs(x)
    k = int(0.001 * sr)
    if k > 1:
        env = np.convolve(env, np.ones(k) / k, mode="same")
    pk = int(np.argmax(env))
    thr = env[pk] * 10.0 ** (floor_db / 20.0)
    below = np.nonzero(env[:pk] < thr)[0]
    return int(below[-1]) if below.size else 0


# ---------------------------------------------------------------------------
# Partial frequencies, f0 and B together
# ---------------------------------------------------------------------------

def _refine_peak(mag: np.ndarray, i: int) -> float:
    """Quadratic interpolation on the log magnitude. Sub-bin, and unbiased for
    a windowed sinusoid to well under a tenth of a bin."""
    if i <= 0 or i >= mag.size - 1:
        return float(i)
    a, b, c = (math.log(max(mag[j], 1e-300)) for j in (i - 1, i, i + 1))
    d = a - 2 * b + c
    return float(i) if abs(d) < 1e-30 else float(i) - 0.5 * (c - a) / d


def estimate_f0_and_B(x: np.ndarray, sr: int, onset: int, f0_nominal: float,
                      n_max: int = 32, win_s: float = 0.6,
                      ) -> tuple[float, float, float, float, list[tuple[int, float, float]]]:
    """``(f0, B, B_stderr, resid_cents, [(n, f_n, mag_n)])``.

    The window starts 20 ms after the strike -- past the hammer noise, which is
    broadband and would otherwise be peak-picked as a partial -- and is long
    enough to resolve ``f0`` at the bottom of the keyboard.

    ``f_n = n f0 sqrt(1 + B n^2)`` rearranges to ``(f_n/n)^2 = f0^2 + f0^2 B n^2``,
    which is a straight line in ``n^2``. Fitting it that way rather than by
    nonlinear least squares means the residual is interpretable: it is how far
    the measured series departs from *any* stiff-string law, in cents.
    """
    start = onset + int(0.020 * sr)
    n_win = int(win_s * sr)
    seg = x[start:start + n_win]
    if seg.size < 1024:
        raise ValueError("segment too short")
    seg = seg * np.hanning(seg.size)
    nfft = 1 << int(math.ceil(math.log2(seg.size * 8)))
    mag = np.abs(np.fft.rfft(seg, nfft))
    df = sr / nfft

    # A local noise floor, so "is there a partial here" is answered against the
    # spectrum's own background rather than against a fixed dB below the peak.
    # A treble note has four partials and thirty bands of hiss; a fixed
    # threshold accepts the hiss and the regression then fits it.
    k = max(3, int(round(f0_nominal * 1.5 / df)))
    pad = np.pad(mag, (k, k), mode="edge")
    floor = np.array([np.median(pad[i:i + 2 * k + 1]) for i in
                      range(0, mag.size, max(1, k // 2))])
    floor = np.interp(np.arange(mag.size),
                      np.arange(floor.size) * max(1, k // 2), floor)

    def pick(f_pred: float, half: float):
        lo = int(max(1, (f_pred - half) / df))
        hi = int(min(mag.size - 2, (f_pred + half) / df))
        if hi <= lo + 2:
            return None
        i = lo + int(np.argmax(mag[lo:hi]))
        if mag[i] < floor[i] * 6.0:
            return None
        return _refine_peak(mag, i) * df, float(mag[i])

    # Pass 1: the first few partials, where inharmonicity has not yet moved
    # anything by more than a few cents, fix f0 on its own.
    low = []
    for n in range(1, 6):
        got = pick(n * f0_nominal, 0.035 * n * f0_nominal)
        if got:
            low.append((n, got[0], got[1]))
    if len(low) < 2:
        raise ValueError("no usable low partials")
    f0 = float(np.average([f / n for n, f, _ in low],
                          weights=[m for *_, m in low]))
    B = 0.0

    # Pass 2: walk up, predicting each partial from the current (f0, B) and
    # refitting as we go. Three consecutive misses ends the series -- that is
    # the measurement of "how many partials are actually there", which is a
    # question the model has been answering with a constant 28.
    found: list[tuple[int, float, float]] = []
    misses = 0
    for n in range(1, n_max + 1):
        f_pred = n * f0 * math.sqrt(max(1.0 + B * n * n, 1e-9))
        if f_pred > sr * 0.45:
            break
        half = min(0.42 * f0, 0.5 * f_pred)
        got = pick(f_pred, half)
        if got is None:
            misses += 1
            if misses >= 3 and n > 4:
                break
            continue
        misses = 0
        found.append((n, got[0], got[1]))
        if len(found) >= 4:
            f0, B = _fit_stiff(found)[:2]

    if len(found) < 3:
        raise ValueError("too few partials")

    f0, B, B_stderr, resid_cents = _fit_stiff(found)
    return f0, B, B_stderr, resid_cents, found


def _fit_stiff(found: list[tuple[int, float, float]]
               ) -> tuple[float, float, float, float]:
    """Weighted, outlier-trimmed fit of ``(f_n/n)^2 = f0^2 (1 + B n^2)``."""
    nn = np.array([n for n, _, _ in found], dtype=float)
    ff = np.array([f for _, f, _ in found], dtype=float)
    ww = np.array([m for _, _, m in found], dtype=float)
    ww = ww / ww.max()

    keep = np.ones(nn.size, dtype=bool)
    coef = np.zeros(2)
    for _ in range(3):
        n_, f_, w_ = nn[keep], ff[keep], ww[keep]
        if n_.size < 3:
            keep = np.ones(nn.size, dtype=bool)
            n_, f_, w_ = nn, ff, ww
        y = (f_ / n_) ** 2
        X = np.stack([np.ones_like(n_), n_ ** 2], axis=1)
        s = np.sqrt(w_)[:, None]
        coef, *_ = np.linalg.lstsq(X * s, y * s[:, 0], rcond=None)
        if coef[0] <= 0:
            break
        f0_, B_ = math.sqrt(coef[0]), coef[1] / coef[0]
        pred_all = nn * f0_ * np.sqrt(np.maximum(1.0 + B_ * nn ** 2, 1e-9))
        cents = np.abs(1200 * np.log2(ff / pred_all))
        new = cents < max(12.0, 2.5 * float(np.median(cents[keep])))
        if new.sum() < 3 or (new == keep).all():
            keep = new if new.sum() >= 3 else keep
            break
        keep = new

    if coef[0] <= 0:
        raise ValueError("degenerate f0 fit")
    f0 = math.sqrt(coef[0])
    B = float(coef[1] / coef[0])

    n_, f_, w_ = nn[keep], ff[keep], ww[keep]
    y = (f_ / n_) ** 2
    X = np.stack([np.ones_like(n_), n_ ** 2], axis=1)
    resid = y - X @ coef
    dof = max(n_.size - 2, 1)
    s2 = float(resid @ resid) / dof
    try:
        cov = s2 * np.linalg.inv(X.T @ X)
        B_stderr = float(math.sqrt(max(cov[1, 1], 0.0))) / float(coef[0])
    except np.linalg.LinAlgError:
        B_stderr = float("nan")
    pred = n_ * f0 * np.sqrt(np.maximum(1.0 + B * n_ ** 2, 1e-9))
    resid_cents = float(np.sqrt(np.mean((1200 * np.log2(f_ / pred)) ** 2)))
    return f0, B, B_stderr, resid_cents


# ---------------------------------------------------------------------------
# Per-partial envelopes
# ---------------------------------------------------------------------------

def partial_envelope(x: np.ndarray, sr: int, onset: int, f: float, bw: float,
                     dec: int | None = None) -> tuple[np.ndarray, np.ndarray, int]:
    """Complex baseband envelope of one partial: ``(t, |env|, sr_env)``.

    Heterodyne to DC, lowpass with a windowed-sinc of bandwidth ``bw``, then
    decimate. This is a narrowband filter whose passband is placed exactly on
    the measured partial frequency, so it is immune to the bin-quantisation and
    the time/frequency trade that makes an STFT track wander on a decaying tone.
    """
    from scipy.signal import fftconvolve

    seg = x[onset:]
    t = np.arange(seg.size) / sr
    z = seg * np.exp(-2j * math.pi * f * t)

    # FIR lowpass, Hann-windowed sinc, transition band ~ bw. Convolved through
    # the FFT: the direct form is O(N M) and with a 16k-tap filter over a
    # half-million samples that is ten gigaflops per partial, which turns a
    # forty-note sweep into an overnight job for no gain in accuracy.
    ntaps = int(max(31, 4 * sr / max(bw, 1.0)))
    ntaps |= 1
    ntaps = min(ntaps, 16384)
    m = (ntaps - 1) / 2
    k = np.arange(ntaps) - m
    fc = bw / sr
    h = 2 * fc * np.sinc(2 * fc * k) * np.hanning(ntaps)
    h /= h.sum()
    y = fftconvolve(z, h, mode="same")

    if dec is None:
        dec = max(1, int(sr / (8 * max(bw, 1.0))))
    y = y[::dec]
    sr_env = sr / dec
    return np.arange(y.size) / sr_env, np.abs(y), int(round(sr_env))


def fit_decay(t: np.ndarray, a: np.ndarray, skip_s: float = 0.03,
              floor_db: float = 6.0, drop_db: float | None = 25.0,
              ) -> tuple[float, float, float, float, float]:
    """``(alpha, amp0, r2, span_s, snr_db)`` from ``log a = log amp0 - alpha t``.

    The fit starts after ``skip_s`` -- the first few tens of milliseconds are
    the attack and the hammer, not the free decay -- and stops at whichever
    comes first: ``drop_db`` below where it started, or ``floor_db`` above the
    noise floor estimated from the last tenth of the record. Weighted by
    amplitude, because the quiet end of a decay is where the noise is.

    ``drop_db`` is not cosmetic. This recording is **not anechoic**, and a room
    with a 1.5 s reverberation time puts a floor under every measured decay:
    once a partial has fallen far enough, what the microphone hears is the room
    decaying, not the string. Restricting the fit to the first 25 dB is the
    early-decay-time convention for exactly that reason. Passing ``None`` fits
    the whole span and gives the reverb-contaminated number, which is worth
    having only as the other end of the bracket.
    """
    if a.size < 16:
        return float("nan"), 0.0, 0.0, 0.0, 0.0
    noise = float(np.median(a[int(a.size * 0.9):])) if a.size > 40 else 1e-12
    noise = max(noise, 1e-12)
    peak = float(np.max(a))
    snr_db = 20 * math.log10(peak / noise)

    i0 = int(np.searchsorted(t, t[int(np.argmax(a))] + skip_s))
    if i0 >= a.size - 8:
        return float("nan"), peak, 0.0, 0.0, snr_db
    thr = noise * 10 ** (floor_db / 20.0)
    if drop_db is not None:
        thr = max(thr, a[i0] * 10 ** (-drop_db / 20.0))
    alive = np.nonzero(a[i0:] > thr)[0]
    if alive.size < 16:
        return float("nan"), peak, 0.0, 0.0, snr_db
    i1 = i0 + int(alive[-1])
    tt, aa = t[i0:i1], a[i0:i1]
    if tt.size < 16:
        return float("nan"), peak, 0.0, 0.0, snr_db

    w = aa / aa.max()
    Y = np.log(np.maximum(aa, 1e-300))
    X = np.stack([np.ones_like(tt), tt], axis=1)
    W = np.sqrt(w)[:, None]
    coef, *_ = np.linalg.lstsq(X * W, Y * W[:, 0], rcond=None)
    amp0, alpha = math.exp(coef[0]), -float(coef[1])
    pred = X @ coef
    ss_res = float(np.sum(w * (Y - pred) ** 2))
    ss_tot = float(np.sum(w * (Y - np.average(Y, weights=w)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return alpha, amp0, r2, float(tt[-1] - tt[0]), snr_db


def fit_two_stage(t: np.ndarray, a: np.ndarray, alpha1: float
                  ) -> tuple[float, float]:
    """``(ratio, mix)`` for ``(1-mix) e^{-t/tau} + mix e^{-t/(tau*ratio)}``.

    A grid search rather than a solver: with two nearly-collinear exponentials
    the likelihood surface is a long flat valley and a gradient method lands
    wherever it was initialised. The grid is coarse because the parameter is
    coarse -- what matters is whether the aftersound is 2x or 6x the prompt
    sound, not the third digit.
    """
    if not np.isfinite(alpha1) or alpha1 <= 0 or a.size < 32:
        return float("nan"), float("nan")
    tau = 1.0 / alpha1
    i0 = int(np.argmax(a))
    tt = t[i0:] - t[i0]
    aa = a[i0:] / a[i0]
    keep = aa > 10 ** (-50 / 20)
    tt, aa = tt[keep], aa[keep]
    if tt.size < 32:
        return float("nan"), float("nan")
    logaa = np.log(aa)
    best = (1e30, float("nan"), float("nan"))
    for ratio in np.arange(1.5, 20.01, 0.25):
        for mix in np.concatenate([[0.0], np.geomspace(0.002, 0.45, 40)]):
            model = (1 - mix) * np.exp(-tt / tau) + mix * np.exp(-tt / (tau * ratio))
            e = float(np.mean((np.log(np.maximum(model, 1e-300)) - logaa) ** 2))
            if e < best[0]:
                best = (e, float(ratio), float(mix))
    return best[1], best[2]


def beat_frequency(t: np.ndarray, a: np.ndarray, alpha: float
                   ) -> tuple[float, float, float]:
    """``(beat_hz, strength, depth)`` from the amplitude modulation on a partial.

    Divide out the fitted exponential, then take the periodogram of what is
    left. A unison beats; a single string does not, so the strength -- the
    fraction of the residual variance in the strongest peak -- is the number
    that says whether the answer means anything. ``depth`` is the modulation
    depth of that component on the normalised envelope, ``sqrt(2)`` times the
    RMS of the residual restricted to the peak, which is the quantity the model
    calls ``unison_depth``.
    """
    if not np.isfinite(alpha) or a.size < 64:
        return float("nan"), 0.0, float("nan")
    keep = a > a.max() * 10 ** (-30 / 20)
    tt, aa = t[keep], a[keep]
    if tt.size < 64:
        return float("nan"), 0.0, float("nan")
    r = aa / (aa[0] * np.exp(-alpha * (tt - tt[0])))
    r = r / max(np.mean(r), 1e-12) - 1.0
    r = r - np.mean(r)
    sr_env = 1.0 / float(np.mean(np.diff(tt)))
    w = np.hanning(r.size)
    pad = r.size * 4
    spec = np.abs(np.fft.rfft(r * w, pad)) ** 2
    fr = np.fft.rfftfreq(pad, 1.0 / sr_env)
    band = (fr > 0.3) & (fr < 25.0)
    if not band.any():
        return float("nan"), 0.0, float("nan")
    idx = np.nonzero(band)[0]
    j = idx[int(np.argmax(spec[band]))]
    total = float(np.sum(spec[1:]))
    strength = float(spec[j] * 3) / total if total > 0 else 0.0
    # Parseval on the peak and its two neighbours, undone for the Hann window's
    # coherent gain, gives the depth of that one cosine.
    lobe = float(np.sum(spec[max(j - 2, 1):j + 3]))
    depth = math.sqrt(2.0 * lobe / max(np.sum(spec[1:]), 1e-30)) * float(np.std(r))
    return float(fr[j]), min(strength, 1.0), float(depth)


def fit_strike_point(ns: np.ndarray, amps: np.ndarray, rolloff: float
                     ) -> tuple[float, float]:
    """``(alpha, gain)``: where the hammer hit, from the notches it left.

    A hammer at a fraction ``alpha`` of the speaking length cannot drive a mode
    with a node there, so ``a_n`` carries a factor ``|sin(n pi alpha)|`` -- a
    comb of nulls at ``n = 1/alpha, 2/alpha, ...``. Divide out the fitted
    ``n^-rolloff`` tilt and what is left should be that comb, so ``alpha`` is
    found by scanning it and taking the value that best explains the residual.
    ``gain`` is the correlation at the optimum: near zero means the notches are
    not there and the strike point is not identified for that note.
    """
    if ns.size < 8 or not np.isfinite(rolloff):
        return float("nan"), 0.0
    resid = np.log(np.maximum(amps, 1e-300)) + rolloff * np.log(ns.astype(float))
    resid = resid - resid.mean()
    best = (-2.0, float("nan"))
    for al in np.arange(0.06, 0.201, 0.0005):
        m = np.log(np.maximum(np.abs(np.sin(ns * math.pi * al)), 1e-3))
        m = m - m.mean()
        d = float(np.linalg.norm(m) * np.linalg.norm(resid))
        c = float(m @ resid) / d if d > 0 else 0.0
        if c > best[0]:
            best = (c, float(al))
    return best[1], best[0]


def fit_rolloff(ns: np.ndarray, amps: np.ndarray) -> tuple[float, float]:
    """``(rolloff, r2)`` from ``log a_n = c - rolloff log n``.

    Partials near a hammer-strike node are notched by tens of dB and are not
    evidence about the tilt of the spectrum, so the fit is on the *upper
    envelope*: within each half-octave band of n, only the loudest partial is
    kept. That is the same reason a spectral-envelope estimator uses a peak
    picker rather than the raw bins.
    """
    if ns.size < 4:
        return float("nan"), 0.0
    ln, la = np.log(ns.astype(float)), np.log(np.maximum(amps, 1e-300))
    edges = np.arange(0.0, ln.max() + 0.36, 0.35)
    keep = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (ln >= lo) & (ln < hi)
        if m.any():
            keep.append(int(np.nonzero(m)[0][int(np.argmax(la[m]))]))
    if len(keep) < 3:
        keep = list(range(ns.size))
    ln, la = ln[keep], la[keep]
    X = np.stack([np.ones_like(ln), ln], axis=1)
    coef, *_ = np.linalg.lstsq(X, la, rcond=None)
    pred = X @ coef
    ss_res = float(np.sum((la - pred) ** 2))
    ss_tot = float(np.sum((la - la.mean()) ** 2))
    return -float(coef[1]), (1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0)


# ---------------------------------------------------------------------------
# One note, end to end
# ---------------------------------------------------------------------------

def analyse_note(path: str | Path, midi: int | None = None,
                 dynamic: str = "", n_max: int = 32,
                 max_s: float = 12.0) -> NoteFit:
    """Everything the preset needs, from one recorded note."""
    path = Path(path)
    if midi is None or not dynamic:
        m = NOTE_RE.match(path.name)
        if not m:
            raise ValueError(f"cannot read note/dynamic out of {path.name!r}")
        midi = midi if midi is not None else note_to_midi(m.group("note"))
        dynamic = dynamic or m.group("dyn")

    x, sr = load_mono(path)
    onset = find_onset(x, sr)
    x = x[: onset + int(max_s * sr)]
    f0_nom = 440.0 * 2.0 ** ((midi - 69) / 12.0)

    f0, B, B_se, resid_cents, found = estimate_f0_and_B(x, sr, onset, f0_nom, n_max)

    # Attack: 10% to peak on a 1 ms-smoothed broadband envelope.
    env = np.abs(x[onset:onset + int(0.25 * sr)])
    k = max(1, int(0.001 * sr))
    env = np.convolve(env, np.ones(k) / k, mode="same")
    pk = int(np.argmax(env))
    rise = np.nonzero(env[:pk + 1] >= 0.1 * env[pk])[0]
    attack_ms = 1000.0 * (pk - int(rise[0])) / sr if rise.size else float("nan")

    bw = max(f0 / 2.4, 4.0)
    fits: list[PartialFit] = []
    for n, f_meas, _ in found:
        f = n * f0 * math.sqrt(1.0 + B * n * n)
        if f > sr * 0.45:
            break
        # Trust the measured peak when it is close to the law, the law when it
        # is not: a spurious peak would otherwise drag the filter off-partial.
        if abs(1200 * math.log2(max(f_meas, 1e-9) / f)) < 60:
            f = f_meas
        te, ae, _ = partial_envelope(x, sr, onset, f, bw)
        alpha, amp0, r2, span, snr = fit_decay(te, ae)
        a_full, _, r2_full, _, _ = fit_decay(te, ae, drop_db=None)
        fits.append(PartialFit(n=n, freq=f, amp0=amp0, alpha=alpha,
                               tau=(1.0 / alpha if alpha > 0 else float("nan")),
                               r2=r2, span_s=span, snr_db=snr,
                               alpha_full=a_full, r2_full=r2_full))

    amps = np.array([p.amp0 for p in fits])
    ns = np.array([p.n for p in fits])
    peak_amp = float(amps.max()) if amps.size else 1.0
    audible = int(np.sum(amps > peak_amp * 10 ** (-60 / 20)))

    # A partial counts as measured if its decay is a decay: a real slope, a
    # log-linear fit that holds, and enough headroom over the noise that the
    # slope is the string's and not the room's.
    good = np.array([np.isfinite(p.alpha) and p.alpha > 0 and p.r2 > 0.70
                     and p.snr_db > 25 for p in fits])
    rolloff, roll_r2 = fit_rolloff(ns[amps > peak_amp * 1e-4],
                                   amps[amps > peak_amp * 1e-4])

    # tau_n = tau_1 n^-e  <=>  log alpha_n = log alpha_1 + e log n
    dec_e = dec_se = dec_r2 = float("nan")
    if good.sum() >= 3:
        ln = np.log(ns[good].astype(float))
        la = np.log(np.array([p.alpha for p in fits])[good])
        X = np.stack([np.ones_like(ln), ln], axis=1)
        coef, *_ = np.linalg.lstsq(X, la, rcond=None)
        dec_e = float(coef[1])
        pred = X @ coef
        res = la - pred
        dof = max(ln.size - 2, 1)
        s2 = float(res @ res) / dof
        try:
            cov = s2 * np.linalg.inv(X.T @ X)
            dec_se = float(math.sqrt(max(cov[1, 1], 0.0)))
        except np.linalg.LinAlgError:
            dec_se = float("nan")
        ss_tot = float(np.sum((la - la.mean()) ** 2))
        dec_r2 = 1.0 - float(res @ res) / ss_tot if ss_tot > 0 else 0.0

    tau1 = fits[0].tau if fits and np.isfinite(fits[0].tau) else float("nan")
    te, ae, _ = partial_envelope(x, sr, onset, fits[0].freq, bw) if fits else (
        np.zeros(0), np.zeros(0), 1)
    ratio, mix = fit_two_stage(te, ae, fits[0].alpha) if fits else (
        float("nan"), float("nan"))
    beat_hz, beat_str, beat_dep = beat_frequency(te, ae, fits[0].alpha) if fits else (
        float("nan"), 0.0, float("nan"))
    strike, strike_c = fit_strike_point(ns[amps > peak_amp * 1e-4],
                                        amps[amps > peak_amp * 1e-4], rolloff)
    # Two strings a cents apart beat at |f1 - f2| = f0 * (2^(c/1200) - 1).
    beat_cents = (1200 * math.log2(1 + beat_hz / f0)
                  if np.isfinite(beat_hz) and f0 > 0 else float("nan"))

    return NoteFit(
        path=str(path), midi=int(midi), dynamic=dynamic, f0=f0, f0_nominal=f0_nom,
        B=B, B_stderr=B_se, inharm_resid_cents=resid_cents,
        n_partials_measured=len(fits), n_partials_audible=audible,
        rolloff=rolloff, rolloff_r2=roll_r2,
        decay_exponent=dec_e, decay_exponent_stderr=dec_se, decay_exponent_r2=dec_r2,
        tau1=tau1, two_stage_ratio=ratio, two_stage_mix=mix,
        beat_hz=beat_hz, beat_cents=beat_cents, beat_strength=beat_str,
        beat_depth=beat_dep, strike_point=strike, strike_point_corr=strike_c,
        attack_ms=attack_ms, partials=fits)
