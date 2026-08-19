r"""Build ``docs/warp-op55.html``: the Op. 55 No. 1 alignment, written up and
playable.

Run it with::

    .venv/bin/python -m fp30x_studio.align

Everything on the page is computed here, from the take, at build time. Nothing
is fetched at view time: the plots are inline SVG, the equations are MathML,
and the sound is Web Audio synthesis driven by note events inlined as JSON.
The page opens from a bare ``file://`` URL, with no server and no network, a
month from now.

**Why synthesis and not audio files.** Two rendered performances would be tens
of megabytes and would freeze the page's sound at whatever the renderer did
that day. The note events are the measurement; a WAV is a lossy picture of one
playback of it. Synthesising in the browser keeps the page under a megabyte and
keeps the audio and the arithmetic reading from the same numbers.

**What you can hear.** Three timelines, from two performances:

``A``  the false start (segment 2), as played.
``B``  the matching opening span of the complete performance (segment 3), as
       played, on its own clock.
``W``  that same span pushed through :math:`\varphi^{-1}`, so it runs on A's
       clock.

The point of the page is the pair ``A + W`` played together. If :math:`\varphi`
is doing its job the two lock; the control, ``A + B`` merely started together
with no warp, drifts apart by the end. A is panned left, B and W right, so the
two voices stay separable by ear.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .events import Segment, load_segments
from .report import Alignment, align_segments

__all__ = ["build", "render", "TAKE", "OUT"]

TAKE = Path.home() / "Music" / "FP-30X Studio" / "takes" / "2026-08-19-a.fp30"
OUT = Path(__file__).resolve().parents[2] / "docs" / "warp-op55.html"

#: Segment indices in the take. 2 is the false start, 3 the complete run.
SEG_A, SEG_B = 2, 3

#: Sustain-pedal controller number.
SUSTAIN_CC = 64

#: Slack past a segment's last onset cluster, in seconds, when collecting the
#: note rows that belong to it. A cluster is timed by its *earliest* member, so
#: the stragglers of a rolled final chord sit just past it.
TAIL_S = 0.30

#: How far past the matched span to keep playing the complete performance, so
#: the ear hears the phrase finish rather than a guillotine.
RUNOUT_S = 1.20


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def _store(take: Path):
    from ..pipeline import TakeStore

    st = TakeStore(take)
    st.ingest()
    return st


def _notes(st, t0: float, t1: float) -> list[dict]:
    """Note intervals in ``[t0, t1]``, straight from the pipeline.

    Never a hand parser: a CoreMIDI packet can carry several messages, and
    every hand-rolled reader on this machine has at some point split them
    wrongly. :class:`~fp30x_studio.pipeline.TakeStore` owns the framing and the
    note-on/note-off pairing.
    """
    out = []
    for r in st.intervals():
        a = st.seconds(r["ns_on"])
        if not (t0 - 1e-9 <= a <= t1 + 1e-9):
            continue
        b = st.seconds(r["ns_off"]) if r["ns_off"] is not None else a + 0.12
        out.append({"t": a, "dur": max(0.02, b - a),
                    "note": int(r["note"]), "vel": int(r["velocity_on"])})
    out.sort(key=lambda r: (r["t"], r["note"]))
    return out


def _pedal(st, t0: float, t1: float) -> list[tuple[float, int]]:
    """CC64 in ``[t0, t1]``, with the state at ``t0`` carried in.

    Without the carried-in state a span that opens mid-pedal comes back
    staccato, because the damper is already off the strings and the page has no
    way to know it. He holds this nocturne with his foot; the note-off is not
    when the string stops, and the alignment is much less convincing dry.
    """
    events, before = [], None
    for m in st.messages(kinds=["control_change"]):
        if m["d1"] != SUSTAIN_CC:
            continue
        t, v = st.seconds(m["ns"]), int(m["d2"] or 0)
        if t < t0:
            before = v
        elif t <= t1:
            events.append((t, v))
    if before is not None and before >= 64 and (not events or events[0][0] > t0):
        events.insert(0, (t0, before))
    return events


# ---------------------------------------------------------------------------
# the playable payload
# ---------------------------------------------------------------------------

def _track(notes, pedal, origin: float, warp=None, t_origin: float = 0.0,
           s_origin: float = 0.0) -> dict:
    r"""One playable timeline, in milliseconds relative to ``origin``.

    With ``warp`` given, every time is pushed through :math:`\varphi^{-1}`
    first: ``s`` seconds into B becomes :math:`\varphi^{-1}(s-s_0)+t_0`
    seconds into A. Note *durations* are warped too, not just onsets -- a note
    held through a stretched bar is held longer, and warping onsets alone would
    put the right attacks over the wrong sustain.

    ``numpy.interp`` clamps outside the knots, so anything past the matched
    span lands on the endpoint rather than extrapolating a slope nobody fitted.
    """
    def z(t: float) -> float:
        if warp is None:
            return t - origin
        return float(warp.inverse(t - origin - s_origin)) + t_origin

    ev = [[round(1000 * z(r["t"]), 1), r["note"], r["vel"],
           round(1000 * max(0.02, z(r["t"] + r["dur"]) - z(r["t"])), 1)]
          for r in notes]
    ped = [[round(1000 * z(t), 1), v] for t, v in pedal]
    span = max((e[0] + e[3] for e in ev), default=0.0) / 1000.0
    return {"events": ev, "pedal": ped, "span": round(span + 1.5, 3)}


def listen_stats(al: Alignment) -> dict:
    r"""How close the two takes actually land, as the ear will get them.

    The page's own residual is :math:`\varphi(t_i)-s_i`, measured in B seconds.
    What a listener gets is the other direction -- the warped take strikes at
    :math:`\varphi^{-1}(s_i)` on A's clock, and the gap to :math:`t_i` is what
    they hear as together or not together. The two differ by a factor of
    :math:`\varphi'` and it is worth quoting the one that corresponds to the
    demonstration rather than the one that is convenient.

    ``rigid`` is the control: B merely started at the same moment as A.
    """
    w, c = al.warp, al.correspondence
    t = np.asarray(c.t) - c.t[0]
    s = np.asarray(c.s) - c.s[0]
    out = {}
    for name, d in (("warped", (w.inverse(s) - t) * 1000.0),
                    ("rigid", (s - t) * 1000.0)):
        a = np.abs(d)
        out[name] = {"rms": float(np.sqrt((d ** 2).mean())),
                     "median": float(np.median(a)), "max": float(a.max()),
                     "within50": int((a < 50).sum())}
    out["n"] = len(t)
    return out


def _payload(al: Alignment, seg_a: Segment, seg_b: Segment, st) -> dict:
    w, c = al.warp, al.correspondence
    a_start = seg_a.events[0].t
    b_start = seg_b.events[0].t
    # phi's domain is the matched core, not the whole segment: outside it the
    # two performances are not playing the same music.
    t_origin = float(c.t[0])                       # seconds into A (shifted)
    s_origin = float(c.s[0])                       # seconds into B (shifted)

    a_lo, a_hi = a_start, a_start + seg_a.duration + TAIL_S
    b_lo = b_start + s_origin
    b_hi = b_start + s_origin + w.T2 + RUNOUT_S

    na, pa = _notes(st, a_lo, a_hi), _pedal(st, a_lo, a_hi)
    nb, pb = _notes(st, b_lo, b_hi), _pedal(st, b_lo, b_hi)

    return {
        "T1": round(w.T1, 4), "T2": round(w.T2, 4),
        "aOrigin": round(t_origin, 4), "bOrigin": round(s_origin, 4),
        "knots": [round(float(x), 5) for x in w.knots],
        "values": [round(float(x), 5) for x in w.values],
        "A": _track(na, pa, a_start),
        "B": _track(nb, pb, b_start),
        "W": _track(nb, pb, b_start, warp=w, t_origin=t_origin,
                    s_origin=s_origin),
        "nA": len(na), "nB": len(nb),
    }


# ---------------------------------------------------------------------------
# geometry, shared by the SVG generator here and the playhead in the browser
# ---------------------------------------------------------------------------

GEO = {"tempo": {"w": 980, "h": 380, "pad": 56},
       "phi": {"w": 560, "h": 560, "pad": 52}}


def _plot_tempo(T, lg, tA, top) -> str:
    g = GEO["tempo"]
    w, h, pad = g["w"], g["h"], g["pad"]
    lo, hi = min(lg) - .08, max(lg) + .08
    g["lo"], g["hi"] = round(lo, 6), round(hi, 6)
    sx = lambda t: pad + t * (w - 2 * pad)
    sy = lambda v: h - pad - (v - lo) / (hi - lo) * (h - 2 * pad)
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
         f'aria-label="log tempo ratio over the performance">']
    o.append(f'<line x1="{pad}" y1="{sy(0):.1f}" x2="{w-pad}" y2="{sy(0):.1f}" '
             f'stroke="#5b6472" stroke-width="1.5" stroke-dasharray="5 4"/>')
    o.append(f'<rect x="{pad}" y="{pad}" width="{w - 2 * pad}" '
             f'height="{h - 2 * pad}" fill="none" stroke="#2a323d"/>')
    d = []
    for i, v in enumerate(lg):
        x0, x1, y = sx(T[i]), sx(T[i + 1]), sy(v)
        d.append(f'{"M" if i == 0 else "L"}{x0:.2f},{y:.2f}L{x1:.2f},{y:.2f}')
    o.append(f'<path d="{"".join(d)}" fill="none" stroke="#8ab4f8" stroke-width="2.4"/>')
    for _, i, dv in top[:6]:
        x = sx(T[i + 1])
        col = '#f0b45e' if dv > 0 else '#e08a8a'
        o.append(f'<line x1="{x:.2f}" y1="{pad}" x2="{x:.2f}" y2="{h-pad}" '
                 f'stroke="{col}" stroke-width="1" opacity=".55"/>')
        o.append(f'<text x="{x:.2f}" y="{pad-8}" fill="{col}" font-size="11" '
                 f'text-anchor="middle">{tA[i+1]:.1f}s</text>')
    for lab, v in [('2×', math.log(2)), ('1.5×', math.log(1.5)), ('same', 0.0),
                   ('0.75×', math.log(.75)), ('0.5×', math.log(.5))]:
        if lo < v < hi:
            o.append(f'<text x="{pad-10}" y="{sy(v)+4:.1f}" fill="#6e7d8c" font-size="11" '
                     f'text-anchor="end">{lab}</text>')
    o.append(f'<text x="{w/2}" y="{h-14}" fill="#9aa7b4" font-size="13" '
             f'text-anchor="middle">normalised time through the false start</text>')
    o.append(f'<text x="16" y="{h/2}" fill="#9aa7b4" font-size="13" text-anchor="middle" '
             f'transform="rotate(-90 16 {h/2})">log φ′ — local tempo ratio</text>')
    # the playhead, parked and invisible until something plays
    o.append(f'<g id="ph-tempo" opacity="0">'
             f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" '
             f'stroke="#7ee0d6" stroke-width="2"/>'
             f'<circle cx="{pad}" cy="{pad}" r="4.5" fill="#7ee0d6"/></g>')
    o.append('</svg>')
    return ''.join(o)


def _plot_phi(T, S, pairs) -> str:
    g = GEO["phi"]
    w, h, pad = g["w"], g["h"], g["pad"]
    sx = lambda t: pad + t * (w - 2 * pad)
    sy = lambda v: h - pad - v * (h - 2 * pad)
    pts = ' '.join(f'{sx(t):.2f},{sy(v):.2f}' for t, v in zip(T, S))
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
         f'aria-label="warp function against the diagonal">']
    o.append(f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(1)}" '
             f'stroke="#5b6472" stroke-width="1.5" stroke-dasharray="5 4"/>')
    o.append(f'<rect x="{pad}" y="{pad}" width="{w - 2 * pad}" '
             f'height="{h - 2 * pad}" fill="none" stroke="#2a323d"/>')
    o.append(f'<polyline points="{pts}" fill="none" stroke="#7ee0d6" stroke-width="2.5"/>')
    for t, v in zip(T, S):
        o.append(f'<circle cx="{sx(t):.2f}" cy="{sy(v):.2f}" r="2.6" fill="#7ee0d6" opacity=".85"/>')
    o.append(f'<text x="{w/2}" y="{h-14}" fill="#9aa7b4" font-size="13" '
             f'text-anchor="middle">false start, normalised time</text>')
    o.append(f'<text x="16" y="{h/2}" fill="#9aa7b4" font-size="13" text-anchor="middle" '
             f'transform="rotate(-90 16 {h/2})">full performance, normalised time</text>')
    o.append(f'<text x="{sx(.62):.0f}" y="{sy(.52):.0f}" fill="#5b6472" font-size="12" '
             f'transform="rotate(-45 {sx(.62):.0f} {sy(.52):.0f})">identity</text>')
    o.append(f'<g id="ph-phi" opacity="0">'
             f'<line id="ph-phi-v" x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" '
             f'stroke="#7ee0d6" stroke-width="1" opacity=".45"/>'
             f'<line id="ph-phi-h" x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" '
             f'stroke="#7ee0d6" stroke-width="1" opacity=".45"/>'
             f'<circle id="ph-phi-d" cx="{pad}" cy="{h-pad}" r="5.5" fill="#7ee0d6"/></g>')
    o.append('</svg>')
    return ''.join(o)


# ---------------------------------------------------------------------------
# style and behaviour
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#0e1116;--panel:#161b22;--line:#2a323d;--ink:#e6edf3;--dim:#9aa7b4;
--dim2:#6e7d8c;--acc:#7ee0d6;--acc2:#8ab4f8;--warn:#f0b45e;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
line-height:1.6;font-size:15.5px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1060px;margin:0 auto;padding:44px 22px 100px}
h1{font-size:29px;margin:0 0 6px;letter-spacing:-.02em}
.sub{color:var(--dim);margin:0 0 30px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.11em;color:var(--dim2);
margin:44px 0 14px;font-weight:600}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:26px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.stat .v{font-size:31px;font-weight:650;letter-spacing:-.02em;font-family:var(--mono)}
.stat .k{color:var(--dim);font-size:12.5px;margin-top:5px}
.big .v{color:var(--acc)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:20px;margin:16px 0}
.cap{color:var(--dim);font-size:13.5px;margin-top:10px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim2);font-weight:600;font-size:12px;text-transform:uppercase;
letter-spacing:.06em}
td.n{font-family:var(--mono)}
.up{color:var(--warn)} .dn{color:#e08a8a}
math{font-size:1.08em}
.eq{margin:16px 0;text-align:center}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:840px){.two{grid-template-columns:1fr}}
.warnbox{border-color:#5a4426;background:#1a1610}
code{font-family:var(--mono);font-size:.92em;background:#1c232c;padding:1px 5px;
border-radius:4px}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);
color:var(--dim2);font-size:12.5px}
.scrollx{overflow-x:auto;-webkit-overflow-scrolling:touch}

/* transport */
.tp{display:flex;flex-wrap:wrap;gap:9px;margin:4px 0 2px}
.tp button{font:inherit;font-size:13.5px;color:var(--ink);background:#1c232c;
border:1px solid var(--line);border-radius:9px;padding:9px 14px;cursor:pointer;
display:inline-flex;align-items:center;gap:9px;transition:border-color .12s,background .12s}
.tp button:hover{background:#222b36;border-color:#3b4552}
.tp button:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.tp button[aria-pressed="true"]{border-color:var(--acc);background:#15282a;color:#d7fbf6}
.tp button.hero-btn{background:#14262b;border-color:#2f6f6a}
.tp button.hero-btn:hover{background:#173038}
.tp button.stop{margin-left:auto;color:var(--dim)}
.tp .ch{font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;
color:var(--dim2);border:1px solid var(--line);border-radius:4px;padding:1px 4px}
.tp button[aria-pressed="true"] .ch{color:var(--acc);border-color:#2f6f6a}
.bar{height:4px;border-radius:3px;background:#1c232c;margin:14px 0 4px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:var(--acc);transition:width .08s linear}
.status{font-family:var(--mono);font-size:12px;color:var(--dim2);margin-top:8px;
display:flex;flex-wrap:wrap;gap:6px 18px}
.status b{color:var(--dim);font-weight:600}
.legend2{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:12.5px;color:var(--dim);
margin-top:12px}
.legend2 i{display:inline-block;width:10px;height:10px;border-radius:3px;
margin-right:7px;vertical-align:-1px}
"""

