"""Build ``docs/warp-op55.html``: the alignment, written up.

Self-contained by construction. KaTeX is inlined with its woff2 faces, every
chart is inline SVG generated here, and there is no script, style or image
fetched at view time -- the page opens from a bare ``file://`` URL with no
server and no network, a month from now.

Run it with::

    python -m fp30x_studio.align
"""

from __future__ import annotations

import html
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .charts import Axes, fmt
from .events import LATTICE_S
from .report import Alignment, align_segments
from .events import load_segments

__all__ = ["build", "render"]

NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G",
              "G♯", "A", "A♯", "B"]

#: MIDI note at or below which an onset counts as a left-hand bass attack. In
#: this nocturne's accompaniment the bass sounds on beats 1 and 3, so these
#: mark the half-bar; everything above is chord or melody.
BASS_CEILING = 48

TAKE = Path.home() / "Music" / "FP-30X Studio" / "takes" / "2026-08-19-a.fp30"


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------

CSS_VARS = """
:root{
  color-scheme: light;
  --plane:#f9f9f7; --surface:#fcfcfb;
  --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --rule:rgba(11,11,11,0.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#4a3aa7;
  --ord1:#86b6ef; --ord2:#5598e7; --ord3:#2a78d6; --ord4:#1c5cab; --ord5:#104281;
  --code:#f0efec;
}
@media (prefers-color-scheme: dark){
  :root:where(:not([data-theme="light"])){
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --rule:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#9085e9;
    --ord1:#cde2fb; --ord2:#9ec5f4; --ord3:#6da7ec; --ord4:#3987e5; --ord5:#1c5cab;
    --code:#232321;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --plane:#0d0d0d; --surface:#1a1a19;
  --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --rule:rgba(255,255,255,0.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#9085e9;
  --ord1:#cde2fb; --ord2:#9ec5f4; --ord3:#6da7ec; --ord4:#3987e5; --ord5:#1c5cab;
  --code:#232321;
}
"""

