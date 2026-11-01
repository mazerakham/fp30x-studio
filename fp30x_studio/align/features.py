r"""Event features for the correlation stage — and why twelve dimensions is not enough.

:func:`~.corr.chroma` sends each onset event to its pitch-class indicator in
:math:`\mathbb{R}^{12}`. That is the pushforward along the octave quotient
:math:`\pi:\mathbb{R}^{88}\to\mathbb{R}^{12}`, whose **kernel is 76-dimensional**:
register is discarded before the comparison begins, and everything downstream
inherits a similarity that cannot tell a bass octave from a treble one.

The cost of that was measured, not guessed. In
``docs/VALIDATION-score-alignment.md`` the diagonal statistic on 12-dimensional
chroma was scored for 138 windows against a 515-score corpus: the median z of
the *true* piece (5.50) came out **below** the median z of the best *wrong*
score (5.72). On a typical window the best wrong answer beat the right one, and
a usable operating point existed only in the far tail — precision 1.0 at recall
0.22. Six "magnet" score regions (Op. 19 bars 152-168 chief among them, 22
spurious wins across 8 unrelated takes) won repeatedly because their local
chroma is near-uniform, so their cosine against *anything* is elevated. A
chromatic run is only ambiguous once you have thrown its register away.

Everything here keeps the interface :func:`~.corr.chroma` has — ``(events) ->
(n, d)`` with L2-normalised rows — so it drops into ``Q @ R.T`` and
:func:`~.corr.diagonal_sums` unchanged, and the GEMM cost is merely linear in
``d``. Nothing in this module changes the behaviour of :mod:`.corr`,
:mod:`.correspond` or :mod:`.warp`.

The measured ranking of these (same windows, same 515-score pool, gate on the
margin to the best *different* work) is recorded in the validation document.
In one line: **register is what was missing.** On the same 122 windows and the
same 515-score pool, gating on ``margin >= 2.0``, the recall available at
precision 1.0 went 0.18 (chroma) -> 0.38 (:func:`pitch_full`) -> **0.60**
(:func:`pitch_full` with :func:`intervals` concatenated at weight 0.5). The
median inversion is undone with it: true-piece z 5.07 against best-decoy 5.74
on chroma, 9.34 against 6.16 on the winning pair. The extra dimensions cost one
factor in the GEMM and nothing else.
"""
from __future__ import annotations

import numpy as np

__all__ = ["MIDI_LO", "MIDI_HI", "pitch_full", "pitch_bands", "intervals",
           "concat", "register_contour", "event_frequency", "idf_weights", "apply_weights",
           "normalise"]

#: The 88 keys of the instrument. Anything outside is clipped into the end key
#: rather than dropped: a score with a stray out-of-range note is a notation
#: artefact, and losing the event would put every later onset out of step.
MIDI_LO, MIDI_HI = 21, 108


def normalise(X: np.ndarray) -> np.ndarray:
    """L2-normalise rows in place, leaving all-zero rows at zero.

    Zero rows are left alone deliberately: an event that contributed no
    feature (an interval feature has no predecessor at index 0) must
    contribute nothing to the diagonal, not a NaN that poisons the whole
    correlation.
    """
    n = np.linalg.norm(X, axis=1, keepdims=True)
    np.divide(X, n, out=X, where=n > 0)
    return X


def pitch_full(events, *, weight_velocity: bool = False) -> np.ndarray:
    """(n, 88) L2-normalised key indicators — chroma with the register kept.

    The one change from :func:`~.corr.chroma` that mattered. Two events share a
    dimension only if they struck the same *key*, so a chromatic figure in the
    bass no longer resembles the same figure two octaves up, which is precisely
    what let the magnet passages of §5.3 win against unrelated takes.

    ``weight_velocity`` is offered for symmetry with :func:`~.corr.chroma` and
    should be left off for score matching: :func:`~.score.load_score` gives
    every notated note the same velocity 64, so switching it on weights only
    the performance side and breaks the cosine's symmetry.
    """
    X = np.zeros((len(events), MIDI_HI - MIDI_LO + 1), dtype=np.float32)
    for i, e in enumerate(events):
        for p in e.notes:
            X[i, min(max(int(p), MIDI_LO), MIDI_HI) - MIDI_LO] = 1.0
        if weight_velocity:
            X[i] *= (e.velocity / 127.0) ** 0.5
    return normalise(X)