JS = r"""
/* ======================================================================
   AUDIO. Everything here is synthesised from the note events of his own
   take, inlined above -- nothing is fetched, and no audio file is embedded,
   so the page stays small and works with the network off.

   The voice is a struck string: six to ten inharmonic partials, each with
   its own decay, plus a short filtered noise burst for the hammer. The
   damper is applied at the note's *effective* release -- the note-off,
   pushed out to the moment the sustain pedal next comes up. Skip that and
   a nocturne comes back staccato, because he holds the sound with his foot.
   ====================================================================== */
const D = JSON.parse(document.getElementById("warpdata").textContent);
const clamp = (x,a,b) => x<a?a:x>b?b:x;

const AU = {ctx:null, graph:null, live:[], parts:[], playing:false,
            t0:0, span:0, clock:"A", mode:null, timer:null, osc:0};
/* The scheduling horizon is deliberately wider than it needs to be: Chrome
   throttles setInterval in a tab that is not visible, down to about once a
   second, and everything inside the horizon is already on the sample-accurate
   audio clock, which is not throttled. A slow pump costs nothing. */
const LOOKAHEAD = 2.5, TICK = 200, MAX_PEDAL_S = 8;

function mkVoice(ctx, bus, pan, rollBias){
  const g = ctx.createGain(); g.gain.value = 1;
  let out = g;
  if(ctx.createStereoPanner){
    const p = ctx.createStereoPanner(); p.pan.value = pan;
    g.connect(p); out = p;
  }
  out.connect(bus);
  return {bus:g, rollBias:rollBias};
}

/* One graph builder, used by the live context and by the offline render, so
   what gets measured is the thing that gets heard. */
function buildGraph(ctx, dest){
  const bus = ctx.createGain(); bus.gain.value = 0.10;
  const lim = ctx.createDynamicsCompressor();
  lim.threshold.value=-8; lim.knee.value=6; lim.ratio.value=12;
  lim.attack.value=0.003; lim.release.value=0.25;
  const master = ctx.createGain(); master.gain.value = 1.0;
  bus.connect(lim); lim.connect(master); master.connect(dest);
  return {bus:bus, master:master,
          voices:{A: mkVoice(ctx,bus,-0.72,0.0), B: mkVoice(ctx,bus,0.72,0.30)}};
}

function initAudio(){
  if(AU.ctx) return AU.ctx;
  AU.ctx = new (window.AudioContext||window.webkitAudioContext)({latencyHint:"interactive"});
  AU.graph = buildGraph(AU.ctx, AU.ctx.destination);
  AU.analyser = AU.ctx.createAnalyser(); AU.analyser.fftSize = 2048;
  AU.bins = new Float32Array(AU.analyser.fftSize);
  AU.graph.master.connect(AU.analyser);
  return AU.ctx;
}

function noiseBuf(ctx){
  if(ctx._noise) return ctx._noise;
  const n = Math.floor(ctx.sampleRate*0.5);
  const b = ctx.createBuffer(1,n,ctx.sampleRate);
  const d = b.getChannelData(0);
  for(let i=0;i<n;i++) d[i] = Math.random()*2-1;
  ctx._noise = b; return b;
}

/* One struck string into voice V. rollBias dulls the partial rolloff, which
   is how the two performances stay tellable apart when they play together
   even on a mono speaker where the panning does nothing. */
function strike(ctx, V, t, midi, vel, relS){
  const f0 = 440*Math.pow(2,(midi-69)/12);
  const vn = clamp(vel/127, 0.02, 1), amp = Math.pow(vn, 1.6);
  const nyq = ctx.sampleRate*0.47;
  const B = 0.00045;                                  // inharmonicity
  const N = clamp(Math.round(13 - midi/11), 3, 9);
  const roll = 1.25 - 0.45*(vn-0.5) + V.rollBias;     // harder = brighter
  const base = 3.1*Math.pow(2, -(midi-60)/26);        // bass rings longer
  const vg = ctx.createGain(); vg.gain.value = 1; vg.connect(V.bus);

  let norm = 0; const parts = [];
  for(let n=1;n<=N;n++){
    const fn = n*f0*Math.sqrt(1+B*n*n);
    if(fn >= nyq) break;
    norm += Math.pow(n,-roll); parts.push([n,fn]);
  }
  if(norm<=0) norm=1;
  let endT = t; const nodes = [];
  for(const pr of parts){
    const n = pr[0], fn = pr[1];
    const an = amp*Math.pow(n,-roll)/norm;
    const tau = Math.max(0.03, base*Math.pow(n,-0.62));
    const o = ctx.createOscillator(); o.type="sine"; o.frequency.value = fn;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(an, t+0.004);
    g.gain.setTargetAtTime(0, t+0.004, tau);
    g.gain.setTargetAtTime(0, t+Math.max(0.05, relS), 0.085);   // the damper
    o.connect(g); g.connect(vg);
    const stop = t + Math.max(0.05, relS) + 0.55;
    o.start(t); o.stop(stop);
    if(stop>endT) endT = stop;
    nodes.push(o); AU.osc++;
  }
  /* the hammer: most of what makes a synthesised attack read as a piano
     rather than as an organ. */
  const src = ctx.createBufferSource(); src.buffer = noiseBuf(ctx);
  const bp = ctx.createBiquadFilter(); bp.type="bandpass";
  bp.frequency.value = clamp(f0*3.2, 60, nyq*0.9); bp.Q.value = 0.9;
  const ng = ctx.createGain();
  ng.gain.setValueAtTime(0,t);
  ng.gain.linearRampToValueAtTime(0.35*amp*vn, t+0.0012);
  ng.gain.setTargetAtTime(0, t+0.0012, 0.014);
  src.connect(bp); bp.connect(ng); ng.connect(vg);
  src.start(t); src.stop(t+0.2);
  AU.live.push({vg:vg, nodes:nodes, endT:endT});
}

/* Effective release per note: note-off, pushed out to the moment the pedal
   next comes up if the pedal was down when the finger left. */
function prepare(tr){
  if(tr._rel) return tr;
  const P = (tr.pedal||[]).slice().sort((a,b)=>a[0]-b[0]);
  const spans = []; let down = null;
  for(const pv of P){
    if(pv[1] >= 64 && down === null) down = pv[0];
    else if(pv[1] < 64 && down !== null){ spans.push([down, pv[0]]); down = null; }
  }
  if(down !== null) spans.push([down, Infinity]);
  tr._rel = tr.events.map(function(e){
    const off = e[0] + Math.max(0, e[3]);
    let rel = off;
    for(const ab of spans){
      if(off >= ab[0] && off <= ab[1]){ rel = Math.min(ab[1], off + MAX_PEDAL_S*1000); break; }
    }
    return Math.max(0.06, (rel - e[0])/1000);
  });
  return tr;
}

/* ----------------------------------------------------------------------
   Programmes. `shift` is a rigid translation in ms, used only by the
   control: B started at the same moment as A and otherwise left alone.
   ---------------------------------------------------------------------- */
const RIGID_MS = (D.aOrigin - D.bOrigin) * 1000;
const PROG = {
  a:     {clock:"A", parts:[{t:"A", v:"A", shift:0}]},
  b:     {clock:"B", parts:[{t:"B", v:"B", shift:0}]},
  w:     {clock:"A", parts:[{t:"W", v:"B", shift:0}]},
  both:  {clock:"A", parts:[{t:"A", v:"A", shift:0}, {t:"W", v:"B", shift:0}]},
  rigid: {clock:"A", parts:[{t:"A", v:"A", shift:0}, {t:"B", v:"B", shift:RIGID_MS}]}
};

function stopAudio(){
  if(AU.ctx){
    const now = AU.ctx.currentTime;
    for(const v of AU.live){
      try{
        v.vg.gain.cancelScheduledValues(now);
        v.vg.gain.setTargetAtTime(0, now, 0.015);
        for(const o of v.nodes){ try{ o.stop(now+0.08); }catch(e){} }
      }catch(e){}
    }
  }
  AU.live = []; AU.osc = 0; AU.playing = false; AU.mode = null; AU.parts = [];
  if(AU.timer){ clearInterval(AU.timer); AU.timer = null; }
  paint();
}

function play(mode){
  const p = PROG[mode]; if(!p) return;
  const was = AU.mode;
  initAudio();
  stopAudio();
  if(was === mode) return;                     // second click = stop
  if(AU.ctx.state === "suspended") AU.ctx.resume();
  AU.parts = p.parts.map(function(q){
    const tr = prepare(D[q.t]);
    return {ev:tr.events, rel:tr._rel, V:AU.graph.voices[q.v], shift:q.shift, i:0};
  });
  AU.span = Math.max.apply(null, p.parts.map(q => D[q.t].span + q.shift/1000));
  AU.clock = p.clock;
  AU.mode = mode;
  AU.t0 = AU.ctx.currentTime + 0.12;
  AU.playing = true;
  AU.timer = setInterval(pump, TICK);
  pump(); paint();
}

function pump(){
  if(!AU.playing) return;
  const now = AU.ctx.currentTime, horizon = now + LOOKAHEAD;
  let done = true;
  for(const P of AU.parts){
    while(P.i < P.ev.length){
      const e = P.ev[P.i];
      const when = AU.t0 + (e[0] + P.shift)/1000;
      if(when > horizon) break;
      strike(AU.ctx, P.V, Math.max(when, now), e[1], e[2], P.rel[P.i]);
      P.i++;
    }
    if(P.i < P.ev.length) done = false;
  }
  AU.live = AU.live.filter(v => v.endT > now - 0.2);
  if(done && elapsed() > AU.span) stopAudio();
}

const elapsed = () => AU.playing ? (AU.ctx.currentTime - AU.t0) : 0;

/* ======================================================================
   PLAYHEAD. phi is piecewise linear on the matched onsets, so its inverse
   is too, and both are one interpolation.
   ====================================================================== */
function interp(x, xs, ys){
  if(x <= xs[0]) return ys[0];
  const n = xs.length;
  if(x >= xs[n-1]) return ys[n-1];
  let lo = 0, hi = n-1;
  while(hi - lo > 1){ const m = (lo+hi)>>1; if(xs[m] <= x) lo = m; else hi = m; }
  const f = (x - xs[lo])/(xs[hi] - xs[lo]);
  return ys[lo] + f*(ys[hi] - ys[lo]);
}
const phi    = t => interp(t, D.knots, D.values);
const phiInv = s => interp(s, D.values, D.knots);

/* Seconds into A, whatever is playing: the one clock both plots live on. */
function headA(){
  const e = elapsed();
  return AU.clock === "B" ? phiInv(e - D.bOrigin) + D.aOrigin : e;
}

const G = JSON.parse(document.getElementById("geo").textContent);
const gT = document.getElementById("ph-tempo");
const gP = document.getElementById("ph-phi");

function frame(){
  requestAnimationFrame(frame);
  if(!AU.playing){ gT.setAttribute("opacity","0"); gP.setAttribute("opacity","0"); return; }
  const tA = headA();
  const tau = (tA - D.aOrigin)/D.T1;
  const inside = tau >= 0 && tau <= 1;
  const u = clamp(tau, 0, 1);
  const op = inside ? "1" : "0.28";
  gT.setAttribute("opacity", op); gP.setAttribute("opacity", op);

  const t = G.tempo, x = t.pad + u*(t.w - 2*t.pad);
  const lgv = Math.log(interpSlope(u));
  const y = t.h - t.pad - (lgv - t.lo)/(t.hi - t.lo)*(t.h - 2*t.pad);
  const tl = gT.firstChild, tc = tl.nextSibling;
  tl.setAttribute("x1", x); tl.setAttribute("x2", x);
  tc.setAttribute("cx", x); tc.setAttribute("cy", y);

  const p = G.phi;
  const px = p.pad + u*(p.w - 2*p.pad);
  const pyv = phi(u*D.T1)/D.T2;
  const py = p.h - p.pad - pyv*(p.h - 2*p.pad);
  const vl = document.getElementById("ph-phi-v");
  const hl = document.getElementById("ph-phi-h");
  const dt = document.getElementById("ph-phi-d");
  vl.setAttribute("x1", px); vl.setAttribute("x2", px);
  vl.setAttribute("y1", py);
  hl.setAttribute("y1", py); hl.setAttribute("y2", py);
  hl.setAttribute("x2", px);
  dt.setAttribute("cx", px); dt.setAttribute("cy", py);

  const bar = document.getElementById("bar");
  bar.style.width = (100*clamp(elapsed()/Math.max(AU.span,0.001),0,1)) + "%";
  document.getElementById("clk").textContent =
    elapsed().toFixed(1) + " s   ·   φ′ = ×" + interpSlope(u).toFixed(3);
}

/* phi' at normalised time u, as a ratio of *normalised* clocks -- the same
   quantity the tempo plot draws. */
function interpSlope(u){
  const t = u*D.T1, K = D.knots, V = D.values;
  let i = 0;
  while(i < K.length-2 && K[i+1] <= t) i++;
  return ((V[i+1]-V[i])/(K[i+1]-K[i])) * (D.T1/D.T2);
}

/* ======================================================================
   CHROME
   ====================================================================== */
function paint(){
  for(const b of document.querySelectorAll(".tp button[data-mode]")){
    b.setAttribute("aria-pressed", String(b.dataset.mode === AU.mode));
  }
  const s = document.getElementById("astate");
  s.textContent = AU.ctx ? AU.ctx.state : "not started — press a button";
  if(!AU.playing){
    document.getElementById("bar").style.width = "0%";
    document.getElementById("clk").textContent = "—";
  }
}

document.addEventListener("click", function(e){
  const b = e.target.closest("button[data-mode], button[data-stop]");
  if(!b) return;
  if(b.dataset.stop !== undefined) stopAudio(); else play(b.dataset.mode);
});
/* Web Audio needs a gesture. Any pointer or key on the page unsticks a
   context that started suspended, so the transport never sits there dead. */
function unstick(){ if(AU.ctx && AU.ctx.state === "suspended") AU.ctx.resume().then(paint); }
document.addEventListener("pointerdown", unstick);
document.addEventListener("keydown", function(e){
  if(e.key === " "){ e.preventDefault(); AU.mode ? stopAudio() : play("both"); }
  unstick();
});
requestAnimationFrame(frame);
paint();

/* ======================================================================
   PROOF. A live AnalyserNode only tells you the graph is loud *now*, and a
   headless browser -- which has no audio clock at all -- reads exactly zero
   however healthy the graph is. This is the deterministic version of the
   same question: no wall clock, no scheduler, just the samples.
   ====================================================================== */
async function renderOffline(mode, sec){
  sec = sec || 8;
  const p = PROG[mode || "both"];
  const off = new OfflineAudioContext(2, Math.ceil(44100*sec), 44100);
  const g = buildGraph(off, off.destination);
  const keepLive = AU.live; AU.live = [];
  try{
    for(const q of p.parts){
      const tr = prepare(D[q.t]);
      for(let i=0;i<tr.events.length;i++){
        const t = (tr.events[i][0] + q.shift)/1000;
        if(t > sec-0.2) break;
        if(t < 0) continue;
        strike(off, g.voices[q.v], t, tr.events[i][1], tr.events[i][2], tr._rel[i]);
      }
    }
    const buf = await off.startRendering();
    const out = {mode:mode||"both", seconds:sec, samples:buf.length, channels:[]};
    for(let c=0;c<buf.numberOfChannels;c++){
      const d = buf.getChannelData(c);
      let peak=0, sum=0;
      for(let i=0;i<d.length;i++){ const a=Math.abs(d[i]); if(a>peak) peak=a; sum+=d[i]*d[i]; }
      out.channels.push({peak:+peak.toFixed(5), rms:+Math.sqrt(sum/d.length).toFixed(5)});
    }
    return out;
  } finally { AU.live = keepLive; }
}

/* a handle for driving this from the console, which is how it was verified */
window.WARP = {
  D:D, AU:AU, play:play, stop:stopAudio, renderOffline:renderOffline,
  phi:phi, phiInv:phiInv, headA:headA, elapsed:elapsed,
  get audio(){ return AU.ctx ? {state:AU.ctx.state, mode:AU.mode,
                                voices:AU.live.length, oscillators:AU.osc,
                                t:AU.ctx.currentTime} : "no context"; },
  rms(){ if(!AU.analyser) return 0; AU.analyser.getFloatTimeDomainData(AU.bins);
         let s=0; for(let i=0;i<AU.bins.length;i++) s+=AU.bins[i]*AU.bins[i];
         return Math.sqrt(s/AU.bins.length); }
};
"""


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def render(al: Alignment, seg_a: Segment, seg_b: Segment, st) -> str:
    w, c = al.warp, al.correspondence
    cen = c.census()

    T = [float(x) / w.T1 for x in w.knots]
    S = [float(x) / w.T2 for x in w.values]
    slope = [float(x) * w.T1 / w.T2 for x in w.slopes]
    tA = [float(x) for x in w.knots]
    lg = [math.log(x) for x in slope]

    gaps = np.diff(np.asarray(T))
    sl = np.asarray(slope)
    unorm = math.sqrt(float(np.sum((sl - 1.0) ** 2 * gaps)))
    fr = math.degrees(math.acos(min(1.0, float(np.sum(np.sqrt(sl) * gaps)))))

    nd, na = cen["dropped_in_core_a"], cen["added_in_core_b"]
    pairs, nA = len(c.pairs), cen["events_a"]
    durA, durB = w.T1, w.T2

    # kinks: jumps in log phi' between adjacent constant pieces
    jumps = [(abs(lg[i + 1] - lg[i]), i, lg[i + 1] - lg[i])
             for i in range(len(lg) - 1)]
    jumps.sort(reverse=True)
    top = jumps[:8]

    rows = ''.join(
        f'<tr><td class="n">{tA[i+1]:.1f} s</td><td class="n">{T[i+1]:.3f}</td>'
        f'<td class="n {"up" if dv>0 else "dn"}">{dv:+.3f}</td>'
        f'<td class="n {"up" if dv>0 else "dn"}">{math.exp(dv):.2f}×</td></tr>'
        for _, i, dv in top)

    tempo_svg = _plot_tempo(T, lg, tA, top)     # sets GEO["tempo"] lo/hi
    phi_svg = _plot_phi(T, S, pairs)
    data = _payload(al, seg_a, seg_b, st)
    ls = listen_stats(al)
    lw, lr = ls["warped"], ls["rigid"]

    warped_first = data["W"]["events"][0][0] / 1000.0 if data["W"]["events"] else 0.0

    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rubato as a Warp</title>