PAGE_CSS = """
*{box-sizing:border-box}
body{margin:0; background:var(--plane); color:var(--ink);
     font:16px/1.62 system-ui,-apple-system,"Segoe UI",sans-serif;
     -webkit-text-size-adjust:100%}
main{max-width:62rem; margin:0 auto; padding:2.6rem 1.35rem 6rem}
h1{font-size:2.05rem; line-height:1.18; letter-spacing:-.02em; margin:0 0 .35rem}
h2{font-size:1.28rem; letter-spacing:-.01em; margin:3.2rem 0 .5rem;
   padding-top:1.3rem; border-top:1px solid var(--rule)}
h3{font-size:1.02rem; margin:2rem 0 .4rem; color:var(--ink)}
p{margin:.75rem 0; color:var(--ink2); max-width:44rem}
p.lede{font-size:1.09rem; color:var(--ink); max-width:44rem}
strong{color:var(--ink); font-weight:600}
a{color:var(--s1)}
code,kbd{font:0.86em/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
     background:var(--code); padding:.12em .38em; border-radius:4px}
pre{background:var(--code); padding:.85rem 1rem; border-radius:8px;
    overflow-x:auto; margin:.9rem 0}
pre code{background:none; padding:0}
.sub{color:var(--muted); font-size:.9rem; margin:.2rem 0 1.6rem}
ul,ol{color:var(--ink2); max-width:44rem; padding-left:1.15rem}
li{margin:.3rem 0}

.tiles{display:grid; gap:.7rem; grid-template-columns:repeat(auto-fit,minmax(9.6rem,1fr));
       margin:1.6rem 0 .4rem}
.tile{background:var(--surface); border:1px solid var(--rule); border-radius:10px;
      padding:.75rem .85rem}
.tile .lab{font-size:.76rem; color:var(--muted); letter-spacing:.02em}
.tile .val{font-size:1.55rem; font-weight:600; line-height:1.15; margin-top:.15rem}
.tile .note{font-size:.76rem; color:var(--muted); margin-top:.1rem}

figure{margin:1.7rem 0 0; background:var(--surface); border:1px solid var(--rule);
       border-radius:12px; padding:1rem 1rem .6rem}
figcaption{color:var(--muted); font-size:.83rem; margin-top:.55rem; line-height:1.5}
figcaption b{color:var(--ink2); font-weight:600}
.chart{display:block; width:100%; height:auto; overflow:visible}
.chart .grid{stroke:var(--grid); stroke-width:1}
.chart .axis{stroke:var(--axis); stroke-width:1}
.chart .ref{stroke:var(--axis); stroke-width:1.5}
.chart .span{fill:var(--s2); opacity:.07}
.chart .tickmark{stroke:var(--muted); stroke-width:1.5; opacity:.55}
.chart text{font:12px system-ui,-apple-system,"Segoe UI",sans-serif; fill:var(--muted)}
.chart .tick{font-variant-numeric:tabular-nums}
.chart .ty{text-anchor:end}
.chart .tx{text-anchor:middle}
.chart .axlabel{fill:var(--ink2); font-size:12.5px; text-anchor:middle}
.chart .annot{fill:var(--ink2); font-size:12px}
.chart .line{fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round}
.chart .stem{stroke-width:1.5; opacity:.55}
.chart .dot{stroke:var(--surface); stroke-width:2}
.chart .mark{stroke:none}
.s1{stroke:var(--s1)} circle.s1,rect.s1{fill:var(--s1)}
.s2{stroke:var(--s2)} circle.s2,rect.s2{fill:var(--s2)}
.s3{stroke:var(--s3)} circle.s3,rect.s3{fill:var(--s3)}
.s4{stroke:var(--s4)} circle.s4,rect.s4{fill:var(--s4)}
.o1{stroke:var(--ord1)} .o2{stroke:var(--ord2)} .o3{stroke:var(--ord3)}
.o4{stroke:var(--ord4)} .o5{stroke:var(--ord5)}
.ghost{stroke:var(--muted); stroke-width:1.5; opacity:.55}

.legend{display:flex; flex-wrap:wrap; gap:.15rem 1.1rem; margin:.15rem 0 .1rem;
        font-size:.82rem; color:var(--ink2)}
.legend span{display:inline-flex; align-items:center; gap:.4rem}
.key{width:15px; height:3px; border-radius:2px; display:inline-block}
.key.dsh{height:0; border-top:2px solid var(--axis)}

table{border-collapse:collapse; width:100%; font-size:.85rem; margin:.7rem 0}
th,td{text-align:right; padding:.32rem .5rem; border-bottom:1px solid var(--rule);
      font-variant-numeric:tabular-nums}
th{color:var(--muted); font-weight:600; font-size:.76rem; letter-spacing:.03em;
   text-transform:uppercase; white-space:nowrap}
th:first-child,td:first-child{text-align:left; font-variant-numeric:normal}
tbody tr:hover{background:var(--code)}
.scroll{overflow-x:auto; -webkit-overflow-scrolling:touch}
details{margin:.7rem 0; border:1px solid var(--rule); border-radius:8px;
        background:var(--surface); padding:.35rem .8rem}
details[open]{padding-bottom:.7rem}
summary{cursor:pointer; color:var(--ink2); font-size:.86rem; padding:.35rem 0}

.callout{background:var(--surface); border:1px solid var(--rule);
         border-left:3px solid var(--s2); border-radius:8px;
         padding:.85rem 1rem; margin:1.2rem 0}
.callout p{margin:.3rem 0}
.callout p:first-child{margin-top:0}
.callout p:last-child{margin-bottom:0}

#tip{position:fixed; pointer-events:none; opacity:0; transition:opacity .1s;
     background:var(--ink); color:var(--plane); font-size:.78rem; line-height:1.45;
     padding:.35rem .55rem; border-radius:6px; max-width:19rem; z-index:9;
     font-variant-numeric:tabular-nums; white-space:pre}
.katex{font-size:1.03em}
.katex-display{margin:1.1rem 0; overflow-x:auto; overflow-y:hidden; padding:2px 0}
@media (max-width:640px){
  main{padding:1.8rem .9rem 4rem} h1{font-size:1.6rem}
}
"""

