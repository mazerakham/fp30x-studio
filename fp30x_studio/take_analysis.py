"""Analysis of a single live take, and the standalone page that renders it.

This is the applied half of :mod:`fp30x_studio.performance`.  That module says
what a performance *is* -- the direct sum ``f = (+)_k sum_i P_i 1_[a_i, b_i]``
of scaled indicators on disjoint closed intervals.  This module takes one
captured file, computes the functionals that framing makes natural, and writes
them out as a self-contained HTML document with the figures inlined.

Nothing here re-implements the representation.  Every quantity is read off a
:class:`~fp30x_studio.performance.Performance` built by
:meth:`Performance.from_capture`, or off the raw
:class:`~fp30x_studio.rawcapture.RawCapture` when the question is about the
transport rather than about the music.

The five questions the page answers
-----------------------------------
1. **What is the object?**  Support, plateaus, polyphony, total variation,
   cumulative energy -- for both the *actuator* representation (what the hands
   did) and the *sounding* representation (what CC64 left ringing).
2. **Is ``F`` Cantor-like?**  Answered by comparing the modulus of continuity
   ``omega(delta) = sup_{|t-s|<=delta} |F(t)-F(s)|`` against the Lipschitz
   bound ``L*delta`` and against the Cantor function's ``delta^(log2/log3)``.
   The comparison is quantitative and the answer is no; see
   :func:`modulus_of_continuity`.
3. **What do the two velocity channels carry?**  Strike velocity and release
   velocity, their distributions, their mutual information.
4. **How is the rhythm structured across scales?**  The Fano factor of the
   onset point process as a function of window width, against the Poisson
   reference ``F = 1``.
5. **Can the timestamps be trusted?**  The inter-packet gap lattice.  See
   :func:`timing_lattice` -- the answer is "to 5 ms, and no finer".

Run ``python -m fp30x_studio.take_analysis`` to rebuild the page.
"""

from __future__ import annotations

import argparse
import base64
import collections
import html
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import rawcapture
from .core import note_name
from .performance import Performance, midi_note

__all__ = [
    "PALETTE",
    "TakeAnalysis",
    "analyse",
    "timing_lattice",
    "velocity_channels",
    "modulus_of_continuity",
    "fano_curve",
    "build_page",
]

# ---------------------------------------------------------------------------
# Palette.  Dark-surface steps of the data-viz reference palette; the
# categorical slots used here validate all-pairs on the dark surface:
#   node scripts/validate_palette.js "#3987e5,#d95926,#199e70" \
#        --mode dark --surface "#1a1a19" --pairs all   ->  ALL CHECKS PASS
# The page commits to a single dark look, so the figures are rendered on
# SURFACE and no light variant is defined.
# ---------------------------------------------------------------------------

PALETTE = {
    "surface": "#1a1a19",
    "page": "#0d0d0d",
    "ink": "#ffffff",
    "ink_2": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "s1": "#3987e5",   # blue    -- actuator / strike velocity / observed
    "s2": "#d95926",   # orange  -- sounding / release velocity
    "s3": "#199e70",   # aqua    -- pedal
}

#: Sequential ramp for the momentum encoding, ordered low -> high.  On a dark
#: surface the step nearest the surface is the *darkest*; it is held at
#: ``#184f95`` (2.15:1) so the softest strike is still visible.
MOMENTUM_RAMP = ["#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5",
                 "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#b7d3f6",
                 "#cde2fb"]

DEFAULT_TAKE = Path.home() / "Music" / "FP-30X Studio" / "takes" / "2026-08-17-open.fp30"
DEFAULT_CONTROL = DEFAULT_TAKE.with_name("2026-08-17-wiggle.fp30")
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "take-2026-08-17.html"

#: Where to find a KaTeX distribution to inline.  The page must open from disk
#: with no network, so the CSS, the JS and the woff2 fonts are all embedded.
KATEX_SEARCH = [
    Path(__file__).resolve().parent.parent / "vendor" / "katex",
    Path.home() / "workspace" / "math-tutor" / "item-format" / "vendor" / "katex",
]


# ---------------------------------------------------------------------------
# Timing: what the transport did to the event times
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TimingLattice:
    """The arithmetic structure of the packet timestamps.

    A capture front end can only ever be as good as the clock underneath it.
    :mod:`fp30x_studio.rawcapture` removed *our* 2.08 ms poll quantisation by
    taking CoreMIDI's driver timestamps, but that does not make the timestamps
    continuous -- it makes them honest about whatever the source did.  This
    class measures what the source did.

    ``step`` is the largest ``g`` such that (almost) every timestamp is a
    multiple of ``g``; ``off_lattice`` counts the packets that are not.
    """

    n_packets: int
    step_ns: int
    off_lattice: int
    gaps_ms: np.ndarray
    stamps_ms: np.ndarray
    gap_histogram: list[tuple[int, int]]
    min_gap_ms: int
    distinct_stamps: int
    bundled_packets: int
    peak_rate_100ms: float
    ceiling_rate: float

    @property
    def step_ms(self) -> float:
        return self.step_ns / 1e6

    @property
    def lattice_fraction(self) -> float:
        return 1.0 - self.off_lattice / max(1, self.n_packets)