<style>{CSS}</style>
<div class="wrap">
<h1>Rubato as a warp</h1>
<p class="sub">Chopin, Nocturne in F minor, Op. 55 No. 1 — your false start against the
opening of the complete performance, 19 August 2026.</p>

<div class="hero">
  <div class="stat big"><div class="v">{fr:.2f}°</div>
    <div class="k">Fisher–Rao angle from identity (max 90°)</div></div>
  <div class="stat"><div class="v">{unorm:.3f}</div>
    <div class="k">‖u′‖<sub>L²</sub> — displacement energy</div></div>
  <div class="stat"><div class="v">{min(slope):.2f}–{max(slope):.2f}×</div>
    <div class="k">local tempo range</div></div>
  <div class="stat"><div class="v">{pairs}</div>
    <div class="k">matched onsets ({nd} dropped, {na} added)</div></div>
</div>

<div class="card">
<p><b>The finding.</b> Locally your tempo swings by a factor of two — from
{min(slope):.2f}× to {max(slope):.2f}× of uniform. Globally the two performances sit
<b>{fr:.2f} degrees apart</b> on a scale where orthogonal is ninety. You do not play
it the same way twice in any local sense, and you play it almost identically in the
global one. The rubato is not noise; it is reproducible structure.</p>
</div>