TIP_JS = """
(function(){
  var tip=document.getElementById('tip');
  document.addEventListener('mouseover',function(e){
    var t=e.target.closest('[data-tip]'); if(!t){return;}
    tip.textContent=t.getAttribute('data-tip'); tip.style.opacity='1';
  });
  document.addEventListener('mousemove',function(e){
    if(tip.style.opacity!=='1'){return;}
    var x=e.clientX+14, y=e.clientY+16, r=tip.getBoundingClientRect();
    if(x+r.width>window.innerWidth-8){x=e.clientX-r.width-14;}
    if(y+r.height>window.innerHeight-8){y=e.clientY-r.height-16;}
    tip.style.left=x+'px'; tip.style.top=y+'px';
  });
  document.addEventListener('mouseout',function(e){
    if(e.target.closest('[data-tip]')){tip.style.opacity='0';}
  });
})();
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    """Clean round tick values spanning ``[lo, hi]``."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** np.floor(np.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10)),
               key=lambda s: abs(s - raw))
    first = np.ceil(lo / step) * step
    out = []
    v = first
    while v <= hi + step * 1e-9:
        out.append(round(v, 10) + 0.0)
        v += step
    return out


def _pad(lo: float, hi: float, frac: float = 0.06) -> tuple[float, float]:
    d = (hi - lo) * frac or 1.0
    return lo - d, hi + d


def _note_rows(take: Path) -> list[dict]:
    """Note intervals straight from the pipeline. Never a hand parser."""
    from ..pipeline import TakeStore

    st = TakeStore(take)
    st.ingest()
    out = []
    for r in st.intervals():
        t0 = st.seconds(r["ns_on"])
        t1 = st.seconds(r["ns_off"]) if r["ns_off"] is not None else t0 + 0.12
        out.append({"t0": t0, "t1": t1, "note": r["note"],
                    "vel": r["velocity_on"]})
    out.sort(key=lambda r: r["t0"])
    return out


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def fig_phi(al: Alignment) -> str:
    w, c = al.warp, al.correspondence
    T1, T2 = w.T1, w.T2
    ax = Axes(0.0, T1, 0.0, T2, height=440.0)
    ax.grid(_ticks(0, T1, 6), _ticks(0, T2, 6),
            xfmt=lambda v: f"{v:.0f}",
            yfmt=lambda v: f"{v:.0f}",
            xlabel="t — seconds into the false start (seg 2), from the first matched onset",
            ylabel="φ(t) — seconds into the complete take (seg 3)")
    # the straight-line null, first, so phi sits on top of it
    ax.path([0.0, T1], [0.0, T2], "ghost")
    ax.path(w.knots, w.values, "s1")
    tips = [f"onset {i + 1} of {len(c.pairs)}\n"
            f"seg 2  t = {p.ta:.3f} s\nseg 3  s = {p.tb:.3f} s\n"
            f"φ(t) − s = {1000 * (float(w(p.ta - c.t[0])) - (p.tb - c.s[0])):+.0f} ms\n"
            + " ".join(note_name(n) for n in sorted(c.a[p.ia].notes))
            for i, p in enumerate(c.pairs)]
    ax.dots(w.data_t, w.data_s, "s1", r=3.6, tips=tips)
    ax.text(T1 * 0.62, T1 * 0.62 * (T2 / T1) - 1.4,
            f"straight line, φ₀(t) = {w.mean_rate:.3f} t", anchor="start", dy=0)
    return ax.render(
        title="The fitted homeomorphism against the straight-line null",
        desc=("phi rises from the origin to (T1, T2), tracking the 47 matched "
              "onsets and staying close to, but visibly bowing away from, the "
              "uniform-tempo straight line."))


def fig_tempo(al: Alignment, bass_t) -> str:
    w = al.warp
    lo, hi = float(np.log(w.slopes.min())), float(np.log(w.slopes.max()))
    lo, hi = _pad(min(lo, -0.05), max(hi, 0.05), 0.14)
    ax = Axes(0.0, w.T1, lo, hi, height=430.0, left=92.0)
    ratios = [0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6]
    yt = [np.log(r) for r in ratios if lo <= np.log(r) <= hi]
    ax.grid(_ticks(0, w.T1, 6), yt,
            xfmt=lambda v: f"{v:.0f}",
            yfmt=lambda v: f"×{np.exp(v):.2f}",
            xlabel="t — seconds into the false start (seg 2)",
            ylabel="φ′(t):  seg-3 seconds per seg-2 second")
    ax.hline(float(np.log(w.mean_rate)), "ref",
             label=f"uniform scaling, ×{w.mean_rate:.3f}", dy=-7)
    ax.step(w.knots, np.log(w.slopes), "s1")
    ax.tick_marks([t for t in bass_t if 0 <= t <= w.T1])
    # direct-label only the two extremes
    k = int(np.argmax(w.slopes))
    j = int(np.argmin(w.slopes))
    for idx, anchor in ((k, "middle"), (j, "middle")):
        xm = 0.5 * (w.knots[idx] + w.knots[idx + 1])
        v = float(np.log(w.slopes[idx]))
        ax.text(min(max(xm, 2.0), w.T1 - 2.0), v,
                f"×{w.slopes[idx]:.2f}", anchor=anchor,
                dy=-9 if idx == k else 16)
    return ax.render(
        title="The tempo curve: log phi-prime",
        desc=("A step function. Above the reference line the false start is "
              "running ahead of the complete take; below it, behind. The short "
              "marks on the axis are left-hand bass attacks."))