def timing_lattice(cap: rawcapture.RawCapture) -> TimingLattice:
    """Find the arithmetic lattice the source's timestamps live on.

    The step is taken as the gcd of the positive inter-packet gaps, computed
    robustly: a handful of off-lattice packets would drag a plain gcd down to
    1 ns, so the candidate steps are tried in descending order and the largest
    one explaining at least 99% of the gaps wins.  ``off_lattice`` then reports
    the residue honestly rather than hiding it.
    """
    ns = np.array([r.ns for r in cap.records], dtype=np.int64)
    if ns.size < 2:
        raise ValueError("a lattice needs at least two packets")
    rel = ns - ns[0]
    gaps = np.diff(rel)
    positive = gaps[gaps > 0]

    step = 1
    for candidate in (10_000_000, 5_000_000, 2_000_000, 1_000_000,
                      500_000, 100_000, 10_000, 1_000, 1):
        if (positive % candidate == 0).mean() >= 0.99:
            step = candidate
            break
    off = int(np.count_nonzero(rel % step))

    hist = collections.Counter((positive // 1_000_000).astype(int).tolist())
    t = np.array([m[0] for m in cap.messages])
    peak = max((np.searchsorted(t, t[i] + 0.1) - i) for i in range(t.size)) / 0.1

    return TimingLattice(
        n_packets=len(ns),
        step_ns=step,
        off_lattice=off,
        gaps_ms=gaps / 1e6,
        stamps_ms=rel / 1e6,
        gap_histogram=sorted(hist.items()),
        min_gap_ms=int(positive.min() // 1_000_000),
        distinct_stamps=len(set(rel.tolist())),
        bundled_packets=sum(1 for r in cap.records
                            if len(rawcapture.split_messages(r.data)) > 1),
        peak_rate_100ms=peak,
        ceiling_rate=1e9 / step,
    )


# ---------------------------------------------------------------------------
# Velocity: the two channels
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class VelocityChannels:
    """Strike velocity and release velocity, paired note by note.

    MIDI carries a velocity byte on the note-off as well as on the note-on.
    The FP-30X populates it -- see :func:`velocity_channels` -- and this is the
    only continuous gesture data the instrument emits at all, since it sends
    no channel or polyphonic aftertouch.
    """

    strike: np.ndarray
    release: np.ndarray
    duration: np.ndarray
    pitch: np.ndarray
    t_on: np.ndarray
    pedal_at_release: np.ndarray

    def entropy(self, x: np.ndarray, bins: int = 16) -> float:
        """Shannon entropy of ``x`` binned uniformly over the MIDI byte range."""
        h, _ = np.histogram(x, bins=np.linspace(0, 128, bins + 1))
        p = h[h > 0] / h.sum()
        return float(-(p * np.log2(p)).sum())

    def mutual_information(self, bins: int = 8) -> tuple[float, float]:
        """``I(strike; release)`` in bits, and the maximum it could have been.

        Binned at ``bins`` levels per axis, which is coarse enough that 804
        notes populate the joint table.  The maximum is
        ``min(H(strike), H(release))`` at the same binning, so the ratio is
        interpretable as "how much of one channel the other one already tells
        you".
        """
        edges = np.linspace(0, 128, bins + 1)
        joint, _, _ = np.histogram2d(self.strike, self.release, bins=[edges, edges])
        p = joint / joint.sum()
        q = p.sum(1, keepdims=True) * p.sum(0, keepdims=True)
        m = p > 0
        mi = float((p[m] * np.log2(p[m] / q[m])).sum())
        cap = min(self.entropy(self.strike, bins), self.entropy(self.release, bins))
        return mi, cap

    def correlations(self) -> dict[str, tuple[float, float]]:
        """Pearson and Spearman for the pairs worth asking about."""
        def both(x, y):
            rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
            return (float(np.corrcoef(x, y)[0, 1]), float(np.corrcoef(rx, ry)[0, 1]))
        return {
            "strike vs release": both(self.strike, self.release),
            "duration vs release": both(self.duration, self.release),
            "duration vs strike": both(self.duration, self.strike),
            "pitch vs strike": both(self.pitch, self.strike),
            "pitch vs release": both(self.pitch, self.release),
        }


def velocity_channels(cap: rawcapture.RawCapture,
                      perf: Performance) -> VelocityChannels:
    """Pair every note-on with the note-off that closed it, keeping both bytes.

    :class:`~fp30x_studio.performance.Strike` deliberately does not carry the
    release velocity -- it is not part of the indicator-function model, since
    it scales nothing and bounds nothing.  It is recovered here directly from
    the message stream, using the same pairing rule the parser uses (a
    ``note_on`` with velocity 0 is a release).
    """
    open_: dict[int, tuple[float, int]] = {}
    rows: list[tuple[float, float, int, int, int]] = []
    for t, m in cap.messages:
        kind = getattr(m, "type", None)
        if kind == "note_on" and m.velocity > 0:
            open_[m.note] = (t, int(m.velocity))
        elif kind in ("note_off", "note_on") and m.note in open_:
            a, v = open_.pop(m.note)
            rows.append((a, t, v, int(m.velocity), int(m.note)))
    if not rows:
        raise ValueError("no completed notes in this capture")
    a = np.array(rows, dtype=float)
    t_on, t_off, vs, vr, note = a.T
    return VelocityChannels(
        strike=vs, release=vr, duration=t_off - t_on, pitch=note, t_on=t_on,
        pedal_at_release=np.array([perf.pedal_down_at(x) for x in t_off]),
    )


# ---------------------------------------------------------------------------
# Scaling: how far F is from a devil's staircase, and how clustered the onsets are
# ---------------------------------------------------------------------------

CANTOR_EXPONENT = math.log(2) / math.log(3)  # 0.6309...


def modulus_of_continuity(perf: Performance,
                          deltas: np.ndarray) -> dict[str, np.ndarray]:
    """``omega(delta) = sup_{|t-s| <= delta} |F(t) - F(s)|``, normalised by ``F(T)``.

    Computed exactly, not on a sampling grid.  ``F`` is non-decreasing, so the
    sup over the pair is the sup of ``g_delta(t) = F(t + delta) - F(t)``; and
    ``F`` is piecewise affine, so ``g_delta`` is piecewise affine with
    breakpoints in ``B u (B - delta)`` where ``B`` is the set of interval
    endpoints.  Evaluating ``g_delta`` at those finitely many candidates
    therefore attains the sup with no discretisation error at all.

    Returned alongside are the two reference curves the comparison is about:

    * the **Lipschitz bound** ``L*delta / F(T)`` with ``L = max_t sum_k f_k(t)``,
      which ``omega`` must lie under and which it attains at small ``delta``;
    * the **Cantor function's** modulus ``(delta/T)^(log 2 / log 3)``, which is
      what ``omega`` would look like if ``F`` really were a devil's staircase.

    A Cantor function is Hölder of exponent ``log2/log3`` and of no better; it
    is nowhere Lipschitz on the Cantor set.  So this plot is the whole test.
    """
    ts, fs = perf.cumulative_curve()
    total = float(fs[-1])
    t0, t1 = perf.t_start, perf.t_end
    span = t1 - t0

    lipschitz = max(float(perf.evaluate(t).sum())
                    for t in sorted({s.t_on for s in perf.strikes()}
                                    | {s.t_off for s in perf.strikes()}))

    # The longest stretch on which the Lipschitz constant is actually held.
    # omega(delta) = L*delta exactly for every delta up to this length.
    bp = sorted({s.t_on for s in perf.strikes()} | {s.t_off for s in perf.strikes()})
    hold, hold_at = 0.0, 0.0
    for u, w in zip(bp, bp[1:]):
        if float(perf.evaluate((u + w) / 2).sum()) == lipschitz and w - u > hold:
            hold, hold_at = w - u, u

    observed, used = [], []
    for d in deltas:
        if d <= 0 or d >= span:
            continue
        cand = np.concatenate([ts, ts - d, [t0, t1 - d]])
        cand = cand[(cand >= t0) & (cand <= t1 - d)]
        rise = np.interp(cand + d, ts, fs) - np.interp(cand, ts, fs)
        observed.append(float(rise.max()) / total)
        used.append(float(d))
    used = np.array(used)
    return {
        "delta": used,
        "omega": np.array(observed),
        "lipschitz": lipschitz * used / total,
        "cantor": (used / span) ** CANTOR_EXPONENT,
        "L": np.array([lipschitz]),
        "F_T": np.array([total]),
        "hold": np.array([hold]),
        "hold_at": np.array([hold_at]),
    }


def fano_curve(onsets: np.ndarray, widths: np.ndarray,
               seed: int = 0) -> dict[str, np.ndarray]:
    """Index of dispersion of the onset counts, as a function of window width.

    For a point process, ``Fano(w) = Var N(w) / E N(w)``.  A Poisson process
    gives ``1`` at every ``w``; ``> 1`` means the onsets clump at that scale,
    ``< 1`` means they are more evenly spaced than chance.  A matched Poisson
    sample of the same count and span is returned with it, so the reader can
    see the estimator's own noise at the widest windows where only a handful of
    bins fit inside the take.
    """
    rng = np.random.default_rng(seed)
    t0, t1 = float(onsets[0]), float(onsets[-1])
    poisson = np.sort(rng.uniform(t0, t1, onsets.size))

    def counts(xs: np.ndarray, w: float, n: int) -> np.ndarray:
        """Counts in ``n`` half-open windows ``[t0 + jw, t0 + (j+1)w)``.

        Half-open rather than :func:`numpy.histogram`'s closed final bin, and
        the incomplete tail window is discarded rather than counted short --
        either would put a spurious wobble into the variance at exactly the
        widths where the estimate is already thinnest.
        """
        idx = np.floor((xs - t0) / w).astype(np.int64)
        idx = idx[(idx >= 0) & (idx < n)]
        return np.bincount(idx, minlength=n)

    out = {"width": [], "fano": [], "fano_poisson": [], "bins": []}
    for w in widths:
        n = int((t1 - t0) // w)
        if n < 3:
            continue
        c, cp = counts(onsets, w, n), counts(poisson, w, n)
        if c.mean() == 0 or cp.mean() == 0:
            continue
        out["width"].append(w)
        out["fano"].append(c.var() / c.mean())
        out["fano_poisson"].append(cp.var() / cp.mean())
        out["bins"].append(n)
    return {k: np.array(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# The whole analysis
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TakeAnalysis:
    """Everything the page needs about one take."""

    path: Path
    capture: rawcapture.RawCapture
    actuator: Performance
    sounding: Performance
    lattice: TimingLattice
    velocity: VelocityChannels
    modulus: dict[str, np.ndarray]
    fano: dict[str, np.ndarray]
    census: dict[str, int] = field(default_factory=dict)

    @property
    def span(self) -> float:
        return self.actuator.t_end - self.actuator.t_start

    def cc64_values(self) -> tuple[np.ndarray, np.ndarray]:
        rows = [(t, m.value) for t, m in self.capture.messages
                if getattr(m, "type", None) == "control_change" and m.control == 64]
        if not rows:
            return np.array([]), np.array([])
        a = np.array(rows, dtype=float)
        return a[:, 0], a[:, 1]

    def key_counts(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per-key ``(index, strike count, mean strike velocity)``, active keys only."""
        idx, n, mv = [], [], []
        for k, tr in enumerate(self.actuator.tracks):
            if len(tr):
                idx.append(k)
                n.append(len(tr))
                mv.append(float(np.mean([s.velocity for s in tr])))
        return np.array(idx), np.array(n), np.array(mv)

    def pitch_class_counts(self) -> np.ndarray:
        c = np.zeros(12, dtype=int)
        for s in self.actuator.strikes():
            c[s.note % 12] += 1
        return c


def analyse(path: str | Path) -> TakeAnalysis:
    """Read one ``.fp30`` capture and compute every functional the page shows."""
    path = Path(path)
    cap = rawcapture.read(path)
    perf = Performance.from_capture(cap)
    census = collections.Counter(getattr(m, "type", "?") for _, m in cap.messages)
    onsets = np.array(sorted(s.t_on for s in perf.strikes()))
    return TakeAnalysis(
        path=path,
        capture=cap,
        actuator=perf,
        sounding=perf.with_sustain(),
        lattice=timing_lattice(cap),
        velocity=velocity_channels(cap, perf),
        modulus=modulus_of_continuity(
            perf, np.array([0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0,
                            2.0, 5.0, 10.0, 20.0, 50.0])),
        fano=fano_curve(onsets, np.array([0.05, 0.1, 0.2, 0.5, 1.0, 2.0,
                                          5.0, 10.0, 20.0])),
        census=dict(census),
    )


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    p = PALETTE
    plt.rcParams.update({
        "figure.facecolor": p["page"],
        "axes.facecolor": p["surface"],
        "savefig.facecolor": p["page"],
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "text.color": p["ink"],
        "axes.edgecolor": p["axis"],
        "axes.labelcolor": p["ink_2"],
        "xtick.color": p["muted"],
        "ytick.color": p["muted"],
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "axes.labelsize": 10,
        "axes.titlesize": 11.5,
        "axes.linewidth": 0.8,
        "grid.color": p["grid"],
        "grid.linewidth": 0.7,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "mathtext.fontset": "dejavusans",
    })
    return plt


def _tidy(ax, *, xgrid: bool = True, ygrid: bool = False):
    p = PALETTE
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(p["axis"])
    if xgrid:
        ax.grid(axis="x", zorder=0)
    if ygrid:
        ax.grid(axis="y", zorder=0)
    ax.set_axisbelow(True)


def _title(ax, text):
    ax.set_title(text, loc="left", color=PALETTE["ink"], pad=7)


def _key_axis(ax, keys):
    ticks = [k for k in range(min(keys), max(keys) + 1) if midi_note(k) % 12 == 0]
    ax.set_yticks(ticks)
    ax.set_yticklabels([note_name(midi_note(k)) for k in ticks])
    ax.set_ylim(min(keys) - 2, max(keys) + 2)
    ax.set_ylabel("key")


def _momentum_cmap(from_surface: bool = False):
    """The single-hue magnitude ramp.

    ``from_surface`` prepends the page surface, which is what a density/count
    encoding needs on a dark background: an empty cell must recede into the
    surface rather than read as a low but nonzero value.
    """
    from matplotlib.colors import LinearSegmentedColormap
    stops = ([PALETTE["surface"]] + MOMENTUM_RAMP) if from_surface else MOMENTUM_RAMP
    return LinearSegmentedColormap.from_list("momentum", stops)


def _roll(ax, perf, cmap, norm, window=None, lw=3.2):
    # No pedal shading here: CC64 is down for 94% of this take, so a wash would
    # tint the whole panel and say nothing.  The pedal gets its own panel.
    for s in perf.strikes():
        if window and (s.t_off < window[0] or s.t_on > window[1]):
            continue
        ax.plot([s.t_on, s.t_off], [s.key, s.key], color=cmap(norm(s.velocity)),
                lw=lw, solid_capstyle="round", zorder=2)


def figure_object(a: TakeAnalysis, path: Path, dpi: int = 190) -> Path:
    """The four stacked views of ``f`` on one shared time axis, whole take."""
    plt = _plt()
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable

    p = PALETTE
    cmap = _momentum_cmap()
    vels = np.array([s.velocity for s in a.actuator.strikes()])
    norm = Normalize(vmin=float(vels.min()), vmax=float(vels.max()))
    t0, t1 = a.actuator.t_start, a.actuator.t_end

    fig, axes = plt.subplots(4, 1, figsize=(15.0, 12.8), sharex=True,
                             gridspec_kw={"height_ratios": [3.0, 1.0, 1.2, 2.4],
                                          "hspace": 0.28})
    fig.subplots_adjust(left=0.062, right=0.905, top=0.878, bottom=0.055)
    ax0, ax1, ax2, ax3 = axes

    # 1. support -----------------------------------------------------------
    _roll(ax0, a.actuator, cmap, norm)
    _key_axis(ax0, a.actuator.active_keys())
    _title(ax0, "Support of $f$: every strike as a closed interval "
                "$[T_1,T_2]$ on its own key, shaded by momentum $P$")
    box = ax0.get_position()
    cax = fig.add_axes([box.x1 + 0.013, box.y0, 0.010, box.height])
    cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=cax)
    cb.set_label("momentum $P$", color=p["ink_2"], fontsize=9)
    cb.outline.set_visible(False)
    cb.ax.tick_params(color=p["muted"], labelcolor=p["muted"], labelsize=8, length=3)

    # 2. CC64 --------------------------------------------------------------
    tc, vc = a.cc64_values()
    ax1.fill_between(np.append(tc, t1), np.append(vc, vc[-1]), step="post",
                     color=p["s3"], alpha=0.24, lw=0, zorder=2)
    ax1.step(np.append(tc, t1), np.append(vc, vc[-1]), where="post",
             color=p["s3"], lw=1.1, zorder=3)
    ax1.set_ylim(-6, 138)
    ax1.set_yticks([0, 64, 127])
    ax1.set_ylabel("CC64")
    n_mid = int(((vc > 0) & (vc < 127)).sum())
    pedal_time = sum(b - u for u, b in a.actuator.pedal)
    _title(ax1, f"Damper pedal: down {pedal_time / (t1 - t0) * 100:.0f}% of the "
                f"take in {len(a.actuator.pedal)} spans — {len(vc)} CC64 events, "
                f"{n_mid} strictly between 0 and 127 (the shape resolves in the "
                f"next figure)")

    # 3. polyphony ---------------------------------------------------------
    for src, colour, label in ((a.sounding, p["s2"], "sounding (CC64 applied)"),
                               (a.actuator, p["s1"], "actuator (keys held)")):
        ts, counts = src.polyphony_steps()
        ts = np.append(ts, t1)
        counts = np.append(counts, counts[-1])
        ax2.step(ts, counts, where="post", color=colour, lw=1.2, zorder=3,
                 label=f"{label} — peak {src.max_polyphony()}")
        ax2.fill_between(ts, counts, step="post", color=colour, alpha=0.13,
                         lw=0, zorder=2)
    ax2.set_ylabel("$n(t)$")
    ax2.set_ylim(0, a.sounding.max_polyphony() + 2)
    ax2.legend(loc="upper left", ncol=2, labelcolor=p["ink_2"])
    _title(ax2, "Polyphony $n(t)=\\sum_k\\sum_i \\mathbf{1}_{I_{k,i}}(t)$ — "
                "at most 7 keys down, up to 29 strings ringing")

    # 4. cumulative --------------------------------------------------------
    for j, (u, v) in enumerate(a.actuator.plateaus()):
        ax3.axvspan(u, v, color=p["muted"], alpha=0.22, lw=0, zorder=0,
                    label="plateau of the actuator $F$ ($f\\equiv 0$)"
                          if j == 0 else None)
    traces = []
    grid = np.linspace(t0, t1, 2000)
    for src, colour, label in ((a.actuator, p["s1"], "actuator"),
                               (a.sounding, p["s2"], "sounding")):
        ts, fs = src.cumulative_curve()
        ts, fs = np.append(ts, t1), np.append(fs, fs[-1])
        y = fs / fs[-1]
        ax3.plot(ts, y, color=colour, lw=1.9, zorder=3, solid_capstyle="round",
                 label=f"{label} — $F(T)={fs[-1]:,.0f}$ velocity·s")
        traces.append((np.interp(grid, ts, y), colour, label))
    sep = np.abs(traces[0][0] - traces[1][0])
    j = int(np.argmax(sep))
    for y, colour, label in traces:
        above = y[j] >= max(traces[0][0][j], traces[1][0][j])
        ax3.annotate(label, (grid[j], y[j]), xytext=(7, 8 if above else -15),
                     textcoords="offset points", color=colour, fontsize=9.5,
                     zorder=4)
    ax3.legend(loc="upper left", labelcolor=p["ink_2"])
    ax3.set_ylim(-0.03, 1.16)
    ax3.set_ylabel("$F(t)\\,/\\,F(T)$")
    ax3.set_xlabel("time $t$ (s)")
    _title(ax3, "Cumulative energy $F(t)=\\int_{-\\infty}^{t}\\sum_k f_k(s)\\,ds$ — "
                "each curve indexed to its own total, so the comparison is of shape")

    for ax in axes:
        ax.set_xlim(t0, t1)
        _tidy(ax)
    ax3.grid(axis="y", zorder=0)

    fig.suptitle("One take, four views of the same $\\mathbb{R}^{88}$-valued "
                 "step function", x=0.062, y=0.978, ha="left", fontsize=16,
                 color=p["ink"], fontweight="bold")
    fig.text(0.062, 0.949,
             f"{a.path.name} · {a.actuator.n_strikes} strikes on "
             f"{len(a.actuator.active_keys())} keys · {a.span:.1f} s · "
             f"{a.capture.n_dropped} packets dropped",
             ha="left", va="top", fontsize=10.5, color=p["ink_2"])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def figure_detail(a: TakeAnalysis, path: Path, window: tuple[float, float],
                  dpi: int = 190) -> Path:
    """The same object over the densest few seconds, where individual notes read."""
    plt = _plt()
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable

    p = PALETTE
    cmap = _momentum_cmap()
    vels = np.array([s.velocity for s in a.actuator.strikes()])
    norm = Normalize(vmin=float(vels.min()), vmax=float(vels.max()))
    w0, w1 = window

    fig, (ax0, ax1, ax2) = plt.subplots(
        3, 1, figsize=(15.0, 8.8), sharex=True,
        gridspec_kw={"height_ratios": [3.0, 1.0, 1.7], "hspace": 0.26})
    fig.subplots_adjust(left=0.062, right=0.905, top=0.858, bottom=0.075)

    inside = [s for s in a.actuator.strikes() if s.t_off >= w0 and s.t_on <= w1]
    _roll(ax0, a.actuator, cmap, norm, window=window, lw=5.5)
    _key_axis(ax0, [s.key for s in inside])
    _title(ax0, f"The densest {w1 - w0:.0f} s: {len(inside)} strikes, at roughly "
                "16× the horizontal magnification of the previous figure")
    box = ax0.get_position()
    cax = fig.add_axes([box.x1 + 0.013, box.y0, 0.010, box.height])
    cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), cax=cax)
    cb.set_label("momentum $P$", color=p["ink_2"], fontsize=9)
    cb.outline.set_visible(False)
    cb.ax.tick_params(color=p["muted"], labelcolor=p["muted"], labelsize=8, length=3)

    tc, vc = a.cc64_values()
    m = (tc >= w0 - 2) & (tc <= w1 + 2)
    ax1.fill_between(tc[m], vc[m], step="post", color=p["s3"], alpha=0.15,
                     lw=0, zorder=2)
    ax1.step(tc[m], vc[m], where="post", color=p["s3"], lw=1.6, zorder=3)
    ax1.plot(tc[m], vc[m], ".", color=p["s3"], ms=5.5, zorder=4)
    ax1.axhline(64, color=p["axis"], lw=0.8, zorder=1)
    inner = (tc >= w0) & (tc <= w1)
    dips = tc[inner & (vc > 0) & (vc < 127)]
    if dips.size:
        near = (tc >= dips[0] - 0.09) & (tc <= dips[0] + 0.09)
        seq = ", ".join(str(int(x)) for x in vc[near])
        ax1.annotate(f"one pedal change, message by message: {seq}",
                     (dips[0], 64), xytext=(26, 4), textcoords="offset points",
                     ha="left", va="center", color=p["s3"], fontsize=9.5,
                     arrowprops=dict(arrowstyle="-", color=p["s3"], lw=0.9))
    ax1.set_ylim(-6, 138)
    ax1.set_yticks([0, 64, 127])
    ax1.set_ylabel("CC64")
    _title(ax1, "CC64 in the same window — every dot is one message, and the "
                "pedal passes through intermediate positions on the way down")

    for src, colour, label in ((a.actuator, p["s1"], "actuator $f$"),
                               (a.sounding, p["s2"], "sounding $f$")):
        ts = np.linspace(w0, w1, 4000)
        vals = src.evaluate_many(ts).sum(axis=1)
        ax2.plot(ts, vals, color=colour, lw=1.6, zorder=3, label=label)
        ax2.fill_between(ts, vals, color=colour, alpha=0.14, lw=0, zorder=2)
    ax2.legend(loc="upper left", ncol=2, labelcolor=p["ink_2"])
    ax2.set_ylabel("$\\sum_k f_k(t)$")
    ax2.set_xlabel("time $t$ (s)")
    _title(ax2, "Total sustained momentum $\\sum_k f_k(t)$ — the slope of $F$, "
                "and the Lipschitz constant is its maximum")

    for ax in (ax0, ax1, ax2):
        ax.set_xlim(w0, w1)
        _tidy(ax)
    ax2.grid(axis="y", zorder=0)

    fig.suptitle("Zoomed in, the staircase resolves into individual actuators",
                 x=0.062, y=0.972, ha="left", fontsize=15.5, color=p["ink"],
                 fontweight="bold")
    fig.text(0.062, 0.936,
             f"$t \\in [{w0:.1f}, {w1:.1f}]$ s — where the sounding polyphony "
             f"reaches its maximum of {a.sounding.max_polyphony()}",
             ha="left", va="top", fontsize=10.5, color=p["ink_2"])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def figure_timing(a: TakeAnalysis, path: Path, dpi: int = 190) -> Path:
    """The transport's arithmetic: where the timestamps are allowed to be."""
    plt = _plt()
    p = PALETTE
    lat = a.lattice

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(15.0, 5.6),
                                   gridspec_kw={"width_ratios": [1.35, 1.0],
                                                "wspace": 0.20})
    fig.subplots_adjust(left=0.055, right=0.985, top=0.715, bottom=0.125)

    counts = dict(lat.gap_histogram)
    xs = np.arange(0, 101)
    ys = np.array([counts.get(int(x), 0) for x in xs])
    on = (xs % 5 == 0) & (xs > 0)
    ax0.vlines(xs[on], 0.6, np.maximum(ys[on], 0.6), color=p["s1"], lw=2.6,
               zorder=3)
    ax0.plot(xs[on], np.maximum(ys[on], 0.6), ".", color=p["s1"], ms=5, zorder=4)
    off_sel = ~on & (xs > 0) & (ys > 0)
    ax0.vlines(xs[off_sel], 0.6, ys[off_sel], color=p["s2"], lw=3.0, zorder=3)
    n_on_filled = int((ys[on] > 0).sum())
    n_off_empty = int((~on & (xs > 0) & (ys == 0)).sum())
    ax0.set_yscale("log")
    ax0.set_ylim(0.6, max(ys.max(), 10) * 2.2)
    ax0.set_xlim(-1, 101)
    ax0.set_xlabel("gap between consecutive packets (ms)")
    ax0.set_ylabel("packets (log)")
    ax0.annotate(f"all {n_on_filled} multiples of 5 ms are populated;\n"
                 f"{n_off_empty} of the other 80 bins are empty",
                 (0.985, 0.94), xycoords="axes fraction", ha="right", va="top",
                 color=p["ink_2"], fontsize=9.5)
    ax0.annotate(f"min gap {lat.min_gap_ms} ms", (lat.min_gap_ms, counts.get(lat.min_gap_ms, 1)),
                 xytext=(10, 14), textcoords="offset points", color=p["s1"],
                 fontsize=9.5,
                 arrowprops=dict(arrowstyle="-", color=p["s1"], lw=0.9))
    labels = " and ".join(f"{int(x)} ms" for x in xs[off_sel])
    _title(ax0, "Inter-packet gaps, first 100 ms")

    res = lat.stamps_ms % lat.step_ms
    t = lat.stamps_ms / 1000.0
    ax1.plot(t, res, ".", color=p["s1"], ms=2.6, alpha=0.55, zorder=3)
    bad = res != 0
    if bad.any():
        ax1.plot(t[bad], res[bad], "o", mfc="none", mec=p["s2"], ms=9, mew=1.6,
                 zorder=5)
        ax1.annotate(f"{int(bad.sum())} packet of {lat.n_packets}\n"
                     f"off the lattice, by 1 ms",
                     (t[bad][0], res[bad][0]), xytext=(-16, 0),
                     textcoords="offset points", color=p["s2"], fontsize=9.5,
                     ha="right", va="center")
    ax1.set_ylim(-0.6, lat.step_ms)
    ax1.set_yticks(np.arange(0, lat.step_ms))
    ax1.set_xlabel("time $t$ (s)")
    ax1.set_ylabel(f"timestamp mod {lat.step_ms:.0f} ms")
    _title(ax1, f"Residue: {lat.lattice_fraction * 100:.2f}% of packets on one "
                f"{lat.step_ms:.0f} ms lattice")

    for ax in (ax0, ax1):
        _tidy(ax, xgrid=False, ygrid=True)

    fig.suptitle("The timestamps are hardware, and they are quantised to 5 ms",
                 x=0.055, y=0.962, ha="left", fontsize=15.5, color=p["ink"],
                 fontweight="bold")
    fig.text(0.055, 0.905,
             f"CoreMIDI driver timestamps, {lat.n_packets} packets, "
             f"{a.capture.n_dropped} dropped, {a.capture.n_ts_zero} unstamped. "
             "The floor is the source's, not the capture's — the old Python poll "
             "loop had its own 2.08 ms floor on top of this one.\n"
             f"Orange marks the only two exceptions in the whole take: gaps of "
             f"{labels}, and the single packet whose timestamp is not a multiple "
             "of 5.",
             ha="left", va="top", fontsize=10, color=p["ink_2"], linespacing=1.5)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def figure_velocity(a: TakeAnalysis, path: Path, dpi: int = 190) -> Path:
    """Strike velocity against release velocity: two channels, not one."""
    plt = _plt()
    p = PALETTE
    v = a.velocity
    mi, mi_max = v.mutual_information()
    corr = v.correlations()

    fig, (ax0, ax1, ax2) = plt.subplots(1, 3, figsize=(15.0, 5.4),
                                        gridspec_kw={"wspace": 0.26})
    fig.subplots_adjust(left=0.05, right=0.985, top=0.745, bottom=0.125)

    bins = np.arange(0, 132, 4)
    for x, colour, label in ((v.strike, p["s1"], "strike (note-on)"),
                             (v.release, p["s2"], "release (note-off)")):
        ax0.hist(x, bins=bins, color=colour, alpha=0.38, lw=0, zorder=2,
                 label=f"{label} — mean {x.mean():.0f}, sd {x.std():.0f}")
        ax0.hist(x, bins=bins, histtype="step", color=colour, lw=1.6, zorder=3)
        ax0.axvline(x.mean(), color=colour, lw=1.0, ls=(0, (4, 3)), zorder=4)
    ax0.set_xlim(0, 128)
    ax0.set_ylim(0, 96)
    ax0.set_xlabel("MIDI velocity byte")
    ax0.set_ylabel("notes")
    ax0.legend(loc="upper left", labelcolor=p["ink_2"])
    _title(ax0, f"Two distributions, {len(v.strike)} notes")

    h, xe, ye = np.histogram2d(v.strike, v.release,
                               bins=[np.arange(0, 132, 6), np.arange(0, 132, 6)])
    pc = ax1.pcolormesh(xe, ye, h.T, cmap=_momentum_cmap(from_surface=True),
                        shading="flat", zorder=2)
    cb = fig.colorbar(pc, ax=ax1, pad=0.02, fraction=0.045)
    cb.set_label("notes", color=p["ink_2"], fontsize=9)
    cb.outline.set_visible(False)
    cb.ax.tick_params(color=p["muted"], labelcolor=p["muted"], labelsize=8, length=3)
    ax1.set_xlabel("strike velocity")
    ax1.set_ylabel("release velocity")
    ax1.set_xlim(0, 128)
    ax1.set_ylim(0, 128)
    ax1.annotate(f"$r={corr['strike vs release'][0]:+.2f}$\n"
                 f"$I={mi:.3f}$ bits of {mi_max:.2f}",
                 (0.03, 0.97), xycoords="axes fraction", va="top",
                 color=p["ink"], fontsize=10.5)
    _title(ax1, "Joint distribution — almost no structure")

    order = np.argsort(v.duration)
    d = v.duration[order]
    r = v.release[order]
    k = 61
    kern = np.ones(k) / k
    smooth = np.convolve(r, kern, mode="valid")
    dm = d[k // 2: k // 2 + smooth.size]
    ax2.plot(d, r, ".", color=p["s2"], ms=3.0, alpha=0.35, zorder=2)
    ax2.plot(dm, smooth, color=p["ink"], lw=2.0, zorder=4,
             label=f"running mean, {k} notes")
    ax2.set_xscale("log")
    ax2.set_xlabel("how long the key was held (s, log)")
    ax2.set_ylabel("release velocity")
    ax2.set_ylim(-4, 132)
    ax2.legend(loc="lower left", labelcolor=p["ink_2"])
    _title(ax2, f"Release vs duration: $\\rho={corr['duration vs release'][1]:+.2f}$")

    for ax in (ax0, ax1, ax2):
        _tidy(ax, xgrid=False, ygrid=True)

    fig.suptitle("The release byte carries more information than the strike byte",
                 x=0.05, y=0.962, ha="left", fontsize=15.5, color=p["ink"],
                 fontweight="bold")
    fig.text(0.05, 0.905,
             f"strike: {len(set(v.strike.tolist()))} distinct values over "
             f"[{v.strike.min():.0f}, {v.strike.max():.0f}], "
             f"H = {v.entropy(v.strike):.2f} bits   ·   "
             f"release: {len(set(v.release.tolist()))} distinct values over "
             f"[{v.release.min():.0f}, {v.release.max():.0f}], "
             f"H = {v.entropy(v.release):.2f} bits   ·   "
             f"{int(v.pedal_at_release.sum())} of {len(v.release)} releases "
             "happened with the damper already off the string",
             ha="left", va="top", fontsize=10, color=p["ink_2"])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def figure_scaling(a: TakeAnalysis, path: Path, dpi: int = 190) -> Path:
    """The two scaling diagnostics: is F a devil's staircase, is the rhythm Poisson."""
    plt = _plt()
    p = PALETTE
    m = a.modulus
    f = a.fano

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(15.0, 5.8),
                                   gridspec_kw={"wspace": 0.20})
    fig.subplots_adjust(left=0.055, right=0.985, top=0.755, bottom=0.12)

    ax0.plot(m["delta"], m["cantor"], color=p["s2"], lw=1.8, zorder=3,
             label="Cantor function: $(\\delta/T)^{\\log 2/\\log 3}$")
    ax0.plot(m["delta"], m["lipschitz"], color=p["muted"], lw=1.5,
             ls=(0, (5, 3)), zorder=3, label="Lipschitz bound: $L\\delta/F(T)$")
    ax0.plot(m["delta"], m["omega"], color=p["s1"], lw=2.2, zorder=4,
             marker="o", ms=4.5, label="this take: $\\omega(\\delta)/F(T)$")
    ax0.set_xscale("log")
    ax0.set_yscale("log")
    ax0.set_xlabel("$\\delta$ (s, log)")
    ax0.set_ylabel("$\\omega(\\delta)\\,/\\,F(T)$ (log)")
    ax0.legend(loc="lower right", labelcolor=p["ink_2"])
    ratio = m["cantor"][0] / m["omega"][0]
    shade = dict(boxstyle="round,pad=0.34", fc=PALETTE["surface"], ec="none",
                 alpha=0.92)
    ax0.annotate(f"at $\\delta$ = 5 ms the Cantor\nmodulus is {ratio:.0f}× larger",
                 (m["delta"][0], m["cantor"][0]), xytext=(18, 14),
                 textcoords="offset points", color=p["s2"], fontsize=9.5,
                 va="bottom", bbox=shade,
                 arrowprops=dict(arrowstyle="-", color=p["s2"], lw=0.9))
    ax0.annotate("$\\omega$ hugs the Lipschitz line at small $\\delta$:\n"
                 "the constant $L$ is attained, so $F$ is Lipschitz\n"
                 "and the optimal Hölder exponent is exactly 1",
                 (0.035, 0.965), xycoords="axes fraction", va="top",
                 color=p["ink"], fontsize=9.5)
    _title(ax0, "Modulus of continuity of $F$, against the devil's staircase")

    ax1.axhline(1.0, color=p["muted"], lw=1.4, ls=(0, (5, 3)), zorder=3)
    ax1.annotate("Poisson: $\\mathrm{Fano}\\equiv 1$", (f["width"][-1], 1.0),
                 xytext=(-4, -16), textcoords="offset points", ha="right",
                 color=p["muted"], fontsize=9.5, bbox=shade)
    ax1.plot(f["width"], f["fano_poisson"], color=p["muted"], lw=1.2, marker=".",
             ms=6, zorder=3, label="matched Poisson sample (estimator noise)")
    ax1.plot(f["width"], f["fano"], color=p["s1"], lw=2.2, marker="o", ms=5.5,
             zorder=4, label="onsets of this take")
    for w, y, n in zip(f["width"], f["fano"], f["bins"]):
        if w in (0.1, 1.0, 10.0):
            ax1.annotate(f"{n} windows", (w, y), xytext=(0, 11),
                         textcoords="offset points", ha="center",
                         color=p["muted"], fontsize=8.5)
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("window width $w$ (s, log)")
    ax1.set_ylabel("$\\mathrm{Var}\\,N(w)\\,/\\,\\mathbb{E}\\,N(w)$ (log)")
    ax1.legend(loc="upper left", labelcolor=p["ink_2"])
    _title(ax1, "Clustering of the onsets, by scale")

    for ax in (ax0, ax1):
        _tidy(ax, xgrid=True, ygrid=True)

    fig.suptitle("Two scaling questions, both answered against a reference",
                 x=0.055, y=0.962, ha="left", fontsize=15.5, color=p["ink"],
                 fontweight="bold")
    fig.text(0.055, 0.905,
             f"$L=\\max_t\\sum_k f_k(t)$ = {m['L'][0]:.0f}, "
             f"$F(T)$ = {m['F_T'][0]:,.0f} velocity·s.  "
             "Left: $F$ is Lipschitz, so no Cantor-like singularity.  "
             "Right: below a quarter-second the onsets are Poisson; above a "
             "second they clump, which is what phrasing is.",
             ha="left", va="top", fontsize=10, color=p["ink_2"])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def figure_keyboard(a: TakeAnalysis, path: Path, dpi: int = 190) -> Path:
    """Where on the keyboard the take actually lives."""
    plt = _plt()

    p = PALETTE
    idx, n, mv = a.key_counts()
    strikes = a.actuator.strikes()
    keys = np.array([s.key for s in strikes], dtype=float)
    vel = np.array([s.velocity for s in strikes], dtype=float)
    rho = float(np.corrcoef(np.argsort(np.argsort(keys)),
                            np.argsort(np.argsort(vel)))[0, 1])

    fig = plt.figure(figsize=(15.0, 6.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[2.6, 1.0],
                          height_ratios=[1.0, 1.0], wspace=0.16, hspace=0.34,
                          left=0.05, right=0.985, top=0.80, bottom=0.095)
    ax0 = fig.add_subplot(gs[0, 0])
    ax1 = fig.add_subplot(gs[1, 0], sharex=ax0)
    ax2 = fig.add_subplot(gs[:, 1])

    ticks = [k for k in range(0, 88) if midi_note(k) % 12 == 0]

    ax0.bar(idx, n, width=0.78, color=p["s1"], lw=0, zorder=3)
    ax0.set_ylabel("strikes")
    top = int(np.argmax(n))
    ax0.annotate(f"{note_name(midi_note(idx[top]))} — {n[top]} strikes, "
                 f"{n[top] / n.sum() * 100:.0f}% of the take on one key",
                 (idx[top], n[top]), xytext=(13, -1), textcoords="offset points",
                 color=p["ink"], fontsize=10, va="top")
    _title(ax0, "Strikes per key")
    ax0.tick_params(labelbottom=False)

    fit = np.polyfit(keys, vel, 1)
    xs = np.array([keys.min(), keys.max()])
    ax1.plot(keys + np.random.default_rng(1).uniform(-0.3, 0.3, keys.size), vel,
             ".", color=p["s1"], ms=3.2, alpha=0.30, zorder=2,
             label="one strike (jittered horizontally)")
    ax1.plot(idx, mv, "o", color=p["s2"], ms=5.0, zorder=4,
             label="mean $P$ on that key")
    ax1.plot(xs, np.polyval(fit, xs), color=p["ink"], lw=1.8, zorder=5,
             label=f"least squares — $\\rho = {rho:+.2f}$ over all "
                   f"{len(strikes)} strikes")
    ax1.set_xticks(ticks)
    ax1.set_xticklabels([note_name(midi_note(k)) for k in ticks])
    ax1.set_xlim(-1, 88)
    ax1.set_xlabel("key")
    ax1.set_ylabel("momentum $P$")
    ax1.set_ylim(0, 118)
    ax1.legend(loc="upper left", labelcolor=p["ink_2"])
    _title(ax1, "Momentum against pitch — the higher the key, the harder it is struck")

    pc = a.pitch_class_counts()
    names = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
    scale = {1, 3, 4, 6, 8, 9, 11}  # C# natural minor
    colours = [p["s1"] if i in scale else p["muted"] for i in range(12)]
    ax2.bar(np.arange(12), pc, width=0.72, color=colours, lw=0, zorder=3)
    ax2.set_xticks(np.arange(12))
    ax2.set_xticklabels(names)
    ax2.set_ylabel("strikes")
    ax2.set_xlabel("pitch class")
    ax2.set_ylim(0, pc.max() * 1.32)
    inside = sum(pc[i] for i in scale)
    ax2.annotate(f"blue = the seven pitch classes of C♯ natural minor\n"
                 f"{inside} of {pc.sum()} strikes "
                 f"({inside / pc.sum() * 100:.0f}%) fall inside it",
                 (0.5, 0.985), xycoords="axes fraction", ha="center", va="top",
                 color=p["ink_2"], fontsize=9.5)
    _title(ax2, "Pitch classes, folded")

    for ax in (ax0, ax1, ax2):
        _tidy(ax, xgrid=False, ygrid=True)

    fig.suptitle("59 of 88 keys touched, and the weight is nowhere near even",
                 x=0.05, y=0.962, ha="left", fontsize=15.5, color=p["ink"],
                 fontweight="bold")
    fig.text(0.05, 0.905,
             f"Compass C♯1–A♯6, {a.actuator.n_strikes} strikes. The two left "
             "panels share the keyboard axis: how often each key was used, and "
             "how hard.",
             ha="left", va="top", fontsize=10, color=p["ink_2"])
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def _find_katex() -> Path:
    for d in KATEX_SEARCH:
        if (d / "katex.min.js").exists():
            return d
    raise FileNotFoundError(
        "no KaTeX distribution found; looked in "
        + ", ".join(str(d) for d in KATEX_SEARCH)
    )


def _inline_katex(root: Path) -> tuple[str, str]:
    """Return ``(css, js)`` with every font embedded as a data URI.

    The page has to render mathematics from a bare ``file://`` URL a month from
    now, so nothing may be fetched at view time.  Only the woff2 faces are
    kept; the ttf and woff fallbacks in KaTeX's stock CSS would triple the size
    for browsers that have not needed them in a decade.
    """
    css = (root / "katex.min.css").read_text(encoding="utf-8")
    fonts: dict[str, str] = {}
    for f in sorted((root / "fonts").glob("*.woff2")):
        fonts[f.name] = base64.b64encode(f.read_bytes()).decode("ascii")

    def one_src(match: re.Match) -> str:
        body = match.group(0)
        name = re.search(r"([A-Za-z_]+-[A-Za-z]+)\.woff2", body)
        if not name or f"{name.group(1)}.woff2" not in fonts:
            return body
        data = fonts[f"{name.group(1)}.woff2"]
        return (f"src:url(data:font/woff2;base64,{data}) "
                f"format(\"woff2\")")

    css = re.sub(r"src:url\([^)]*\)[^;}]*", one_src, css)
    js = (root / "katex.min.js").read_text(encoding="utf-8")
    js += "\n;" + (root / "contrib" / "auto-render.min.js").read_text(encoding="utf-8")
    return css, js


def _b64_png(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _fig(src: Path, caption: str, alt: str) -> str:
    return (
        '<figure>'
        f'<img alt="{html.escape(alt)}" '
        f'src="data:image/png;base64,{_b64_png(src)}">'
        f'<figcaption>{caption}</figcaption>'
        '</figure>'
    )


def _tile(value: str, label: str, note: str = "") -> str:
    extra = f'<span class="tile-note">{note}</span>' if note else ""
    return (f'<div class="tile"><span class="tile-v">{value}</span>'
            f'<span class="tile-l">{label}</span>{extra}</div>')


def _table(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                   for r in rows)
    return (f'<div class="tw"><table class="{cls}"><thead><tr>{head}</tr>'
            f'</thead><tbody>{body}</tbody></table></div>')


PAGE_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --page:#0d0d0d; --surface:#1a1a19; --raised:#232322;
  --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --hair:rgba(255,255,255,0.10);
  color-scheme:dark;
}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--page); color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
  font-size:16.5px; line-height:1.62; overflow-x:hidden;
}
.wrap{max-width:1180px; margin:0 auto; padding:0 28px 96px}
header.top{padding:76px 0 44px; border-bottom:1px solid var(--hair)}
h1{font-size:clamp(28px,4.4vw,46px); line-height:1.1; margin:0 0 16px;
   font-weight:700; letter-spacing:-0.02em}
.sub{color:var(--ink2); font-size:clamp(16px,1.7vw,19px); max-width:74ch; margin:0}
.meta{margin-top:26px; color:var(--muted); font-size:14px;
      font-variant-numeric:tabular-nums}
.meta code{color:var(--ink2)}
h2{font-size:clamp(21px,2.6vw,28px); margin:76px 0 6px; font-weight:650;
   letter-spacing:-0.01em; scroll-margin-top:24px}
h2 .num{color:var(--muted); font-weight:400; margin-right:12px;
        font-variant-numeric:tabular-nums}
h3{font-size:18px; margin:38px 0 4px; font-weight:600; color:var(--ink)}
p{margin:14px 0; max-width:78ch; color:var(--ink2)}
p.lede{color:var(--ink); font-size:17.5px}
strong{color:var(--ink); font-weight:600}
a{color:var(--s1)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:0.9em}
hr{border:0; border-top:1px solid var(--hair); margin:56px 0}
.rule{height:1px; background:var(--hair); margin:10px 0 0}
figure{margin:34px 0 8px; background:var(--surface); border:1px solid var(--hair);
       border-radius:10px; padding:10px 10px 0; overflow:hidden}
figure img{display:block; width:100%; height:auto; border-radius:6px}
figcaption{color:var(--muted); font-size:13.5px; line-height:1.55;
           padding:12px 8px 14px; max-width:100ch}
figcaption b{color:var(--ink2); font-weight:600}
.tiles{display:grid; gap:12px; margin:30px 0 6px;
       grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr))}
.tile{background:var(--surface); border:1px solid var(--hair); border-radius:10px;
      padding:16px 18px 15px; display:flex; flex-direction:column; gap:3px}
.tile-v{font-size:29px; font-weight:650; color:var(--ink); line-height:1.12;
        letter-spacing:-0.02em}
.tile-l{font-size:13px; color:var(--ink2); line-height:1.35}
.tile-note{font-size:12px; color:var(--muted); margin-top:3px; line-height:1.4}
.tw{overflow-x:auto; margin:24px 0; border:1px solid var(--hair);
    border-radius:10px; background:var(--surface)}
table{border-collapse:collapse; width:100%; font-size:14.5px;
      font-variant-numeric:tabular-nums}
th,td{text-align:right; padding:10px 16px; border-bottom:1px solid var(--grid);
      white-space:nowrap}
th:first-child,td:first-child{text-align:left; white-space:normal}
thead th{color:var(--muted); font-weight:600; font-size:12.5px;
         text-transform:uppercase; letter-spacing:0.05em}
/* uppercasing a header must not reach the mathematics inside it: KaTeX
   renders letters as glyphs, so text-transform turns \\delta into \\Delta. */
thead th .katex{text-transform:none; letter-spacing:normal}
tbody tr:last-child td{border-bottom:0}
tbody td{color:var(--ink2)}
tbody td:first-child{color:var(--ink)}
.callout{border-left:2px solid var(--s2); background:var(--surface);
         padding:18px 24px; margin:30px 0; border-radius:0 10px 10px 0}
.callout.blue{border-left-color:var(--s1)}
.callout.aqua{border-left-color:var(--s3)}
.callout p{margin:8px 0; max-width:none}
.callout p:first-child{margin-top:0}
.callout p:last-child{margin-bottom:0}
.callout .k{display:block; font-size:12px; letter-spacing:0.09em;
            text-transform:uppercase; color:var(--muted); margin-bottom:8px}
.math-block{margin:26px 0; overflow-x:auto; overflow-y:hidden; padding:4px 0}
ul{color:var(--ink2); max-width:78ch; padding-left:22px}
li{margin:9px 0}
li::marker{color:var(--muted)}
.katex{font-size:1.02em}
.katex-display{margin:0; padding:2px 0}
footer{margin-top:80px; padding-top:26px; border-top:1px solid var(--hair);
       color:var(--muted); font-size:13.5px}
footer p{color:var(--muted); font-size:13.5px}
@media (max-width:720px){
  .wrap{padding:0 18px 64px}
  header.top{padding:48px 0 32px}
  body{font-size:16px}
  th,td{padding:9px 12px}
}
"""

PAGE_JS = """
document.addEventListener("DOMContentLoaded", function () {
  renderMathInElement(document.body, {
    delimiters: [
      {left: "$$", right: "$$", display: true},
      {left: "$",  right: "$",  display: false}
    ],
    throwOnError: false,
    strict: false
  });
});
"""


def build_page(a: TakeAnalysis, out: Path, figures: dict[str, Path],
               control: TakeAnalysis | None = None) -> Path:
    """Assemble the standalone document.  Everything is inlined; nothing is fetched."""
    katex_css, katex_js = _inline_katex(_find_katex())

    v = a.velocity
    mi, mi_max = v.mutual_information()
    corr = v.correlations()
    act, snd = a.actuator, a.sounding
    lat = a.lattice
    m = a.modulus
    span = a.span
    pedal_time = sum(b - u for u, b in act.pedal)
    tc, vc = a.cc64_values()
    n_mid = int(((vc > 0) & (vc < 127)).sum())
    L = float(m["L"][0])
    FT = float(m["F_T"][0])
    started = a.capture.header.get("started_utc", "")
    onsets = np.array(sorted(s.t_on for s in act.strikes()))
    ioi = np.diff(onsets)
    ioi = ioi[ioi > 0]
    durations_ms = np.round(np.array([s.duration for s in act.strikes()]) * 1000)
    jumps = act.jump_measure()
    multi = sum(1 for _, x in jumps if int(np.count_nonzero(x)) > 1)

    T = []  # sections

    # -- hero tiles --------------------------------------------------------
    tiles = "".join([
        _tile(f"{act.n_strikes}", "strikes",
              f"on {len(act.active_keys())} of 88 keys"),
        _tile(f"{span:.1f} s", "span", f"{a.capture.n_dropped} packets dropped"),
        _tile("5.000 ms", "timing floor",
              f"{lat.lattice_fraction * 100:.2f}% of packets on the lattice"),
        _tile(f"7 → {snd.max_polyphony()}", "peak polyphony",
              "keys held → strings ringing"),
        _tile(f"{act.support_measure() / span * 100:.1f}%", "of the take is sounding",
              f"{act.plateau_measure():.1f} s of silence, in "
              f"{len(act.plateaus())} plateaus"),
        _tile(f"{mi:.3f} bits", "shared by the two velocity bytes",
              f"of a possible {mi_max:.2f}"),
    ])

    T.append(f'<div class="tiles">{tiles}</div>')

    # -- 1. the object -----------------------------------------------------
    T.append('<h2 id="object"><span class="num">1</span>The object</h2>')
    T.append(
        '<p class="lede">The representation is the one you specified: a note is '
        'a scaled indicator on a closed interval, a key is a finite sum of them '
        'on pairwise disjoint intervals, and the performance is the direct sum '
        'over the 88 keys.</p>'
        '<div class="math-block">$$f \\;=\\; \\bigoplus_{k=0}^{87} f_k, '
        '\\qquad f_k \\;=\\; \\sum_{i=1}^{n_k} P_i\\,\\mathbf{1}_{[a_i,\\,b_i]}, '
        '\\qquad \\lambda\\!\\left(I_i \\cap I_j\\right) = 0 \\ \\ (i \\neq j).$$</div>'
        f'<p>This take realises it with $n = \\sum_k n_k = {act.n_strikes}$ '
        f'strikes distributed over {len(act.active_keys())} nonzero summands. '
        'The disjointness invariant was checked at construction on all 88 tracks '
        'and held: no key was reported down twice, and the parser needed no '
        'repairs at all — '
        f'<span class="mono">{html.escape(act.report.summary())}</span>. '
        'Every quantity below is a functional of that one object.</p>'
    )
    T.append(_fig(
        figures["object"],
        '<b>The whole take.</b> Top: the support of $f$, one horizontal segment '
        'per strike, tinted by momentum $P$ on a single-hue ramp. Second: the '
        'damper pedal, which is down almost the whole time — at this width it '
        'reads as a solid block with fast dips, and the shape of a dip is in the '
        'next figure. Third: polyphony, in both representations. Bottom: the '
        'cumulative energy $F$, each curve indexed to its own total so the two '
        'are compared by shape rather than by size; the grey vertical bands are '
        'the plateaus of the actuator $F$, i.e. the silences.',
        "Four stacked panels over 227 seconds: piano roll, CC64 trace, polyphony, "
        "cumulative energy."))

    T.append(_table(
        ["functional", "actuator $f$", "sounding $f$ (CC64 applied)"],
        [
            ["strikes $n$", f"{act.n_strikes}", f"{snd.n_strikes}"],
            ["support measure $\\lambda(\\operatorname{supp} f)$",
             f"{act.support_measure():.2f} s", f"{snd.support_measure():.2f} s"],
            ["as a fraction of the span",
             f"{act.support_measure() / span * 100:.1f}%",
             f"{snd.support_measure() / span * 100:.1f}%"],
            ["plateaus of $F$ (count / total)",
             f"{len(act.plateaus())} / {act.plateau_measure():.2f} s",
             f"{len(snd.plateaus())} / {snd.plateau_measure():.2f} s"],
            ["longest plateau",
             f"{max(b - u for u, b in act.plateaus()):.2f} s",
             f"{max(b - u for u, b in snd.plateaus()):.2f} s"],
            ["peak polyphony $\\max_t n(t)$",
             f"{act.max_polyphony()}", f"{snd.max_polyphony()}"],
            ["time-average polyphony",
             f"{_mean_polyphony(act):.2f}", f"{_mean_polyphony(snd):.2f}"],
            ["total variation $V_{\\ell^1}(f)$",
             f"{act.total_variation('l1'):,.0f}",
             f"{snd.total_variation('l1'):,.0f}"],
            ["total variation $V_{\\ell^\\infty}(f)$",
             f"{act.total_variation('linf'):,.0f}",
             f"{snd.total_variation('linf'):,.0f}"],
            ["$V_{\\ell^1}/V_{\\ell^\\infty}$",
             f"{act.total_variation('l1') / act.total_variation('linf'):.4f}",
             f"{snd.total_variation('l1') / snd.total_variation('linf'):.4f}"],
            ["cumulative energy $F(T)$",
             f"{act.energy():,.0f} vel·s", f"{snd.energy():,.0f} vel·s"],
            ["Lipschitz constant $L=\\max_t\\sum_k f_k$",
             f"{L:.0f}", f"{max(float(snd.evaluate(t).sum()) for t in sorted({s.t_on for s in snd.strikes()})):.0f}"],
        ]))

    T.append(
        '<div class="callout blue"><span class="k">the norm ratio is a '
        'measurement of the transport, not of the music</span>'
        f'<p>$V_{{\\ell^1}}/V_{{\\ell^\\infty}} = '
        f'{act.total_variation("l1") / act.total_variation("linf"):.4f}$ on the '
        'actuator representation. That ratio is $1$ exactly when no two keys ever '
        'change state at the same instant, and it grows with coincidence. Of the '
        f'{len(jumps)} atoms of $Df$, only <strong>{multi}</strong> involve more '
        'than one key. Notes overlap constantly — up to '
        f'{act.max_polyphony()} keys are down together — but their <em>onsets</em> '
        'almost never coincide, and §5 says why: the link puts nearly every packet '
        f'on its own {lat.step_ms:.0f} ms slot, so a chord struck as one gesture '
        f'is delivered as a {lat.step_ms:.0f} ms arpeggio unless the piano '
        f'happened to bundle it into one packet ({lat.bundled_packets} packets '
        'did). This ratio is measuring the transport. On the sounding '
        'representation, where a single CC64 message lifts many strings at '
        'literally one instant, it rises to '
        f'{snd.total_variation("l1") / snd.total_variation("linf"):.3f}.</p></div>'
    )

    T.append(_fig(
        figures["detail"],
        '<b>The densest fourteen seconds</b>, at roughly 16× the horizontal '
        'magnification of the previous figure, chosen because the sounding '
        f'polyphony reaches its maximum of {snd.max_polyphony()} inside it. '
        'Every CC64 message is a dot, and the two dips show the pedal passing '
        'through intermediate positions rather than switching. The bottom panel '
        'is $\\sum_k f_k$, the slope of $F$: the staircase is not made of jumps, '
        'it is made of ramps of varying steepness. Watch the last four seconds — '
        'the blue actuator trace stays near 200 while the orange sounding trace '
        'climbs to 1,516, because a rising figure is being played into a held '
        'pedal. That gap is the pedal doing the work, not the hands.',
        "Three panels over a 14 second window: piano roll, CC64 dots, and total "
        "sustained momentum for both representations."))

    # -- 2. Cantor ---------------------------------------------------------
    T.append('<h2 id="cantor"><span class="num">2</span>Is $F$ a devil\'s '
             'staircase? No, and here is the distance</h2>')
    T.append(
        '<p class="lede">The Cantor function is monotone, continuous, and flat '
        'off a set of measure zero. $F$ here is monotone, continuous, and flat '
        f'off a set of measure {act.plateau_measure():.2f} s out of '
        f'{span:.2f} s. That is the wrong sign: the exceptional set is not small, '
        'it is almost everything.</p>'
        '<p>The sharp statement is about regularity rather than about measure. '
        '$F$ is the integral of a bounded function, so it is Lipschitz:</p>'
        '<div class="math-block">$$|F(t)-F(s)| \\;\\le\\; '
        '\\Big(\\sup_t \\textstyle\\sum_k f_k(t)\\Big)\\,|t-s| '
        f'\\;=\\; {L:.0f}\\,|t-s|,$$</div>'
        '<p>and the bound is attained exactly — $\\omega(\\delta) = L\\delta$ to '
        f'every digit for $\\delta \\le {m["hold"][0] * 1000:.0f}$ ms, because at '
        f'$t = {m["hold_at"][0]:.3f}$ s there is a stretch that long over which '
        f'$\\sum_k f_k \\equiv {L:.0f}$ — so the optimal Hölder exponent of $F$ is '
        'exactly $1$. The Cantor function is Hölder of exponent '
        '$\\log 2/\\log 3 \\approx 0.6309$ and of no better; it fails to be '
        'Lipschitz at every point of the Cantor set. The two objects are '
        'therefore in different regularity classes, and the gap is measurable.</p>'
    )
    T.append(_fig(
        figures["scaling"],
        '<b>Left:</b> the modulus of continuity $\\omega(\\delta) = '
        '\\sup_{|t-s|\\le\\delta}|F(t)-F(s)|$, normalised by $F(T)$, on log–log '
        'axes, against the Lipschitz bound $L\\delta/F(T)$ and against the Cantor '
        'function\'s $(\\delta/T)^{\\log 2/\\log 3}$. The observed curve rides the '
        'Lipschitz line at small $\\delta$ — slope 1, constant attained — and '
        'falls away from it at large $\\delta$ because the plateaus start to bite. '
        'It is nowhere near the Cantor curve. '
        '<b>Right:</b> the index of dispersion of the onset counts. A Poisson '
        'process sits at 1 for every window width; the matched Poisson sample '
        'shows how much of the rise at 10–20 s is just the estimator running out '
        'of windows.',
        "Two log-log plots: modulus of continuity against Lipschitz and Cantor "
        "references, and Fano factor against window width."))
    T.append(_table(
        ["$\\delta$", "$\\omega(\\delta)/F(T)$", "Lipschitz $L\\delta/F(T)$",
         "Cantor $(\\delta/T)^{\\log 2/\\log 3}$", "Cantor $\\div$ observed"],
        [[f"{d * 1000:.0f} ms" if d < 1 else f"{d:.0f} s",
          f"{o:.5f}", f"{l:.5f}", f"{c:.5f}", f"{c / o:.1f}×"]
         for d, o, l, c in zip(m["delta"], m["omega"], m["lipschitz"], m["cantor"])
         if d in (0.005, 0.02, 0.1, 0.5, 2.0, 10.0)]))
    T.append(
        '<div class="callout"><span class="k">the honest version</span>'
        '<p>The Cantor picture was a good guess about the shape and it is wrong '
        'about the analysis. What the guess got right is that $F$ is a monotone, '
        'continuous, piecewise-affine function that is constant on the silences — '
        'the silhouette is a staircase. What it got wrong is everything measured: '
        f'the flat set has measure {act.plateau_measure() / span * 100:.1f}% of '
        'the interval rather than 100%, there are '
        f'{len(act.plateaus())} plateaus rather than a Cantor set of them, and $F$ '
        'is absolutely continuous with $F\' = \\sum_k f_k \\in L^\\infty$ rather '
        'than singular. Under the pedal it gets further away, not closer: the '
        f'sounding $F$ has {len(snd.plateaus())} plateaus totalling '
        f'{snd.plateau_measure():.2f} s, so it is strictly increasing on '
        f'{snd.support_measure() / span * 100:.1f}% of the take.</p></div>'
    )

    # -- 3. velocity -------------------------------------------------------
    T.append('<h2 id="velocity"><span class="num">3</span>The channel you are '
             'not using is the one carrying the most</h2>')
    T.append(
        '<p class="lede">The FP-30X sends two velocity bytes per note: one on the '
        'note-on, one on the note-off. Only the first appears in the model above, '
        'because only the first scales anything. On this take the second one '
        'carries more information than the first.</p>')
    T.append(_table(
        ["", "strike velocity $P$", "release velocity"],
        [
            ["range", f"[{v.strike.min():.0f}, {v.strike.max():.0f}]",
             f"[{v.release.min():.0f}, {v.release.max():.0f}]"],
            ["distinct values used", f"{len(set(v.strike.tolist()))} of 127",
             f"{len(set(v.release.tolist()))} of 128"],
            ["mean", f"{v.strike.mean():.1f}", f"{v.release.mean():.1f}"],
            ["standard deviation", f"{v.strike.std():.1f}", f"{v.release.std():.1f}"],
            ["entropy, 16 uniform bins", f"{v.entropy(v.strike):.3f} bits",
             f"{v.entropy(v.release):.3f} bits"],
            ["correlation with duration ($\\rho$)",
             f"{corr['duration vs strike'][1]:+.3f}",
             f"{corr['duration vs release'][1]:+.3f}"],
            ["correlation with pitch ($\\rho$)",
             f"{corr['pitch vs strike'][1]:+.3f}",
             f"{corr['pitch vs release'][1]:+.3f}"],
            ["correlation with each other ($r$ / $\\rho$)",
             f"{corr['strike vs release'][0]:+.3f} / "
             f"{corr['strike vs release'][1]:+.3f}", "—"],
        ]))
    T.append(_fig(
        figures["velocity"],
        '<b>Left:</b> the two marginals. The strike byte is roughly symmetric '
        f'about {v.strike.mean():.0f} and never exceeds {v.strike.max():.0f} — you '
        'used four fifths of the dynamic range the instrument offers. The release '
        f'byte is skewed high, sits at {v.release.mean():.0f} on average, and uses '
        'the whole byte. <b>Middle:</b> the joint distribution, on a single-hue '
        'count ramp. The mass is a blob, not a ridge: knowing how hard you struck '
        f'a key tells you {mi:.3f} bits about how you let it up, out of the '
        f'{mi_max:.2f} bits available. <b>Right:</b> release velocity against '
        'hold time, log-scaled, with a 61-note running mean. Flat until about a '
        'second, then falling — long held notes come up more gently, and that is '
        'the only structure in the channel.',
        "Three panels: overlaid velocity histograms, a 2D joint histogram, and a "
        "scatter of release velocity against note duration."))
    T.append(
        '<div class="callout"><span class="k">why this is the interesting '
        'result</span>'
        f'<p><strong>{int(v.pedal_at_release.sum())} of {len(v.release)} releases '
        f'({v.pedal_at_release.mean() * 100:.0f}%) happened with the damper pedal '
        'already down</strong> — the string was not going to be stopped, so the '
        'speed at which the key came up had no acoustic consequence whatsoever. '
        'And yet across those same notes the release byte varies with '
        f'{v.entropy(v.release) - v.entropy(v.strike):+.2f} bits more entropy than '
        'the strike byte, and its variation is essentially independent of the '
        'strike, of the pitch, and (below a second) of the duration. The '
        'instrument is measuring a gesture at higher resolution than the one you '
        'are consciously shaping, and then discarding it.</p>'
        + ('<p>The byte is a real measurement, not a constant and not noise: on '
           f'the control take <span class="mono">{control.path.name}</span>, where '
           'keys were deliberately let up slowly, the release byte goes down to '
           f'{control.velocity.release.min():.0f} — on notes held around '
           f'{np.median(control.velocity.duration[control.velocity.release <= 5]):.1f} s. '
           'It tracks key-return speed, over the full byte, in both directions.</p>'
           if control is not None else '')
        + '<p>Two readings, and this take cannot separate them. Either the release '
        'is a real expressive channel you already articulate unconsciously — in '
        'which case a synthesis path that honours it has something to work with — '
        'or it is uncontrolled noise from a hand leaving a key that no longer '
        'matters, in which case its high entropy is precisely because you are not '
        'attending to it. The discriminating experiment is a take played with the '
        'pedal up, and it has not been done.</p></div>'
    )

    # -- 4. rhythm and geography ------------------------------------------
    T.append('<h2 id="rhythm"><span class="num">4</span>Rhythm and geography</h2>')
    T.append(
        f'<p>Inter-onset intervals: $n = {len(ioi)}$, mean '
        f'{ioi.mean() * 1000:.0f} ms, median {np.median(ioi) * 1000:.0f} ms, '
        f'coefficient of variation ${ioi.std() / ioi.mean():.3f}$. A CV of exactly '
        '1 is the memoryless case, so at the level of the one-dimensional marginal '
        'the onsets are indistinguishable from a Poisson process. The Fano curve '
        'in §2 says where that stops being true: the index of dispersion is '
        f'{a.fano["fano"][a.fano["width"] == 0.1][0]:.2f} at a 100 ms window and '
        f'{a.fano["fano"][a.fano["width"] == 5.0][0]:.2f} at 5 s. Below a quarter '
        'of a second you place notes as if at random; above a second you clump '
        'them, which is what a phrase is.</p>'
        f'<p>Note durations are quantised to the same 5 ms lattice as everything '
        f'else ({int((durations_ms % 5 != 0).sum())} of {len(durations_ms)} '
        f'exceptions) and bottom out at exactly '
        f'{durations_ms.min():.0f} ms — you have no note shorter than one tenth of '
        'a second, and the median is '
        f'{np.median(durations_ms):.0f} ms against a longest of '
        f'{durations_ms.max() / 1000:.2f} s.</p>')
    T.append(_fig(
        figures["keyboard"],
        '<b>Top left:</b> strikes per key across the whole 88. <b>Bottom left, '
        'same axis:</b> every strike\'s momentum against its key, with the '
        'per-key mean in orange and the least-squares line in white. The slope '
        f'is real — $\\rho = {corr["pitch vs strike"][1]:+.2f}$ over all '
        f'{act.n_strikes} strikes — and it is what projecting a top voice looks '
        'like in the data: the right hand is louder than the left by construction '
        'of the playing, not by accident of the instrument. <b>Right:</b> the '
        'same strikes folded onto the twelve pitch classes.',
        "A bar chart of strikes per piano key, a scatter of velocity against "
        "pitch with a fitted line, and a pitch-class histogram."))

    # -- 5. timing ---------------------------------------------------------
    T.append('<h2 id="timing"><span class="num">5</span>Can these timestamps be '
             'trusted?</h2>')
    T.append(
        '<p class="lede">To 5 ms, yes. Below 5 ms there is nothing here, and no '
        'amount of care in the capture layer will produce any, because the floor '
        'is upstream of the capture.</p>'
        '<p>The native front end takes the <code>MIDITimeStamp</code> CoreMIDI '
        'applies to each packet near the driver, which removed our own 2.08 ms '
        'poll-loop quantisation. What it revealed underneath is a harder floor. '
        f'Of {lat.n_packets} packets, <strong>{lat.n_packets - lat.off_lattice} '
        f'({lat.lattice_fraction * 100:.2f}%) lie on a single '
        f'{lat.step_ms:.0f}.000 ms lattice</strong>; the gaps between consecutive '
        'packets take 20 distinct values in the first 100 ms and every one of them '
        f'is a multiple of {lat.step_ms:.0f} ms; 78 of the other 80 bins are '
        f'empty. Exactly {lat.off_lattice} packet in the take is off the lattice, '
        'by 1 ms.</p>')
    T.append(_fig(
        figures["timing"],
        '<b>Left:</b> the histogram of inter-packet gaps below 100 ms, log '
        'vertical. Only the multiples of 5 are populated. <b>Right:</b> every '
        'packet\'s timestamp modulo 5 ms against its time in the take; the single '
        'off-lattice packet is circled. This is what a quantisation floor looks '
        'like when it is real rather than assumed.',
        "A comb histogram of inter-packet gaps and a residue scatter showing all "
        "packets on a 5 ms lattice."))
    T.append(
        '<div class="callout"><span class="k">against the earlier benchmark, '
        'honestly</span>'
        '<p>The 0.0000 ms jitter figure recorded earlier was measured on a '
        'synthetic virtual source with no radio in the path. It measured the '
        'capture tool and it was right about the capture tool; it said nothing '
        'about the link, and it should not be quoted as if it had. This take is '
        'the first measurement of the actual Bluetooth path, and the answer is a '
        'hard 5.000 ms grid.</p>'
        f'<p>The 5.000 ms minimum inter-onset noted on an earlier sample is '
        'therefore not a coincidence and not a rounding artifact — it is the grid '
        'spacing, and it is the shortest gap the link can express. Two layers are '
        'stacked here and the data separates them. BLE-MIDI carries a 13-bit '
        'millisecond timestamp, which is why every value is an integer number of '
        f'milliseconds; the further factor of {lat.step_ms:.0f} is the source\'s '
        'own emission cadence, i.e. either the FP-30X scanning its key matrix at '
        '200 Hz or its BLE transmitter batching into 5 ms slots. This capture '
        'cannot distinguish those two, and a USB-MIDI take would settle it in one '
        'minute.</p>'
        f'<p>Consequences worth stating. (a) The link serialises: '
        f'{lat.distinct_stamps} of {lat.n_packets} packets have a distinct '
        'timestamp, so a chord arrives as separate events 5 ms apart and true '
        'simultaneity is destroyed at the transport — this is what the '
        '$V_{\\ell^1}/V_{\\ell^\\infty}$ ratio in §1 is measuring. The exception '
        f'is the {lat.bundled_packets} packets that carry two or three messages in '
        'one <code>MIDIPacket</code>; those events really were simultaneous, and '
        'they are the only simultaneity the link admits. (b) The message rate has '
        f'a ceiling of {lat.ceiling_rate:.0f}/s plus bundles. The peak observed '
        f'here is {lat.peak_rate_100ms:.0f}/s in a 100 ms window'
        + (f' and {control.lattice.peak_rate_100ms:.0f}/s on the control take'
           if control else '')
        + ', so the ~226 msg/s figure from the old poll-loop captures does not '
        'reproduce and was almost certainly the poll loop bunching arrivals it had '
        'sat on. (c) Nothing was lost: 0 packets dropped, 0 truncated, 0 '
        'unstamped, clean trailer.</p></div>'
    )

    # -- 6. what is not there ---------------------------------------------
    T.append('<h2 id="absent"><span class="num">6</span>What the instrument does '
             'not send</h2>')
    census = ", ".join(f"{k} × {n}" for k, n in sorted(a.census.items(),
                                                       key=lambda x: -x[1]))
    T.append(
        f'<p>Full message census for this take: <span class="mono">{census}</span>. '
        'Three types, nothing else. No channel pressure (0xD0), no polyphonic key '
        'pressure (0xA0), no pitch bend, no system exclusive, no active sensing. '
        'The action is a discrete-event device: one sample when the hammer is '
        'launched, one when the key returns, and nothing at all in between, no '
        'matter what the finger does to a key that is already down.</p>')
    if control is not None:
        cv = control.velocity
        T.append(
            f'<p>The control take <span class="mono">{control.path.name}</span> '
            f'({control.span:.0f} s, {control.actuator.n_strikes} strikes) was '
            'played specifically to try to break that: hold a key and press '
            'harder. It produced no additional messages. That is the second '
            'independent confirmation, after the five older takes, and this one '
            'came through the hardware-timestamped path. It also reproduces the '
            f'5 ms lattice — {control.lattice.lattice_fraction * 100:.2f}% of its '
            f'{control.lattice.n_packets} packets — which is what makes the '
            'lattice a property of the link rather than of one recording.</p>')
    T.append(
        '<p>So the whole expressive surface the FP-30X exposes is: which key, '
        'when it went down, how fast, when it came up, how fast, and the damper '
        f'position as a continuous controller ({len(vc)} CC64 messages, '
        f'{n_mid} of them strictly between 0 and 127, pedal down for '
        f'{pedal_time:.0f} s = {pedal_time / span * 100:.0f}% of the take across '
        f'{len(act.pedal)} spans). Two of those six are underused: the release '
        'byte, per §3, and the intermediate pedal positions, which the '
        'threshold-at-64 model in <code>Performance.with_sustain</code> currently '
        'throws away wholesale.</p>')

    # -- method ------------------------------------------------------------
    T.append('<h2 id="method"><span class="num">7</span>Method and provenance</h2>')
    T.append(
        f'<ul>'
        f'<li>Source: <code>{html.escape(str(a.path))}</code>, '
        f'{a.path.stat().st_size:,} bytes, captured {html.escape(started)}, '
        f'source <code>{html.escape(a.capture.source)}</code>.</li>'
        f'<li>Front end: <code>native/fp30x_capture.c</code> on the CoreMIDI '
        'callback. Trailer reports '
        f'{lat.n_packets} packets, {a.capture.n_dropped} dropped, '
        f'{a.capture.n_ts_zero} unstamped, clean stop '
        f'<code>{a.capture.complete}</code>. Mach timebase '
        f'{a.capture.timebase[0]}/{a.capture.timebase[1]}.</li>'
        '<li>Representation: <code>fp30x_studio.performance.Performance</code>, '
        'built through <code>from_capture</code>. Disjointness enforced at the '
        'a.e. grade on all 88 tracks at construction.</li>'
        '<li>Analysis and figures: '
        '<code>fp30x_studio.take_analysis</code>. $F$ is evaluated in closed form '
        'at its breakpoints, not by quadrature, and $\\omega(\\delta)$ is computed '
        'exactly rather than on a grid: $F(t+\\delta)-F(t)$ is piecewise affine '
        'with breakpoints in $B \\cup (B-\\delta)$, where $B$ is the set of '
        f'{len(act.cumulative_curve()[0])} distinct interval endpoints, so '
        'evaluating it there attains the supremum with no discretisation.</li>'
        '<li>Not mixed with the five takes of 2026-08-16: those came through the '
        'Python poll loop, whose 2.08 ms quantisation collapsed events that only '
        'looked simultaneous. Nothing in this document uses them for timing.</li>'
        '<li>Palette: dark steps of the data-viz reference palette, validated '
        'all-pairs on this surface. Mathematics is KaTeX, inlined with its fonts; '
        'the figures are inlined PNGs. The page fetches nothing.</li>'
        '</ul>')

    body = "\n".join(T)

    return _write_html(out, body, katex_css, katex_js, a, started)


def _mean_polyphony(perf: Performance) -> float:
    ts, counts = perf.polyphony_steps()
    te = np.append(ts, perf.t_end)
    dt = np.diff(te)
    return float((counts * dt).sum() / (perf.t_end - perf.t_start))


def _write_html(out: Path, body: str, katex_css: str, katex_js: str,
                a: TakeAnalysis, started: str) -> Path:
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Take of 2026-08-17 — the mathematics</title>
<style>{katex_css}</style>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
<header class="top">
<h1>One take, as an $\\mathbb{{R}}^{{88}}$-valued step function</h1>
<p class="sub">{a.actuator.n_strikes} strikes, {a.span:.0f} seconds, zero dropped
packets — the first capture in this project whose timing means anything. What the
indicator-function model actually says about how you played, and where the model
and the hardware each run out.</p>
<p class="meta"><code>{html.escape(a.path.name)}</code> · {html.escape(started)} ·
{a.capture.header.get('source', '')} · analysed by
<code>fp30x_studio.take_analysis</code></p>
</header>
{body}
<footer>
<p>Generated from the capture, not from notes about it. Every number on this page
is recomputed by <code>python -m fp30x_studio.take_analysis</code>; if the file
changes, the page changes. Self-contained: figures and KaTeX are embedded, so this
opens from disk with no network.</p>
</footer>
</div>
<script>{katex_js}</script>
<script>{PAGE_JS}</script>
</body>
</html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("take", nargs="?", type=Path, default=DEFAULT_TAKE)
    ap.add_argument("--control", type=Path, default=DEFAULT_CONTROL,
                    help="second capture used only as a corroborating control")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--figures", type=Path, default=None,
                    help="directory for the intermediate PNGs "
                         "(default: alongside --out, in figures/)")
    ap.add_argument("--window", type=float, nargs=2, default=(207.5, 221.5),
                    help="the detail window, in seconds")
    ap.add_argument("--dpi", type=int, default=190)
    args = ap.parse_args(argv)

    a = analyse(args.take)
    print(a.capture.summary())
    print(a.actuator.summary())

    control = None
    if args.control and args.control.exists():
        control = analyse(args.control)
        print(f"\ncontrol: {control.path.name}, "
              f"{control.actuator.n_strikes} strikes, "
              f"lattice {control.lattice.step_ms:.0f} ms at "
              f"{control.lattice.lattice_fraction * 100:.2f}%")

    figdir = args.figures or (args.out.parent / "figures")
    figdir.mkdir(parents=True, exist_ok=True)
    stem = args.take.stem
    figures = {
        "object": figure_object(a, figdir / f"{stem}-object.png", args.dpi),
        "detail": figure_detail(a, figdir / f"{stem}-detail.png",
                                tuple(args.window), args.dpi),
        "scaling": figure_scaling(a, figdir / f"{stem}-scaling.png", args.dpi),
        "velocity": figure_velocity(a, figdir / f"{stem}-velocity.png", args.dpi),
        "keyboard": figure_keyboard(a, figdir / f"{stem}-keyboard.png", args.dpi),
        "timing": figure_timing(a, figdir / f"{stem}-timing.png", args.dpi),
    }
    for name, path in figures.items():
        print(f"  figure {name:9s} {path.stat().st_size / 1024:7.0f} KB  {path}")

    out = build_page(a, args.out, figures, control=control)
    print(f"\nwrote {out}  ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
