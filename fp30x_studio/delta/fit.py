r"""The real valued half: the delta from the page to the playing.

The baseline is the score read literally. Fix one tempo, in seconds per quarter
note :math:`\sigma`, and one origin :math:`t_0`; the baseline performance plays
score note :math:`i`, whose notated onset is :math:`b_i` quarter notes and whose
notated length is :math:`\ell_i` quarter notes, at

.. math::   \hat T_i = t_0 + \sigma b_i,
            \qquad \hat d_i = \sigma \ell_i,
            \qquad \hat P_i = \bar P ,

with one constant velocity. :math:`(t_0,\sigma)` are the least squares fit of
the matched performed onsets, which is exactly the ordinary regression of
:math:`T_{\mu(i)}` on :math:`b_i`, and :math:`\bar P` is the mean matched
velocity, which is the constant that minimises the same criterion for velocity.

The delta carries three real components per matched score note, all of them
zero when the playing is the baseline:

.. math::

   r_i = \frac{T_{\mu(i)} - \hat T_i}{\sigma}, \qquad
   v_i = \frac{P_{\mu(i)}}{127}, \qquad
   a_i = \log \frac{d_{\mu(i)}}{\sigma \ell_i}.

:math:`r_i` is in quarter notes, so it is a tempo free quantity and two takes at
different speeds are still comparable. :math:`a_i` is a log ratio, so legato and
staccato are symmetric about zero and doubling every length is a translation.
:math:`v_i` is reported raw in :math:`[0,1]` as well as centred at
:math:`\bar P`; the raw vector is nearly parallel to the all ones vector for any
two performances, so the centred one is the one that discriminates.

Rubato against touch
--------------------
:math:`r` mixes two things a player would not call the same. Broad tempo
shaping moves whole phrases; the placement of one note inside a chord moves one
note. Smoothing :math:`r` against **beat position** with a Gaussian kernel of
width :math:`\sigma_b` quarter notes,

.. math::

   \rho_i = \frac{\sum_j e^{-(b_i-b_j)^2/2\sigma_b^2}\, r_j}
                 {\sum_j e^{-(b_i-b_j)^2/2\sigma_b^2}},
   \qquad m_i = r_i - \rho_i ,

splits it into the part that persists over a couple of beats and the part that
does not. :math:`\rho` is rubato and :math:`m` is touch, and
:math:`r = \rho + m` exactly, so nothing is lost by looking at them separately.

The smoother is Nadaraya-Watson against beat position rather than against index,
because notes are not uniformly spaced in time and a rest would otherwise be
treated as an instant.

Local tempo. :math:`\rho` is an offset in quarter notes, and a player who is
running late by a growing amount is playing slow, so the local tempo implied by
the rubato curve is

.. math::   \text{qpm}(b) = \frac{60}{\sigma\,\bigl(1 + \rho'(b)\bigr)} .

That is what the report plots, evaluated on a uniform beat grid where the
derivative is a difference rather than a fit.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .match import Matching, match_notes
from .perform import PerfDoc, load_performance
from .score import ScoreDoc, load_score_notes

__all__ = ["Delta", "TempoFit", "fit_delta", "smooth_over_beats",
           "local_tempo_curve", "RUBATO_SIGMA_BEATS", "write_delta",
           "read_delta"]

#: Width of the rubato smoother, in quarter notes. Two beats: long enough that
#: one note cannot bend the curve, short enough to follow a phrase.
RUBATO_SIGMA_BEATS = 2.0

#: MIDI velocity full scale.
V_MAX = 127.0


@dataclass(frozen=True)
class TempoFit:
    """The single constant tempo the baseline is played at."""

    qpm: float
    seconds_per_beat: float
    t0: float
    rms_residual_s: float
    rms_residual_beats: float
    n: int


def _fit_tempo(beats: np.ndarray, times: np.ndarray) -> TempoFit:
    if len(beats) < 2:
        raise ValueError("a tempo needs at least two matched onsets")
    A = np.stack([np.ones_like(beats), beats], axis=1)
    (t0, spb), *_ = np.linalg.lstsq(A, times, rcond=None)
    if spb <= 0:
        raise ValueError("the least squares tempo came out non positive; the "
                         "matching is not monotone in time")
    resid = times - (t0 + spb * beats)
    rms = float(np.sqrt(np.mean(resid ** 2)))
    return TempoFit(qpm=60.0 / float(spb), seconds_per_beat=float(spb),
                    t0=float(t0), rms_residual_s=rms,
                    rms_residual_beats=rms / float(spb), n=len(beats))


def smooth_over_beats(beats: np.ndarray, values: np.ndarray, *,
                      sigma: float = RUBATO_SIGMA_BEATS,
                      at: np.ndarray | None = None) -> np.ndarray:
    """Nadaraya-Watson smoother with a Gaussian kernel over beat position."""
    x = np.asarray(beats, float)
    y = np.asarray(values, float)
    q = x if at is None else np.asarray(at, float)
    out = np.empty(len(q))
    # Blocked so a long piece does not build one dense n by n kernel matrix.
    step = max(1, int(2_000_000 // max(len(x), 1)))
    for s in range(0, len(q), step):
        d = (q[s:s + step, None] - x[None, :]) / sigma
        w = np.exp(-0.5 * d * d)
        denom = w.sum(axis=1)
        denom[denom == 0.0] = 1.0
        out[s:s + step] = (w @ y) / denom
    return out


def local_tempo_curve(beats: np.ndarray, rubato: np.ndarray, spb: float, *,
                      sigma: float = RUBATO_SIGMA_BEATS, n_grid: int = 400
                      ) -> tuple[np.ndarray, np.ndarray]:
    """Local tempo in quarter notes per minute, on a uniform beat grid."""
    if len(beats) < 2:
        return np.zeros(0), np.zeros(0)
    grid = np.linspace(float(beats[0]), float(beats[-1]), n_grid)
    rho = smooth_over_beats(beats, rubato, sigma=sigma, at=grid)
    drho = np.gradient(rho, grid)
    return grid, 60.0 / (spb * np.clip(1.0 + drho, 0.05, 20.0))


@dataclass
class Delta:
    """One performance, as the operations that carry the page to it."""

    score_source: str
    score_sha256: str
    score_title: str
    score_edition: str
    score_notes: int
    score_measures: int
    performance_source: str
    performance_sha256: str
    performance_notes: int
    tempo: dict
    counts: dict
    norms: dict
    matched: list[dict]              #: one row per matched score note
    insertions: list[dict]
    deletions: list[dict]
    event_census: dict = field(default_factory=dict)
    matching_note: str = ""
    parse_report: str = ""
    rubato_sigma_beats: float = RUBATO_SIGMA_BEATS

    # -- the component vectors, as arrays keyed by score note index -------

    def index(self) -> np.ndarray:
        return np.array([row["i"] for row in self.matched], dtype=int)

    def component(self, name: str) -> np.ndarray:
        return np.array([np.nan if row[name] is None else row[name]
                         for row in self.matched], dtype=float)

    def as_map(self, name: str) -> dict[int, float]:
        return {row["i"]: (float("nan") if row[name] is None else float(row[name]))
                for row in self.matched}


COMPONENTS = ("timing", "rubato", "micro", "velocity", "velocity_centred",
              "articulation")


def fit_delta(score: ScoreDoc | str | Path, perf: PerfDoc | str | Path, *,
              sigma: float = RUBATO_SIGMA_BEATS, band: int | None = None
              ) -> Delta:
    """Match, fit the baseline, and take the difference."""
    if not isinstance(score, ScoreDoc):
        score = load_score_notes(score)
    if not isinstance(perf, PerfDoc):
        perf = load_performance(perf)

    kw = {} if band is None else {"band": band}
    m: Matching = match_notes(score, perf, **kw)

    beats = np.array([score.notes[i].beat for i, _ in m.pairs])
    times = np.array([perf.notes[j].t_on for _, j in m.pairs])
    tf = _fit_tempo(beats, times)
    spb = tf.seconds_per_beat

    baseline = tf.t0 + spb * beats
    timing = (times - baseline) / spb
    rubato = smooth_over_beats(beats, timing, sigma=sigma)
    micro = timing - rubato

    vel = np.array([perf.notes[j].velocity for _, j in m.pairs]) / V_MAX
    vbar = float(vel.mean())
    vel_c = vel - vbar

    art = np.full(len(m.pairs), np.nan)
    for k, (i, j) in enumerate(m.pairs):
        ell = score.notes[i].dur_ql
        d = perf.notes[j].dur
        if ell > 0 and d > 0:
            art[k] = math.log(d / (spb * ell))

    matched = []
    for k, (i, j) in enumerate(m.pairs):
        sn, pn = score.notes[i], perf.notes[j]
        matched.append({
            "i": i, "j": j, "measure": sn.measure, "pitch": sn.pitch,
            "beat": round(sn.beat, 6), "notated_ql": round(sn.dur_ql, 6),
            "t_perf": round(pn.t_on, 6), "t_baseline": round(float(baseline[k]), 6),
            "dur_perf": round(pn.dur, 6),
            "timing": round(float(timing[k]), 6),
            "rubato": round(float(rubato[k]), 6),
            "micro": round(float(micro[k]), 6),
            "velocity": round(float(vel[k]), 6),
            "velocity_centred": round(float(vel_c[k]), 6),
            "articulation": (None if math.isnan(art[k])
                             else round(float(art[k]), 6)),
            "grace": sn.is_grace,
        })

    # An inserted note is placed at the bar of the nearest matched score note,
    # measured in performance time, so the report can say where it happened.
    ins = []
    if m.pairs:
        mt = times
        bars = np.array([score.notes[i].measure for i, _ in m.pairs])
        for j in m.insertions:
            pn = perf.notes[j]
            k = int(np.argmin(np.abs(mt - pn.t_on)))
            ins.append({"j": j, "pitch": pn.pitch, "t": round(pn.t_on, 6),
                        "near_measure": int(bars[k]),
                        "velocity": pn.velocity})
    dels = [{"i": i, "pitch": score.notes[i].pitch,
             "measure": score.notes[i].measure,
             "beat": round(score.notes[i].beat, 6)} for i in m.deletions]

    def norm(v):
        v = v[np.isfinite(v)]
        if len(v) == 0:
            return {"n": 0, "l2": 0.0, "rms": 0.0}
        return {"n": int(len(v)), "l2": float(np.linalg.norm(v)),
                "rms": float(np.sqrt(np.mean(v ** 2)))}

    norms = {"timing": norm(timing), "rubato": norm(rubato),
             "micro": norm(micro), "velocity": norm(vel),
             "velocity_centred": norm(vel_c), "articulation": norm(art)}
    norms["timing_rms_ms"] = norms["timing"]["rms"] * spb * 1000.0
    norms["micro_rms_ms"] = norms["micro"]["rms"] * spb * 1000.0
    norms["mean_velocity"] = vbar

    return Delta(
        score_source=score.source, score_sha256=score.sha256,
        score_title=score.title, score_edition=score.edition,
        score_notes=len(score.notes), score_measures=score.n_measures,
        performance_source=perf.source, performance_sha256=perf.sha256,
        performance_notes=len(perf.notes),
        tempo=asdict(tf), counts=m.counts, norms=norms, matched=matched,
        insertions=ins, deletions=dels, event_census=m.event_census,
        matching_note=m.note, parse_report=perf.defects,
        rubato_sigma_beats=sigma,
    )


def _plain(o):
    """numpy scalars carried out of the aligner's census are still numbers."""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def write_delta(d: Delta, path: str | Path) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(d), indent=1, default=_plain),
                    encoding="utf-8")
    return path


def read_delta(path: str | Path) -> Delta:
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return Delta(**raw)