def fig_residual(al: Alignment) -> str:
    w, c = al.warp, al.correspondence
    r = 1000.0 * w.residuals
    lo, hi = _pad(float(min(r.min(), -20)), float(max(r.max(), 20)), 0.12)
    ax = Axes(0.0, w.T1, lo, hi, height=340.0)
    ax.grid(_ticks(0, w.T1, 6), _ticks(lo, hi, 6),
            xfmt=lambda v: f"{v:.0f}", yfmt=lambda v: f"{v:+.0f}",
            xlabel="t — seconds into the false start (seg 2)",
            ylabel="φ(t_i) − s_i   (ms)")
    ax.hline(0.0, "axis")
    lat = 1000 * LATTICE_S
    ax.raw(f'<rect class="span" x="{ax.left:.1f}" y="{float(ax.sy(lat)):.1f}" '
           f'width="{ax.width - ax.left - ax.right:.1f}" '
           f'height="{float(ax.sy(-lat)) - float(ax.sy(lat)):.1f}"/>')
    ax.text(w.T1 * 0.5, hi * 0.86,
            f"shaded band: ±{lat:.0f} ms, the transport lattice — the noise floor",
            anchor="middle")
    tips = [f"t = {t:.3f} s\nresidual {v:+.0f} ms\n"
            + " ".join(note_name(n) for n in sorted(c.a[p.ia].notes))
            for t, v, p in zip(w.data_t, r, c.pairs)]
    ax.stems(w.data_t, r, "s1", base=0.0, tips=tips)
    return ax.render(
        title="Residuals after warping",
        desc=("The signed timing error left at each matched onset. It is "
              "roughly twenty times the 5 ms transport lattice, so it is "
              "expressive difference, not measurement noise."))


def fig_sweep(al: Alignment) -> str:
    sw = al.sweep
    x = np.log10([max(p.warp.data_term, 1e-12) for p in sw])
    y = np.log10([max(p.excess_energy, 1e-12) for p in sw])
    ax = Axes(*_pad(float(x.min()), float(x.max())),
              *_pad(float(y.min()), float(y.max())), height=400.0, left=86.0)
    ax.grid(_ticks(x.min(), x.max(), 5), _ticks(y.min(), y.max(), 5),
            xfmt=lambda v: f"10^{v:.0f}" if abs(v) > 1e-9 else "1",
            yfmt=lambda v: f"10^{v:.0f}" if abs(v) > 1e-9 else "1",
            xlabel="data term  Σ (φ(tᵢ) − sᵢ)²   (s²)",
            ylabel="excess energy  ∫ (φ′ − c)²   (s)")
    ax.path(x, y, "s1")
    tips = [f"λ = {p.lam:.4g} s\nresidual RMS {1000 * p.residual_rms:.0f} ms\n"
            f"∫(φ′−c)² = {p.excess_energy:.3f}\ndf = {p.df:.1f}" for p in sw]
    ax.dots(x, y, "s1", r=3.2, tips=tips)
    for idx, cls, lab in ((al.chosen, "s2", f"λ = {al.lam:.2g} s   chosen"),
                          (al.corner, "s3", f"λ = {sw[al.corner].lam:.2g} s   L-corner")):
        ax.dots([x[idx]], [y[idx]], cls, r=6.0, tips=[tips[idx]])
        ax.text(x[idx], y[idx], lab, anchor="start", dy=-11)
    ax.text(x[0], y[0], "λ → 0:  interpolation", anchor="start", dy=16)
    ax.text(x[-1], y[-1], "λ → ∞:  metronome", anchor="end", dy=-11)
    return ax.render(
        title="The L-curve of the lambda sweep",
        desc=("Each point is one value of lambda. The family runs from exact "
              "interpolation of the correspondence at the bottom right to "
              "rigid uniform tempo scaling at the top left."))