<h2>Hear it</h2>
<div class="card">
<p><b>Play the last button.</b> It runs the false start and the complete performance
together, the second one pushed through <math><msup><mi>φ</mi><mrow><mo>−</mo><mn>1</mn></mrow></msup></math>
so that both are on the false start's clock. If φ is right they lock. The control above
it starts the same two takes together and warps neither, and by the end they are about
{abs(durB - durA):.1f} s apart.</p>
<div class="tp">
  <button data-mode="a"><span class="ch">L</span>A — false start, as played</button>
  <button data-mode="b"><span class="ch">R</span>B — full performance, matching span</button>
  <button data-mode="w"><span class="ch">R</span>B warped onto A's clock</button>
  <button data-mode="rigid"><span class="ch">L+R</span>A + B, no warp <em>(control)</em></button>
  <button data-mode="both" class="hero-btn"><span class="ch">L+R</span><b>A + warped B, together</b></button>
  <button data-stop class="stop">Stop</button>
</div>
<div class="bar"><i id="bar"></i></div>
<div class="status">
  <span><b>audio</b> <span id="astate">not started</span></span>
  <span><b>clock</b> <span id="clk">—</span></span>
  <span>{data['nA']} + {data['nB']} notes, synthesised — no audio file is embedded</span>
</div>
<div class="legend2">
  <span><i style="background:#7ee0d6"></i>A, the false start — left channel, brighter</span>
  <span><i style="background:#8ab4f8"></i>B, the complete performance — right channel, duller</span>
