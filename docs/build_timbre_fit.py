#!/usr/bin/env python3
"""Build ``docs/timbre-fit.html`` from the measurements, not from prose.

    ~/workspace/audio/.venv/bin/python docs/build_timbre_fit.py

Reads the per-note fits and the pooled report that
``python -m fp30x_studio.synth.fit`` writes, and emits one self-contained page:
no CDN, no scripts, inline SVG for the plots and MathML for the equations,
which is the pattern ``docs/warp-op55.html`` established in this repo. Every
number on the page comes out of the JSON; nothing is typed twice.

Inputs (override with the flags):
    ~/workspace/audio/timbre-data/iowa-fits.json        per-note measurements
    ~/workspace/audio/timbre-data/iowa-fit-report.json  pooled coefficients
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

DATA = Path("~/workspace/audio/timbre-data").expanduser()
DYN_COLOUR = {"pp": "#8ab4f8", "mf": "#7ee0d6", "ff": "#f0b45e"}


# ---------------------------------------------------------------------------
# A very small SVG plotter. Log or linear on either axis, one series at a time.
# ---------------------------------------------------------------------------

class Plot:
    def __init__(self, w=490, h=270, xlim=(21, 108), ylim=(0, 1),
                 xlog=False, ylog=False, xlabel="", ylabel="",
                 pad=(52, 14, 34, 10)):
        self.w, self.h = w, h
        self.x0, self.x1 = xlim
        self.y0, self.y1 = ylim
        self.xlog, self.ylog = xlog, ylog
        self.L, self.R, self.B, self.T = pad
        self.body: list[str] = []
        self.xlabel, self.ylabel = xlabel, ylabel

    def X(self, x):
        a = math.log10(max(x, 1e-12)) if self.xlog else x
        lo = math.log10(self.x0) if self.xlog else self.x0
        hi = math.log10(self.x1) if self.xlog else self.x1
        return self.L + (self.w - self.L - self.R) * (a - lo) / (hi - lo)

    def Y(self, y):
        a = math.log10(max(y, 1e-12)) if self.ylog else y
        lo = math.log10(self.y0) if self.ylog else self.y0
        hi = math.log10(self.y1) if self.ylog else self.y1
        return self.h - self.B - (self.h - self.B - self.T) * (a - lo) / (hi - lo)

    def grid(self, xticks, yticks, xfmt=str, yfmt=str):
        for x in xticks:
            px = round(self.X(x), 1)
            self.body.append(
                f'<line x1="{px}" y1="{self.T}" x2="{px}" y2="{self.h - self.B}" '
                f'stroke="#2a323d" stroke-width="1"/>'
                f'<text x="{px}" y="{self.h - self.B + 15}" fill="#6e7d8c" '
                f'font-size="10.5" text-anchor="middle" '
                f'font-family="ui-monospace,Menlo,monospace">{xfmt(x)}</text>')
        for y in yticks:
            py = round(self.Y(y), 1)
            self.body.append(
                f'<line x1="{self.L}" y1="{py}" x2="{self.w - self.R}" y2="{py}" '
                f'stroke="#2a323d" stroke-width="1"/>'
                f'<text x="{self.L - 6}" y="{py + 3.5}" fill="#6e7d8c" '
                f'font-size="10.5" text-anchor="end" '
                f'font-family="ui-monospace,Menlo,monospace">{yfmt(y)}</text>')
        return self

    def line(self, pts, colour="#7ee0d6", width=1.8, dash=None, opacity=1.0):
        d = " ".join(f"{'M' if i == 0 else 'L'}{self.X(x):.1f},{self.Y(y):.1f}"
                     for i, (x, y) in enumerate(pts))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.body.append(f'<path d="{d}" fill="none" stroke="{colour}" '
                         f'stroke-width="{width}"{da} opacity="{opacity}"/>')
        return self

    def dots(self, pts, colour="#e6edf3", r=3.0, opacity=0.95):
        for x, y in pts:
            self.body.append(f'<circle cx="{self.X(x):.1f}" cy="{self.Y(y):.1f}" '
                             f'r="{r}" fill="{colour}" opacity="{opacity}"/>')
        return self

    def bars(self, pts, colour="#7ee0d6", width=3.0, base=None, opacity=1.0):
        b = self.Y(self.y0 if base is None else base)
        for x, y in pts:
            self.body.append(
                f'<line x1="{self.X(x):.1f}" y1="{b:.1f}" x2="{self.X(x):.1f}" '
                f'y2="{self.Y(y):.1f}" stroke="{colour}" stroke-width="{width}" '
                f'opacity="{opacity}" stroke-linecap="round"/>')
        return self

    def text(self, x, y, s, colour="#9aa7b4", size=11, anchor="start", px=False):
        cx, cy = (x, y) if px else (self.X(x), self.Y(y))
        self.body.append(f'<text x="{cx:.1f}" y="{cy:.1f}" fill="{colour}" '
                         f'font-size="{size}" text-anchor="{anchor}" '
                         f'font-family="ui-monospace,Menlo,monospace">'
                         f'{html.escape(s)}</text>')
        return self

    def svg(self, title=""):
        lab = ""
        if self.ylabel:
            lab += (f'<text x="13" y="{self.h / 2:.0f}" fill="#9aa7b4" font-size="11" '
                    f'text-anchor="middle" transform="rotate(-90 13 {self.h / 2:.0f})" '
                    f'font-family="ui-monospace,Menlo,monospace">'
                    f'{html.escape(self.ylabel)}</text>')
        if self.xlabel:
            lab += (f'<text x="{(self.L + self.w - self.R) / 2:.0f}" y="{self.h - 2}" '
                    f'fill="#9aa7b4" font-size="11" text-anchor="middle" '
                    f'font-family="ui-monospace,Menlo,monospace">'
                    f'{html.escape(self.xlabel)}</text>')
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="100%" '
                f'role="img" aria-label="{html.escape(title or self.ylabel)}" '
                f'style="display:block">'
                f'<rect width="{self.w}" height="{self.h}" fill="#12171e" rx="8"/>'
                + "".join(self.body) + lab + "</svg>")


NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]


def legend(*items) -> str:
    """Legends go under the plot as HTML, never inside the SVG.

    Painted into the axes they collided with the data -- which the first
    screenshot of this page showed plainly, the partial-count legend sitting
    straight across the bass measurements.
    """
    out = []
    for colour, label, dash in items:
        swatch = (f'<i style="background:{colour}"></i>' if not dash else
                  f'<i style="background:linear-gradient(90deg,{colour} 55%,transparent 55%);'
                  f'height:3px;border-radius:1px"></i>')
        out.append(f'<span>{swatch}{html.escape(label)}</span>')
    return '<div class="legend">' + "".join(out) + '</div>'


DYN_LEGEND = legend(("#8ab4f8", "pp", False), ("#7ee0d6", "mf", False),
                    ("#f0b45e", "ff", False))


def note_label(m: int) -> str:
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def mathml(s: str) -> str:
    """Tiny helper so the equations below read as equations in the source."""
    return f'<math display="block" xmlns="http://www.w3.org/1998/Math/MathML">{s}</math>'


# ---------------------------------------------------------------------------

def build(fits: list[dict], rep: dict, model: dict) -> str:
    C = rep["coefficients"]

    def c(k, fmt="{:.3g}"):
        return fmt.format(C[k]["value"])

    # ---- plot 1: inharmonicity ------------------------------------------
    p1 = Plot(ylim=(6e-5, 1e-2), ylog=True, xlabel="MIDI note",
              ylabel="B")
    p1.grid([24, 36, 48, 60, 72, 84, 96],
            [1e-4, 3e-4, 1e-3, 3e-3],
            xfmt=lambda m: note_label(int(m)),
            yfmt=lambda v: f"{v:.0e}".replace("e-0", "e−"))
    p1.line(model["B_old"], "#e08a8a", 1.6, dash="5 4")
    p1.line(model["B_new"], "#7ee0d6", 2.0)
    for dyn, col in DYN_COLOUR.items():
        p1.dots([(m, b) for m, b, d in model["B"] if d == dyn], col, 3.1)

    # ---- plot 2: rolloff -------------------------------------------------
    p2 = Plot(ylim=(-0.6, 6.0), xlabel="MIDI note",
              ylabel="rolloff k")
    p2.grid([24, 36, 48, 60, 72, 84, 96], [0, 1, 2, 3, 4, 5],
            xfmt=lambda m: note_label(int(m)), yfmt=lambda v: f"{v:.0f}")
    p2.line(model["roll_old"], "#e08a8a", 1.6, dash="5 4")
    p2.line(model["roll_new"], "#7ee0d6", 2.0)
    for dyn, col in DYN_COLOUR.items():
        p2.dots([(m, r) for m, r, d in model["roll"] if d == dyn], col, 3.1)

    # ---- plot 3: decay time ---------------------------------------------
    p3 = Plot(ylim=(0.1, 30), ylog=True, xlabel="MIDI note",
              ylabel="τ₁ (s)")
    p3.grid([24, 36, 48, 60, 72, 84, 96], [0.3, 1, 3, 10],
            xfmt=lambda m: note_label(int(m)), yfmt=lambda v: f"{v:g}")
    p3.line(model["tau_old"], "#e08a8a", 1.6, dash="5 4")
    p3.line(model["tau_new"], "#7ee0d6", 2.0)
    for dyn, col in DYN_COLOUR.items():
        p3.dots([(m, t) for m, t, d in model["tau1"] if d == dyn], col, 3.1)

    # ---- plot 4: how many partials are actually there --------------------
    p4 = Plot(ylim=(0, 34), xlabel="MIDI note",
              ylabel="partials")
    p4.grid([24, 36, 48, 60, 72, 84, 96], [0, 8, 16, 24, 32],
            xfmt=lambda m: note_label(int(m)), yfmt=lambda v: f"{v:.0f}")
    p4.line([(21, 28), (108, 28)], "#e08a8a", 1.6, dash="5 4")
    for dyn, col in DYN_COLOUR.items():
        p4.dots([(m, n) for m, n, d in model["naud"] if d == dyn], col, 3.1)

    # ---- plot 5: measured vs modelled spectrum, C4 ff --------------------
    sp = model["spec"]
    p5 = Plot(w=490, h=280, xlim=(0.7, 34), ylim=(-80, 4), xlog=True,
              xlabel="partial index n", ylabel="dB")
    p5.grid([1, 2, 4, 8, 16, 32], [0, -20, -40, -60, -80],
            xfmt=lambda v: f"{v:g}", yfmt=lambda v: f"{v:.0f}")
    p5.line([(n, v) for n, v in sp["old"] if v > -80], "#e08a8a", 1.5, dash="5 4")
    p5.line([(n, v) for n, v in sp["new"] if v > -80], "#7ee0d6", 1.9)
    p5.bars([(n, v) for n, v in sp["measured"] if v > -80], "#e6edf3", 2.2,
            base=-80, opacity=0.55)

    # ---- plot 6: the decay law that does not hold ------------------------
    p6 = Plot(w=490, h=280, xlim=(0.7, 34), ylim=(0.08, 40), xlog=True, ylog=True,
              xlabel="partial index n", ylabel="αₙ (1/s)")
    p6.grid([1, 2, 4, 8, 16, 32], [0.1, 1, 10],
            xfmt=lambda v: f"{v:g}", yfmt=lambda v: f"{v:g}")
    series = [("36_ff", "#8ab4f8", "C2 ff"), ("54_ff", "#7ee0d6", "F♯3 ff"),
              ("60_ff", "#f0b45e", "C4 ff")]
    for key, col, lab in series:
        pts = [(n, a) for n, a, _, _ in model["alpha"][key]]
        p6.dots(pts, col, 3.0)
        if pts:
            a1 = pts[0][1]
            p6.line([(1, a1), (32, a1 * 32 ** 0.127)], col, 1.3, dash="3 3")
    a0 = model["alpha"]["60_ff"][0][1]
    p6.line([(1, a0), (32, a0 * 32 ** 2)], "#e08a8a", 1.6)
    p6.line([(1, a0), (32, a0 * 32 ** 0.65)], "#e08a8a", 1.6, dash="6 4")

    # ---- the coefficient table ------------------------------------------
    OLD = {"partials": "28", "inharmonicity_B": "4.0e−4",
           "inharmonicity_decades_per_octave": "0.20 (implied)",
           "inharmonicity_floor": "— (none)",
           "partial_amp_rolloff": "1.25", "rolloff_per_octave": "— (flat)",
           "hammer_strike_point": "0.125", "partial_decay_base": "2.4 s",
           "decay_halving_semitones": "24", "partial_decay_exponent": "0.65",
           "second_stage_ratio": "3.5", "second_stage_mix": "0.22",
           "unison_detune_cents": "0.7", "unison_depth": "0.40",
           "attack_ms": "4.0", "velocity_brightness": "1.3"}
    ORDER = ["inharmonicity_B", "inharmonicity_decades_per_octave",
             "inharmonicity_floor", "partial_amp_rolloff", "rolloff_per_octave",
             "hammer_strike_point", "partial_decay_base",
             "decay_halving_semitones", "partial_decay_exponent",
             "second_stage_ratio", "second_stage_mix", "unison_detune_cents",
             "unison_depth", "velocity_brightness", "attack_ms"]
    rows = []
    for k in ORDER:
        v = C[k]
        val = v["value"]
        s = f"{val:.4g}"
        err = ""
        if "stderr" in v:
            err = f" ± {v['stderr']:.2g}"
        elif "stderr_factor" in v:
            err = f" ×/ {v['stderr_factor']:.2f}"
        flag = ""
        if v.get("identified") is False:
            flag = ' <span class="up">not identified</span>'
        rows.append(
            f'<tr><td><code>{k}</code></td><td class="n dn">{OLD.get(k, "—")}</td>'
            f'<td class="n"><b>{s}</b>{err}</td>'
            f'<td class="n">{v.get("n", "—")}</td>'
            f'<td>{html.escape(v.get("note", ""))}{flag}</td></tr>')
    table = "\n".join(rows)

    unfit = "\n".join(
        f'<tr><td><code>{html.escape(k)}</code></td><td>{html.escape(v)}</td></tr>'
        for k, v in rep["unfitted"].items())

    slopes = rep["decay_law_fails"]["per_note_slopes"]
    med_r2 = rep["decay_law_fails"]["median_r2"]

    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fitting the Piano</title>
<style>
:root{{--bg:#0e1116;--panel:#161b22;--line:#2a323d;--ink:#e6edf3;--dim:#9aa7b4;
--dim2:#6e7d8c;--acc:#7ee0d6;--acc2:#8ab4f8;--warn:#f0b45e;--bad:#e08a8a;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,sans-serif}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
line-height:1.62;font-size:15.5px;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1060px;margin:0 auto;padding:44px 22px 100px}}
h1{{font-size:29px;margin:0 0 6px;letter-spacing:-.02em}}
.sub{{color:var(--dim);margin:0 0 30px}}
h2{{font-size:13px;text-transform:uppercase;letter-spacing:.11em;color:var(--dim2);
margin:46px 0 14px;font-weight:600}}
h3{{font-size:16px;margin:26px 0 8px}}
.hero{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:26px 0}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}}
.stat .v{{font-size:29px;font-weight:650;letter-spacing:-.02em;font-family:var(--mono)}}
.stat .k{{color:var(--dim);font-size:12.5px;margin-top:5px}}
.big .v{{color:var(--acc)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:20px;margin:16px 0}}
.cap{{color:var(--dim);font-size:13.5px;margin-top:10px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}
th,td{{padding:7px 11px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--dim2);font-weight:600;font-size:12px;text-transform:uppercase;
letter-spacing:.06em}}
td.n{{font-family:var(--mono);white-space:nowrap}}
.up{{color:var(--warn)}} .dn{{color:var(--bad)}}
math{{font-size:1.05em}}
.eq{{margin:14px 0}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:900px){{.two{{grid-template-columns:1fr}}}}
.warnbox{{border-color:#5a4426;background:#1a1610}}
.okbox{{border-color:#2f6f6a;background:#101f21}}
code{{font-family:var(--mono);font-size:.9em;background:#1c232c;padding:1px 5px;
border-radius:4px}}
footer{{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);
color:var(--dim2);font-size:12.5px}}
.scrollx{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:12.5px;color:var(--dim);
margin-top:12px}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:3px;
margin-right:7px;vertical-align:-1px}}
ul{{padding-left:20px}} li{{margin:6px 0}}
</style>
<div class="wrap">

<h1>Fitting the piano</h1>
<p class="sub">Every coefficient in the string model, measured against
{len(fits)} isolated notes of a Steinway&nbsp;B instead of guessed —
19&nbsp;August&nbsp;2026. What moved, what did not, and where the model's
functional form cannot fit the data at all.</p>

<div class="card okbox">
<p><b>I cannot hear any of this.</b> Nothing below is a claim that the new
render sounds better. It is a claim about numbers: what the recordings say, what
the old preset said, and how far apart those two things were.</p>
</div>

<div class="hero">
  <div class="stat big"><div class="v">{len(fits)}</div>
    <div class="k">notes measured, C1–C7 × pp/mf/ff</div></div>
  <div class="stat"><div class="v">15</div>
    <div class="k">coefficients fitted</div></div>
  <div class="stat"><div class="v">7</div>
    <div class="k">left unfitted, and flagged</div></div>
  <div class="stat"><div class="v">1–3¢</div>
    <div class="k">departure from the stiff-string law over 32 partials</div></div>
</div>

<h2>The short version</h2>
<div class="card">
<p>Three coefficients were wrong by a lot, and they are all the same kind of
wrong: <b>a quantity that varies by an order of magnitude across the keyboard was
stored as one number.</b></p>
<ul>
<li><b>The amplitude rolloff</b> was 1.25 for every key. Measured, it is
{c('partial_amp_rolloff', '{:.2f}')} at A4 and rises
{C['rolloff_per_octave']['value']:+.2f} per octave — nearly flat in the bass,
steep at the top. At C6 the old preset gave a partial series roughly 20&nbsp;dB
too bright at the tenth partial, which is to say it synthesised a dozen partials
that the recorded instrument does not have. This is the largest single error.</li>
<li><b>The decay time</b> was 2.4&nbsp;s at A4, halving every two octaves.
Measured, the prompt decay is {c('partial_decay_base', '{:.2f}')}&nbsp;s and
halves every {C['decay_halving_semitones']['value']:.1f} semitones — three times
shorter, and falling faster with pitch.</li>
<li><b>The aftersound</b> was 0.22 of the prompt sound. Measured it is
{c('second_stage_mix', '{:.4f}')}, twenty times smaller. Between that and the
decay time, the old model sustained every note like an organ stop.</li>
</ul>
<p>And <b>inharmonicity was not the problem.</b> Its coefficient was off — 4e−4
against a measured 6.5e−4 at A4, and the wrong slope — but the
<em>law</em> holds beautifully: the RMS departure of the measured partial series
from
<math xmlns="http://www.w3.org/1998/Math/MathML"><msub><mi>f</mi><mi>n</mi></msub><mo>=</mo><mi>n</mi><msub><mi>f</mi><mn>0</mn></msub><msqrt><mrow><mn>1</mn><mo>+</mo><mi>B</mi><msup><mi>n</mi><mn>2</mn></msup></mrow></msqrt></math>
is {rep['inharmonicity_law_holds']['rms_departure_cents_median']:.1f}&nbsp;cents
over as many as 32 partials.</p>
</div>

<h2>What each suspect turned out to be</h2>
<div class="card">
<table>
<tr><th>Suspect</th><th>Verdict</th></tr>
<tr><td>Inharmonicity is bell-sized</td><td><span class="up">Refuted as a
law, confirmed as a coefficient.</span> The stiff-string series fits to 1–3
cents. B at A4 is 6.5e−4, not 4e−4, and rises a decade every 31 semitones, not
60 — so the treble is <em>more</em> stretched than the old preset, not less.
What made the top sound bell-like was the partial count and the rolloff, not
B.</td></tr>
<tr><td>High partials ring far too long; the exponent should be nearer 2</td>
<td><span class="dn">Refuted, and backwards.</span> Fitted per note, α<sub>n</sub>
∝ n<sup>{c('partial_decay_exponent', '{:.2f}')}</sup>. The largest slope seen on
any of the {len(slopes)} notes is
{C['partial_decay_exponent']['max_observed']:.2f}. The old 0.65 was already
<em>above</em> the measurement; 2 is not close. What made notes ring was τ₁ and
the aftersound mix, both of which were far too large.</td></tr>
<tr><td>All partials start at identical phase</td><td><span class="up">Already
false in the offline renderer</span> — <code>voices.py</code> has drawn phases
from a uniform distribution since the model was written. It is now a parameter
(<code>partial_phase_spread</code>) and the browser workbench, which really did
start every partial at zero phase, now honours it.</td></tr>
<tr><td>One string per note</td><td><span class="up">Already modelled</span>, and
now measured. Beating is present from MIDI&nbsp;60 up (periodogram concentration
{rep['unison_register']['60_and_up']:.2f}) and absent below 48
({rep['unison_register']['below_48']:.3f}) — exactly where the strings go to
single wound ones. Detune {c('unison_detune_cents', '{:.2f}')}¢ and depth
{c('unison_depth', '{:.2f}')}, both measured, against 0.7¢ and 0.40
guessed.</td></tr>
<tr><td>No soundboard or radiation filtering</td><td><span class="dn">Not
identifiable, and deliberately not added.</span> See below.</td></tr>
<tr><td>Aliasing</td><td><span class="dn">Refuted, quantitatively.</span> The
partial series is band-limited before synthesis. Rendering MIDI 96–108 at
48&nbsp;kHz and integrating the spectrum above 22.8&nbsp;kHz gives 3&times;10<sup>−17</sup>
of the total with the hammer burst off — numerical noise. The residual
10<sup>−9</sup>–10<sup>−6</sup> with it on is the burst's own bandwidth, which is
generated at the sample rate and is not folded.</td></tr>
<tr><td>The damper ignores frequency</td><td><span class="up">Confirmed, and it
was a real bug.</span> The damper was applied to the summed buffer, so a note
under the felt kept its high partials at full relative weight to silence. Now
per partial, at α<sub>n</sub> = α₁·n<sup>q</sup>.</td></tr>
</table>
</div>

<h2>Inharmonicity</h2>
<div class="two">
<div class="card">{p1.svg("inharmonicity against pitch")}
{legend(("#7ee0d6", "fitted: 6.5e−4 at A4, a decade per 31 semitones, floored at 1.4e−4", False),
        ("#e08a8a", "guessed: 4e−4 at A4, a decade per 60", True))}
{DYN_LEGEND}
<p class="cap">Each dot is one recorded note. The dashed red line is the law the
preset used; the solid line is the fitted one. Note the bass: below the break the
exponential extrapolates sixteen times too small, so the fitted law floors at
{c('inharmonicity_floor', '{:.2e}')}. C1 measures 2.5e−4 and sits above even
that — a documented residual, not a fitted point.</p></div>
<div class="card">
<p>The stiff-string law rearranges to something linear, which is why it can be
fitted without iteration and why its residual is interpretable:</p>
<div class="eq">{mathml(
  '<mrow><msup><mrow><mo>(</mo><mfrac><msub><mi>f</mi><mi>n</mi></msub><mi>n</mi></mfrac><mo>)</mo></mrow><mn>2</mn></msup>'
  '<mo>=</mo><msubsup><mi>f</mi><mn>0</mn><mn>2</mn></msubsup>'
  '<mo>+</mo><msubsup><mi>f</mi><mn>0</mn><mn>2</mn></msubsup><mi>B</mi><msup><mi>n</mi><mn>2</mn></msup></mrow>')}</div>
<p>Regress the left side on <math xmlns="http://www.w3.org/1998/Math/MathML"><msup><mi>n</mi><mn>2</mn></msup></math>
and the intercept is <math xmlns="http://www.w3.org/1998/Math/MathML"><msubsup><mi>f</mi><mn>0</mn><mn>2</mn></msubsup></math>,
the slope is <math xmlns="http://www.w3.org/1998/Math/MathML"><msubsup><mi>f</mi><mn>0</mn><mn>2</mn></msubsup><mi>B</mi></math>,
and the residual is a direct test of whether <em>any</em> stiff-string law
describes the note. It does: {rep['inharmonicity_law_holds']['rms_departure_cents_median']:.1f}
cents median.</p>
<p>Across pitch, <math xmlns="http://www.w3.org/1998/Math/MathML"><msub><mi>log</mi><mn>10</mn></msub><mi>B</mi></math>
is linear with slope {c('inharmonicity_decades_per_octave', '{:.3f}')} ±
{C['inharmonicity_decades_per_octave']['stderr']:.3f} decades per octave over
{C['inharmonicity_decades_per_octave']['n']} notes, scatter a factor of
{C['inharmonicity_B']['resid_factor']:.2f}.</p>
</div>
</div>

<h2>The spectrum, which is where the noise was coming from</h2>
<div class="two">
<div class="card">{p2.svg("rolloff exponent against pitch")}
{legend(("#7ee0d6", f"fitted: {c('partial_amp_rolloff', '{:.2f}')} at A4, "
                    f"{C['rolloff_per_octave']['value']:+.2f} per octave", False),
        ("#e08a8a", "guessed: 1.25, flat across the keyboard", True))}
{DYN_LEGEND}
<p class="cap">The rolloff exponent, fitted per note on the upper envelope of
the partial amplitudes (partials sitting in a hammer-strike notch are not
evidence about the tilt of the spectrum, so the fit takes the loudest partial in
each half-octave band of n). One number for the keyboard cannot be right: the
bass is nearly flat and the top two octaves are four times steeper.</p></div>
<div class="card">{p4.svg("audible partial count against pitch")}
{legend(("#e08a8a", "the old preset synthesised 28 for every key", True))}
{DYN_LEGEND}
<p class="cap">The same fact seen directly. Counting partials within 60 dB of
the loudest: 32 in the bass, 8–16 in the middle, 2–6 above C6. The old preset
built 28 for every key.</p></div>
</div>

<div class="two">
<div class="card">{p5.svg("measured against modelled spectrum at C4 ff")}
{legend(("#e6edf3", "measured C4 ff (bars)", False),
        ("#7ee0d6", "fitted model", False),
        ("#e08a8a", "guessed model", True))}
<p class="cap">C4 struck ff. Bars are the measured partial amplitudes,
extrapolated back to the onset from each partial's own decay fit. The old model
(dashed) is 15–25 dB too loud from the eighth partial up. The fitted one tracks
the envelope, including the strike-point notch near n =
{1 / C['hammer_strike_point']['value']:.0f}.</p></div>
<div class="card">
<p>Amplitude, with both corrections:</p>
<div class="eq">{mathml(
  '<mrow><msub><mi>a</mi><mi>n</mi></msub><mo>=</mo><msup><mi>n</mi>'
  '<mrow><mo>&#x2212;</mo><mi>k</mi><mo>(</mo><mi>p</mi><mo>)</mo><mo>+</mo>'
  '<mi>&#x03B2;</mi><mo>(</mo><mi>v</mi><mo>&#x2212;</mo><mfrac><mn>1</mn><mn>2</mn></mfrac><mo>)</mo></mrow></msup>'
  '<mo>&#x22C5;</mo><mrow><mo>|</mo><mi>sin</mi><mo>&#x2061;</mo><mi>n</mi><mi>&#x03C0;</mi><mi>&#x03B1;</mi><mo>|</mo></mrow></mrow>')}</div>
<div class="eq">{mathml(
  '<mrow><mi>k</mi><mo>(</mo><mi>p</mi><mo>)</mo><mo>=</mo>'
  f'<mn>{C["partial_amp_rolloff"]["value"]:.2f}</mn><mo>+</mo><mn>{C["rolloff_per_octave"]["value"]:.2f}</mn>'
  '<mo>&#x22C5;</mo><mfrac><mrow><mi>p</mi><mo>&#x2212;</mo><mn>69</mn></mrow><mn>12</mn></mfrac></mrow>')}</div>
<p>The strike point <math xmlns="http://www.w3.org/1998/Math/MathML"><mi>&#x03B1;</mi></math>
is itself measured, not assumed: dividing the fitted tilt out of the measured
amplitudes leaves a comb, and scanning for the α that best explains it gives
{c('hammer_strike_point', '{:.4f}')} = 1/{1 / C['hammer_strike_point']['value']:.1f}
over {C['hammer_strike_point']['n']} notes — inside the 1/7 to 1/9 that real
pianos use. The old constant 0.125 was already right, within the scatter.</p>
<p>The velocity term β is the weakest of the fitted set:
{c('velocity_brightness', '{:.2f}')} ±
{C['velocity_brightness']['stderr']:.2f} on
{C['velocity_brightness']['n']} notes. Its interval contains the old guess of
1.3. It is wide because the Iowa recordings carry no velocity byte, only the
words pp, mf and ff, and the mapping from those to a number is an assumption.</p>
</div>
</div>

<h2>Decay — and where the model's form gives out</h2>
<div class="two">
<div class="card">{p3.svg("prompt decay time against pitch")}
{legend(("#7ee0d6", f"fitted: {c('partial_decay_base', '{:.2f}')} s at A4, halving every "
                    f"{C['decay_halving_semitones']['value']:.1f} semitones", False),
        ("#e08a8a", "guessed: 2.4 s, halving every 24", True))}
{DYN_LEGEND}
<p class="cap">τ₁ from the first 25 dB of the fundamental's decay. The 25 dB
matters: the recording is <em>not</em> anechoic, and once a partial has fallen
far enough the microphone is hearing the room decay rather than the string.
Scatter about the line is 0.89 octaves, which is large and real — neighbouring
keys on a piano differ by that much.</p></div>
<div class="card warnbox">{p6.svg("per-partial decay rate against partial index")}
{legend(("#8ab4f8", "C2 ff", False), ("#7ee0d6", "F♯3 ff", False),
        ("#f0b45e", "C4 ff", False),
        ("#e08a8a", "αₙ ∝ n² — stiffness-proportional damping", False),
        ("#e08a8a", "αₙ ∝ n^0.65 — the old preset", True))}
<p class="cap"><b>This is the plot that says the model is wrong.</b> Each dot is
one partial's fitted decay rate. If damping were stiffness-proportional the
points would follow the solid red line, α<sub>n</sub> ∝ n². If the old preset
were right they would follow the dashed one. They follow neither, and they do
not follow a power law at all: fitted per note over {len(slopes)} notes, the
median exponent is {c('partial_decay_exponent', '{:.3f}')} and the power law's
median R² is <b>{med_r2:.2f}</b>.</p></div>
</div>

<div class="card warnbox">
<h3>The functional form cannot fit this data</h3>
<p>The model writes
<math xmlns="http://www.w3.org/1998/Math/MathML"><msub><mi>&#x03C4;</mi><mi>n</mi></msub><mo>=</mo><msub><mi>&#x03C4;</mi><mn>1</mn></msub><msup><mi>n</mi><mrow><mo>&#x2212;</mo><mi>e</mi></mrow></msup></math>.
Fitted note by note it explains a median {med_r2 * 100:.0f}% of the variance in
per-partial decay rate, and in
{100 * rep['decay_law_fails']['fraction_r2_above_half']:.0f}% of notes more than
half. The scatter is not measurement noise — adjacent partials of a single note
differ by a factor of two in either direction, with fit R² above 0.8 on each of
them individually. It is bridge and soundboard coupling, which is mode by mode
and irregular, and a smooth power law in n has no way to represent it.</p>
<p>So {c('partial_decay_exponent', '{:.3f}')} is the best single number, and it
is a weak law rather than a strong one. Reproducing what a real piano does here
would need a per-partial admittance, not a better exponent. That is a bigger
change than this pass, and it is written down rather than hidden inside a fitted
coefficient.</p>
<p>A second thing the corpus cannot settle: <code>second_stage_ratio</code>. The
aftersound's <em>level</em> is well determined ({c('second_stage_mix', '{:.4f}')},
IQR {C['second_stage_mix']['iqr'][0]:.4f}–{C['second_stage_mix']['iqr'][1]:.4f}),
but its <em>time constant</em> runs to whatever upper bound the search grid
allows in {C['second_stage_ratio']['n']} notes, because the aftersound sits close
to the recording's own floor. The data gives a lower bound of
{C['second_stage_ratio']['lower_bound']:.1f} and no upper one; the preset takes
12, the low end of that bracket, which is the shortest tail the measurements
permit.</p>
</div>

<h2>The radiation filter that is not there</h2>
<div class="card">
<p>A soundboard transfer would be a function of absolute frequency, and the
partial rolloff is a function of index. Within one note those are the same thing:
<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>log</mi><mo>&#x2061;</mo><msub><mi>f</mi><mi>n</mi></msub><mo>=</mo><mi>log</mi><mo>&#x2061;</mo><mi>n</mi><mo>+</mo><mi>log</mi><mo>&#x2061;</mo><msub><mi>f</mi><mn>0</mn></msub></math>,
and once each note's own level is free the two regressors are collinear. Fitting
both with per-note intercepts over 337 partials of 21 notes gives
k<sub>n</sub> = −45 ± 4 and k<sub>f</sub> = +46 ± 4: equal, opposite and
meaningless.</p>
<p>So no radiation filter was added. The fitted pitch-dependent rolloff already
carries whatever radiation shaping is in these recordings, and a separate filter
would double-count it. Identifying one needs a design this corpus does not have —
the same note at several string lengths, or a measured soundboard admittance.</p>
</div>

<h2>Every coefficient, and where it came from</h2>
<div class="card"><div class="scrollx"><table>
<tr><th>Coefficient</th><th>Guessed</th><th>Fitted</th><th>n</th><th>How</th></tr>
{table}
</table></div>
<p class="cap">Errors are standard errors of the regression coefficient; “×/” is a
multiplicative one on a log-fitted quantity. n is the number of notes the
estimate rests on.</p></div>

<h2>What is still guessed</h2>
<div class="card warnbox">
<p>Seven coefficients could not be reached by this corpus. They are listed here
because a preset in which some numbers are measured and some are not is more
dangerous than one in which none are: it invites you to trust all of them
equally.</p>
<div class="scrollx"><table>
<tr><th>Coefficient</th><th>Why not, and what would fix it</th></tr>
{unfit}
</table></div>
</div>

<h2>The electric piano is not fitted at all</h2>
<div class="card warnbox">
<p>No openly-licensed corpus of isolated Rhodes notes was reachable without
creating an account, which was out of scope. The MuseScore_General electric piano
that ships with this project was checked and <b>rejected as a source</b>: its pp,
mf and ff spectra are bit-identical, so it carries one velocity layer and no
velocity-to-timbre information at all, and its partials are harmonic to within
0.5%, so there is no bell partial in it to measure.</p>
<p>One number in the tine preset does have an outside authority.
<code>bell_partial</code> = 6.267 is
(2.988/1.194)<sup>2</sup>, the second transverse mode of a clamped–free bar,
which is what a tine is. Everything else there is a first guess and is marked as
one.</p>
<p><b>The FP-30X's own voice could not be analysed.</b> Its <code>USB COMPUTER</code>
port carries audio as well as MIDI, but the USB-C-to-USB-B cable needed to reach
it is still on order; the piano's USB-A socket is a flash-drive host port and
enumerates nothing. Every WAV currently in <code>~/Music/FP-30X&nbsp;Studio/takes/</code>
is a fluidsynth render of the captured MIDI through
<code>MuseScore_General.sf3</code>, not a recording of the instrument. Two ways
in, once the cable lands or before: a USB-B capture of single notes across the
register at three dynamics, about twenty minutes of playing; or a USB flash drive
in the front-panel socket, which records 44.1&nbsp;kHz WAV with no computer at
all and is free to try today.</p>
</div>

<h2>Method</h2>
<div class="card">
<p>Partial frequencies come from a zero-padded DFT of a 0.6&nbsp;s window
starting 20&nbsp;ms after the strike — past the hammer noise, which is broadband
and would otherwise be peak-picked as a partial — with peaks accepted against a
<em>local</em> noise floor rather than a fixed level below the loudest partial.
That is what makes the treble notes come out with four partials instead of
thirty: a fixed threshold accepts the hiss and the regression then fits it.</p>
<p>Per-partial envelopes are a heterodyne filter bank, not an STFT tracker:
multiply by
<math xmlns="http://www.w3.org/1998/Math/MathML"><msup><mi>e</mi><mrow><mo>&#x2212;</mo><mn>2</mn><mi>&#x03C0;</mi><mi>i</mi><msub><mi>f</mi><mi>n</mi></msub><mi>t</mi></mrow></msup></math>,
low-pass at f₀/2.4 and decimate. The passband sits exactly on the measured
partial frequency, so it is immune to the bin quantisation and the
time–frequency trade that make an STFT track wander on a decaying tone.</p>
<p>Decay rates are amplitude-weighted least squares on
<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>log</mi><mo>&#x2061;</mo><mo>|</mo><mi>A</mi><mo>|</mo></math>
over the first 25&nbsp;dB. Everything is in
<code>fp30x_studio/synth/analysis.py</code> (one note in, numbers out) and
<code>fp30x_studio/synth/fit.py</code> (many notes in, a preset and this report
out); rerunning <code>python -m fp30x_studio.synth.fit</code> on the same folder
reproduces every number on this page.</p>
<p><b>Provenance.</b> University of Iowa Electronic Music Studios, Musical
Instrument Samples: Steinway &amp; Sons model B, played by Evan Mazunik, recorded
5 and 27 November 2001 in 2017 Voxman Music Building by Michael Cash; two Neumann
KM&nbsp;84 cardioids 8″ above the bass and treble strings, Mackie 1402-VLZ,
Panasonic SV-3800 DAT, 16-bit 44.1&nbsp;kHz stereo, <b>non-anechoic</b>. The
collection has been free to download and use without restriction since 1997; no
account was created and no terms were accepted. Files and a
<code>PROVENANCE.md</code> are in
<code>~/workspace/audio/timbre-data/iowa-piano/</code>.</p>
</div>

<h2>The attack, which needed a calibration rather than a fit</h2>
<div class="card">
<p>Measured 10%-to-peak on the summed signal gives a median of
{rep['attack_calibration']['measured_median_ms']:.1f}&nbsp;ms across
{rep['attack_calibration']['n']} mf and ff notes, which looks like five times the
model's 4&nbsp;ms. It is not: that estimator measures when the partials finish
interfering, not when one of them rises. Running it on this model's <em>own</em>
output with <code>attack_ms</code> set to 1, 2, 4, 8, 12 and 20 reports 12.8,
14.8, 20.5, 30.2, 36.3 and 62.3&nbsp;ms. The recorded 20.8 therefore corresponds
to <code>attack_ms</code> = 4.0 — exactly what the preset already had. The attack
was not the problem, and a naive fit would have made it five times too slow.</p>
</div>

<footer>
Built by <code>docs/build_timbre_fit.py</code> from
<code>iowa-fits.json</code> and <code>iowa-fit-report.json</code>. Self-contained:
no scripts, no network, inline SVG and MathML. Machine-written on Jake's behalf,
19 August 2026. The renders these coefficients produced are
<code>~/Music/FP-30X&nbsp;Studio/renders/2026-08-17-piece.{{acoustic,electric}}.fitted.wav</code>,
beside the originals for A/B.
</footer>
</div>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", default=str(DATA / "iowa-fits.json"))
    ap.add_argument("--report", default=str(DATA / "iowa-fit-report.json"))
    ap.add_argument("--plotdata", default=str(DATA / "iowa-plotdata.json"))
    ap.add_argument("--out", default=str(Path(__file__).parent / "timbre-fit.html"))
    a = ap.parse_args()
    fits = json.loads(Path(a.fits).expanduser().read_text())
    rep = json.loads(Path(a.report).expanduser().read_text())
    model = json.loads(Path(a.plotdata).expanduser().read_text())
    out = Path(a.out)
    out.write_text(build(fits, rep, model))
    print(f"{out}  {out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