def pitch_bands(events, *, bands: int = 3) -> np.ndarray:
    """(n, 12 * bands) pitch class crossed with a coarse register band.

    The middle of the interpolation between chroma and :func:`pitch_full`: it
    keeps enough register to separate a bass line from a melody while still
    matching a passage played an octave out, which real practice does. Bands
    split the keyboard into equal spans of keys, not into named octaves — the
    boundary position is arbitrary either way, and equal spans keep the
    dimension count honest.
    """
    d = MIDI_HI - MIDI_LO + 1
    X = np.zeros((len(events), 12 * bands), dtype=np.float32)
    for i, e in enumerate(events):
        for p in e.notes:
            q = min(max(int(p), MIDI_LO), MIDI_HI)
            b = min((q - MIDI_LO) * bands // d, bands - 1)
            X[i, b * 12 + q % 12] = 1.0
    return normalise(X)


def intervals(events, *, span: int = 13, both_voices: bool = True) -> np.ndarray:
    """(n, 27) or (n, 54) one-hot melodic interval from the previous event.

    Transposition-invariant *without* the octave collapse — on paper a strictly
    better invariance trade than chroma, and it is what
    :mod:`fp30x_studio.identify` matches on. The outer voices are used because
    they are the two the ear tracks and the two an onset cluster identifies
    unambiguously: top note for the melody, bottom note for the bass.

    Intervals beyond ``span`` semitones saturate into the end bin. A leap of a
    twelfth and a leap of two octaves are not usefully different features, and
    an unbounded one-hot would spend most of its dimensions on noise.

    Measured result, recorded so the negative is not re-derived: **on its own
    this is the worst feature tested** — it never reached precision 1.0 at any
    margin, topping out at 0.74, worse than the 12-dimensional chroma it was
    meant to improve on. It buys an invariance these takes do not need (none of
    them is played transposed) and pays for it by discarding absolute pitch,
    the most discriminating thing an event carries. Its use is as a
    concatenated *addition* to :func:`pitch_full`, where the contour supplies
    the order information a bag-of-keys row cannot: that combination is the
    best measured feature set, 0.60 recall at precision 1.0 against 0.38 for
    register alone. Weight 0.5 was best; 0.25 and 1.0 both give about 0.50, so
    the choice is a broad optimum and not a knife edge.
    """
    w = 2 * span + 1
    d = 2 * w if both_voices else w
    X = np.zeros((len(events), d), dtype=np.float32)
    top = np.array([max(e.notes) for e in events], dtype=np.int32)
    bot = np.array([min(e.notes) for e in events], dtype=np.int32)
    for i in range(1, len(events)):
        X[i, span + int(np.clip(top[i] - top[i - 1], -span, span))] = 1.0
        if both_voices:
            X[i, w + span + int(np.clip(bot[i] - bot[i - 1], -span, span))] = 1.0
    return normalise(X)


def concat(*parts: tuple[np.ndarray, float]) -> np.ndarray:
    """Concatenate already-normalised feature blocks with relative weights.

    Each block is scaled by ``sqrt(weight)`` before stacking, so a cosine
    between two concatenated rows is the weighted mean of the per-block
    cosines — the weights mean what they look like they mean.
    """
    blocks = [p * np.float32(np.sqrt(w)) for p, w in parts]
    X = np.hstack(blocks).astype(np.float32)
    return normalise(X)


def register_contour(events, *, contour_weight: float = 0.5) -> np.ndarray:
    """The feature set that won: 88 keys, plus outer-voice contour at half weight.

    This is what to call. Measured on 122 windows against the full 515-score
    corpus with the gate ``margin >= 2.0``: precision 1.000 at recall 0.598,
    against 1.000 at 0.180 for :func:`~.corr.chroma` on the same windows. It
    also settles a take chroma could not: ``2026-08-21-nocturne`` seg 0 fires
    on Op. 62 No. 1 at margin 2.4 (z = 10.3) on two independent windows, where
    chroma reached z 5.06 and never cleared the gate.

    It does **not** repeal the 120-event window floor. At W = 80 no margin up
    to 3.0 buys precision 1.0, exactly as with chroma: too little music is too
    little music, whatever you embed it in.
    """
    return concat((pitch_full(events), 1.0),
                  (intervals(events), contour_weight))


def event_frequency(rows_per_score, d: int) -> np.ndarray:
    """Fraction of corpus *events* in which each feature dimension fires.

    Event frequency, not document frequency: nearly every score touches nearly
    every middle-register key at some point, so a per-score document frequency
    is 1.0 across most of the keyboard and its IDF weight collapses to zero.
    What varies usefully is how often a key is struck.

    ``rows_per_score`` is an iterable of (m, d) feature matrices, one per
    score. This is the aligner's missing analogue of
    :attr:`fp30x_studio.identify.matcher.ThemeIndex.dropped`: the identifier
    already drops n-gram hashes held by too many works, and the correlation
    stage had no equivalent while its worst false positives came from exactly
    that — texture common to the whole corpus.
    """
    ef = np.zeros(d, dtype=np.float64)
    n = 0
    for X in rows_per_score:
        ef += (np.abs(X) > 0).sum(axis=0)
        n += len(X)
    return ef / max(n, 1)


def idf_weights(freq: np.ndarray, *, floor: float = 1e-3) -> np.ndarray:
    """``sqrt(log(1/freq))`` per dimension, for use with :func:`apply_weights`.

    **This did not help.** Applied to :func:`pitch_full` over the 515-score
    corpus it moved recall at precision 1.0 from 0.377 to 0.385 — noise on 122
    windows. The magnet passages it was aimed at are not made of individually
    common *keys*; they are common *textures*, and a per-dimension weight
    cannot see a texture. Keeping register was what actually killed them. The
    function stays because the corpus statistic is cheap and a texture-level
    weighting would still be built on it.

    The square root is there because the weights are applied to the *vectors*
    and the cosine is bilinear: weighting a dimension by ``sqrt(w)`` on both
    sides weights its contribution to the similarity by ``w``, which is the
    IDF one actually means.
    """
    return np.sqrt(np.log(1.0 / np.maximum(freq, floor))).astype(np.float32)


def apply_weights(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Scale columns by ``w`` and re-normalise rows."""
    return normalise((X * w[None, :]).astype(np.float32))