</div>
<p class="cap">Both voices are computed in the browser from the take's own note-on times,
velocities and CC64 pedal — the same events the warp was fitted to, so what you hear and
what the plots draw cannot drift apart. The playhead below tracks whichever transport is
running; when a phrase stretches, the marker is sitting on the kink that caused it. The
warped take enters at <b>{warped_first:.1f} s</b>, because the first {warped_first:.1f} s
of the false start found no partner in the complete performance. Space bar plays and
stops.</p>
<p class="cap"><b>What it should sound like, measured rather than asserted.</b> Across the
{ls['n']} matched onsets the warped take strikes a median <b>{lw['median']:.0f} ms</b> from
its partner in the false start — {lw['within50']} of {ls['n']} inside 50 ms, RMS
{lw['rms']:.0f} ms, worst {lw['max']:.0f} ms. The control's median is
<b>{lr['median']:.0f} ms</b>, with only {lr['within50']} of {ls['n']} inside 50 ms and a
worst case of {lr['max']/1000:.1f} s. So expect close, not machine-tight: at
{lw['rms']:.0f} ms RMS the warped pair is about {lw['rms']/1000/0.005:.0f} times the
transport lattice apart, and that gap is expressive difference the fit did not remove,
not clock error.</p>
</div>

<h2>The tempo curve</h2>
<div class="card"><div class="scrollx">{tempo_svg}</div>
<p class="cap">log φ′ — how much faster or slower the second performance runs at each
moment. Flat at the dashed line would mean the two are related by a constant tempo
scaling. Piecewise constant because the minimiser of the H¹ energy is piecewise linear:
tempo changes at phrase boundaries and holds between them. Marked lines are the six
largest changes.</p></div>