FAMILY_LAMS = (0.01, 0.1, 0.63, 10.0, 1000.0)


def fig_family(al: Alignment) -> str:
    """The same tempo curve at five values of lambda: the whole family."""
    picks = []
    for target in FAMILY_LAMS:
        i = int(np.argmin([abs(np.log(p.lam / target)) for p in al.sweep]))
        picks.append(al.sweep[i])
    lo = min(float(np.log(p.warp.slopes.min())) for p in picks)
    hi = max(float(np.log(p.warp.slopes.max())) for p in picks)
    lo, hi = _pad(lo, hi, 0.10)
    T1 = al.warp.T1
    ax = Axes(0.0, T1, lo, hi, height=400.0, left=92.0)
    ratios = [0.4, 0.5, 0.6, 0.8, 1.0, 1.25, 1.6, 2.0, 2.5]
    yt = [np.log(r) for r in ratios if lo <= np.log(r) <= hi]
    ax.grid(_ticks(0, T1, 6), yt,
            xfmt=lambda v: f"{v:.0f}",
            yfmt=lambda v: f"×{np.exp(v):.2f}",
            xlabel="t — seconds into the false start (seg 2)",
            ylabel="φ′(t):  seg-3 seconds per seg-2 second")
    ax.hline(float(np.log(al.warp.mean_rate)), "ref")
    for k, p in enumerate(picks):
        ax.step(p.warp.knots, np.log(p.warp.slopes), f"o{k + 1}")
    return ax.render(
        title="The family of tempo curves across lambda",
        desc=("Five members of the one-parameter family, from a nearly "
              "interpolating warp to one indistinguishable from the "
              "straight line."))


def fig_rolls(al: Alignment, rows, seg_a, seg_b) -> str:
    """Two piano rolls, before and after warping, sharing one time axis."""
    w, c = al.warp, al.correspondence
    ta0 = seg_a.events[0].t + c.t[0]          # take-time of phi's origin in A
    tb0 = seg_b.events[0].t + c.s[0]
    span_a = (ta0, ta0 + w.T1)
    span_b = (tb0, tb0 + w.T2)
    A = [r for r in rows if span_a[0] - 0.6 <= r["t0"] <= span_a[1] + 0.6]
    B = [r for r in rows if span_b[0] - 0.6 <= r["t0"] <= span_b[1] + 0.6]
    pitches = [r["note"] for r in A + B]
    plo, phi_ = min(pitches) - 2, max(pitches) + 2

    panels = []
    for title, mapper in (("before warping — a rigid offset only", lambda t: t),
                          ("after warping — seg 2 through φ", lambda t: float(w(t)))):
        ax = Axes(0.0, w.T2, plo, phi_, height=300.0, left=78.0, top=30.0)
        yt = [p for p in range(24, 108, 12) if plo <= p <= phi_]
        ax.grid(_ticks(0, w.T2, 6), yt,
                xfmt=lambda v: f"{v:.0f}", yfmt=note_name,
                xlabel="seconds into the complete take (seg 3)",
                ylabel="pitch")
        for r in B:
            ax.bar(r["t0"] - tb0, r["t1"] - tb0, r["note"], 7.0, "s2",
                   opacity=0.20,
                   tip=f"seg 3   {note_name(r['note'])}\n"
                       f"{r['t0'] - tb0:.3f} s  vel {r['vel']}")
        for r in A:
            t = r["t0"] - ta0
            t1 = r["t1"] - ta0
            ax.bar(mapper(t), mapper(t1), r["note"], 2.6, "s1",
                   tip=f"seg 2   {note_name(r['note'])}\n"
                       f"{t:.3f} s → {mapper(t):.3f} s  vel {r['vel']}")
        ax.raw(f'<text class="annot" x="{ax.left:.0f}" y="16" '
               f'style="font-weight:600">{html.escape(title)}</text>')
        panels.append(ax.render(title=f"Piano roll, {title}",
                                desc="Seg 2 as solid bars over seg 3 as a wash."))
    return "".join(panels)


def legend(*items) -> str:
    out = ['<div class="legend">']
    for colour, label in items:
        if colour == "ref":
            out.append(f'<span><i class="key dsh"></i>{html.escape(label)}</span>')
        else:
            out.append(f'<span><i class="key" style="background:var(--{colour})">'
                       f'</i>{html.escape(label)}</span>')
    out.append("</div>")
    return "".join(out)