<h2>The warp against the identity</h2>
<div class="two">
<div class="card"><div class="scrollx">{phi_svg}</div>
<p class="cap">φ maps normalised time in the false start to normalised time in the full
performance. The dashed diagonal is the identity — <em>played it exactly the same way</em>.
Dots are the {pairs} matched onsets.</p></div>
<div class="card">
<h3 style="margin-top:0;font-size:15px">Where the tempo turns</h3>
<div class="scrollx"><table><thead><tr><th>into take</th><th>norm.</th><th>Δ log φ′</th><th>ratio</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="cap">They alternate — slow, fast, slow, fast — at intervals of roughly 2.5 to 4
seconds. That spacing is phrase-length, and the alternation looks like taking time at
phrase ends and pushing through the middles. <b>Unverified against the score.</b></p>
</div></div>

<h2>The formulation</h2>
<div class="card">
<p>Normalise both performances to <math><mo>[</mo><mn>0</mn><mo>,</mo><mn>1</mn><mo>]</mo></math>,
so the identity exists in the space. Write
<math><mi>φ</mi><mo>=</mo><mi>id</mi><mo>+</mo><mi>u</mi></math> with
<math><mi>u</mi><mo>(</mo><mn>0</mn><mo>)</mo><mo>=</mo><mi>u</mi><mo>(</mo><mn>1</mn><mo>)</mo><mo>=</mo><mn>0</mn></math>.
Then <math><msup><mi>φ</mi><mo>′</mo></msup><mo>=</mo><mn>1</mn><mo>+</mo><msup><mi>u</mi><mo>′</mo></msup></math>,
the homeomorphism condition is exactly
<math><msup><mi>u</mi><mo>′</mo></msup><mo>&gt;</mo><mo>−</mo><mn>1</mn></math>, and we minimise</p>
<div class="eq"><math display="block"><mrow>
<mi>E</mi><mo>[</mo><mi>u</mi><mo>]</mo><mo>=</mo>
<munder><mo>∑</mo><mi>i</mi></munder>
<msup><mrow><mo>(</mo><msub><mi>t</mi><mi>i</mi></msub><mo>+</mo><mi>u</mi>
<mo>(</mo><msub><mi>t</mi><mi>i</mi></msub><mo>)</mo><mo>−</mo>
<msub><mi>s</mi><mi>i</mi></msub><mo>)</mo></mrow><mn>2</mn></msup>
<mo>+</mo><mi>λ</mi><msubsup><mo>∫</mo><mn>0</mn><mn>1</mn></msubsup>
<msup><mrow><mo>|</mo><msup><mi>u</mi><mo>′</mo></msup><mo>|</mo></mrow><mn>2</mn></msup>
</mrow></math></div>
<p>over <math><msubsup><mi>H</mi><mn>0</mn><mn>1</mn></msubsup></math>. Because the endpoint
constraint fixes <math><msubsup><mo>∫</mo><mn>0</mn><mn>1</mn></msubsup><msup><mi>φ</mi><mo>′</mo></msup><mo>=</mo><mn>1</mn></math>,
completing the square gives
<math><msubsup><mo>∫</mo><mn>0</mn><mn>1</mn></msubsup><msup><mrow><mo>|</mo><msup><mi>φ</mi><mo>′</mo></msup><mo>|</mo></mrow><mn>2</mn></msup><mo>=</mo><mn>1</mn><mo>+</mo><msubsup><mrow><mo>∥</mo><msup><mi>φ</mi><mo>′</mo></msup><mo>−</mo><mn>1</mn><mo>∥</mo></mrow><mrow><msup><mi>L</mi><mn>2</mn></msup></mrow><mn>2</mn></msubsup></math> —
so the penalty is <b>exactly the variance of tempo about its own mean</b>. Minimising it
is minimising rubato in the plainest possible sense, and the identity is its unique zero.</p>
<p>Euler–Lagrange gives <math><msup><mi>u</mi><mo>″</mo></msup><mo>=</mo><mn>0</mn></math>
away from the knots, so the minimiser is piecewise linear with corners at matched onsets.
The corners are kept deliberately: a pianist changes tempo abruptly at phrase boundaries,
and an H² penalty would erase exactly the feature worth seeing.</p>
<p>The <b>Fisher–Rao angle</b> is the better cross-piece statistic. The
<math><msup><mi>Ḣ</mi><mn>1</mn></msup></math> metric on Diff⁺([0,1]) <em>is</em> Fisher–Rao
up to a factor of ¼, with the square-root velocity map
<math><mi>ψ</mi><mo>=</mo><msqrt><msup><mi>φ</mi><mo>′</mo></msup></msqrt></math> as the
isometry, so distance is arc length on a sphere:
<math><mi>d</mi><mo>=</mo><mi>arccos</mi><mo>⟨</mo><msqrt><msup><msub><mi>φ</mi><mn>1</mn></msub><mo>′</mo></msup></msqrt><mo>,</mo><msqrt><msup><msub><mi>φ</mi><mn>2</mn></msub><mo>′</mo></msup></msqrt><mo>⟩</mo></math>.
It is dimensionless, bounded by 90°, and comparable across pieces of different lengths —
which a residual in seconds is not.</p>
<p>The <b>warped playback</b> applies the same φ, inverted. A note sounding at
<math><mi>s</mi></math> seconds into the complete performance is struck at
<math><msup><mi>φ</mi><mrow><mo>−</mo><mn>1</mn></mrow></msup><mo>(</mo><mi>s</mi><mo>)</mo></math>
seconds into the false start's clock, and its duration is warped with it —
<math><msup><mi>φ</mi><mrow><mo>−</mo><mn>1</mn></mrow></msup></math> exists and is
continuous precisely because <math><msup><mi>φ</mi><mo>′</mo></msup><mo>≥</mo><mi>δ</mi><mo>&gt;</mo><mn>0</mn></math>
was imposed as a constraint rather than hoped for. What you hear is therefore a test of
the fit and not a rendering of it: nothing in the audio path was tuned to make the two
takes agree.</p>
</div>

<h2>What to distrust</h2>
<div class="card warnbox">
<p><b>{nd} onsets in the false start found no partner, and {na} appeared
only in the full performance.</b> Of {nA} onset clusters, {pairs} matched. A
third of the false start is unaccounted for — ornaments taken differently, or notes simply
not played the same. The warp describes the matched skeleton, not the whole performance.</p>
<p>λ landed at <code>{al.lam:.3f}</code> by generalised cross-validation, giving
<code>{w.df:.1f}</code> effective degrees of freedom over {pairs} knots — the curve
is smoothed to about a third of the freedom the data would allow.</p>
<p>Timestamps sit on a <b>5.000 ms lattice</b> imposed by the Bluetooth MIDI link. No
claim here is finer than that. The constraint
<math><msup><mi>u</mi><mo>′</mo></msup><mo>&gt;</mo><mo>−</mo><mn>1</mn></math> never went
active, so φ is a genuine homeomorphism rather than one clipped into being.</p>
<p>Durations: false start {durA:.1f} s, matched span of the full performance {durB:.1f} s.
The phrase-boundary reading of the kinks is <b>a hypothesis, not a result</b> — it needs
checking against the score.</p>
<p><b>The two takes do not lock exactly, and you will hear that.</b> The warp removes
{100*al.explained:.0f}% of the straight line's timing discrepancy — {lr['rms']/1000:.2f} s
RMS down to {lw['rms']/1000:.3f} s — but {ls['n'] - lw['within50']} of the {ls['n']} matched
onsets still land more than 50 ms apart, and the worst is {lw['max']:.0f} ms. λ is doing
that deliberately: it is a smoothing parameter, and a warp that interpolated every onset
exactly would be a different, wigglier φ that explained nothing.</p>
<p><b>The playback is a synthesiser, not a recording.</b> The FP-30X sends MIDI, not
audio; there is no recording of these takes to embed. Timbre, dynamics and pedal are a
model of a piano driven by his measured note events, and it is the <em>timing</em> that is
his — the thing the whole page is about — not the sound.</p>
</div>

<footer>Generated 19 August 2026 by <code>python -m fp30x_studio.align</code> from
<code>fp30x_studio/align/</code> ·
<code>~/Music/FP-30X Studio/takes/2026-08-19-a.fp30</code>, segments {SEG_A} and {SEG_B} ·
self-contained, no network, no server.</footer>
</div>
<script type="application/json" id="warpdata">{json.dumps(data, separators=(',', ':'))}</script>
<script type="application/json" id="geo">{json.dumps(GEO, separators=(',', ':'))}</script>
<script>{JS}</script>
"""


def build(out: Path = OUT, take: Path = TAKE) -> Path:
    """Fit the warp and write the page. The whole deliverable, one call."""
    segs = load_segments(take)
    seg_a, seg_b = segs[SEG_A], segs[SEG_B]
    al = align_segments(seg_a, seg_b)
    st = _store(take)
    html = render(al, seg_a, seg_b, st)
    out = Path(out)
    out.write_text(html, encoding="utf-8")
    return out
