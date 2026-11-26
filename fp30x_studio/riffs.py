r"""Cut an improvisation into riffs of 1-8 bars, splicing to keep the pulse.

:mod:`fp30x_studio.transcribe` tries to notate *everything that was played*,
with one beat track through the whole take. For composing that is the wrong
contract: what is wanted is the idea, at a steady pulse, in a few bars. So this
module is allowed to delete.

Per riff window:

1. **Ghosts and flutters out.** Ghost notes (quiet and short) are dropped, and
   so is any onset landing less than ``FLUTTER`` of a pulse after the previous
   one -- the quieter of the two goes. That is a grace note or a slip, and
   keeping it forces a 32nd into the bar.
2. **Pulse** from the median inter-onset interval, then a **grid walk**: each
   interval is written as the nearest of 1/2, 1, 3/2, 2, 3, 4 pulses (halves
   cost a little extra, so a sloppy quarter is not written dotted), and the
   pulse follows the playing slowly, so rubato does not knock the grid off.
3. **Splice.** An interval longer than ``MAX_HOLD`` pulses is a hesitation, not
   a rest: it is cut down to ``MAX_HOLD``. That removes time, which is the
   point -- a repeating rhythm survives, the pause does not.
4. **Meter and downbeat** from where the bass and the accents land: 3 or 4
   pulses for a slow pulse, 6 or 8 for a fast one (written in eighths).
5. Anything longer than eight bars is cut at bar lines into several riffs.

Every deletion and splice is counted in the riff's report, so a cut can be
checked against the as-played audio.

    python -m fp30x_studio.riffs CLIP.mid --windows windows.json --out DIR
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

GHOST_VEL, GHOST_DUR, DEAD_VEL = 12, 0.25, 3
CHORD_TOL = 0.06          #: seconds; onsets this close are one chord
BRUSH = 0.35             #: in a chord, quieter than this share of the loudest goes
FLUTTER = 0.3             #: pulses; closer than this and the quieter onset goes
MAX_HOLD = 2.0            #: pulses; longer gaps are spliced down to this
STEPS = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
HALF_COST = 0.12          #: bias toward whole pulses
SPLIT = 55                #: below this MIDI pitch goes to the bass staff
MAX_BARS = 8


@dataclass
class N:
    t: float
    d: float
    p: int
    v: int


@dataclass
class Riff:
    name: str
    t0: float
    t1: float
    events: list            #: (pos_in_pulses, [N...])
    pulse_s: float
    fast: bool
    meter: int              #: pulses per bar
    shift: float            #: rest before the first onset, pulses
    bars: int
    report: dict = field(default_factory=dict)


def read_midi(path):
    import mido
    t, on, out, pedal = 0.0, {}, [], []
    for m in mido.MidiFile(path):
        t += m.time
        if m.type == "note_on" and m.velocity > 0:
            on[m.note] = (t, m.velocity)
        elif m.type in ("note_on", "note_off") and m.note in on:
            s, v = on.pop(m.note)
            out.append(N(s, t - s, m.note, v))
        elif m.type == "control_change" and m.control == 64:
            pedal.append((t, m.value))
    return sorted(out, key=lambda n: (n.t, n.p)), pedal


def chords(notes):
    out = []
    for n in notes:
        if out and n.t - out[-1][0] < CHORD_TOL:
            out[-1][1].append(n)
        else:
            out.append([n.t, [n]])
    return out


def splice(notes, t0, t1, *, pulse=None):
    """The grid walk. Returns (events, pulse, report)."""
    rep = {"ghosts": 0, "flutters": 0, "spliced_s": 0.0, "splices": 0}
    keep = []
    for n in notes:
        if not t0 <= n.t < t1:
            continue
        if n.v <= DEAD_VEL or (n.v <= GHOST_VEL and n.d < GHOST_DUR):
            rep["ghosts"] += 1
            continue
        keep.append(n)
    cs = chords(keep)
    # a key brushed alongside a much louder one is the hand, not the chord
    for c in cs:
        top = max(n.v for n in c[1])
        brushed = [n for n in c[1] if n.v < BRUSH * top]
        rep["ghosts"] += len(brushed)
        c[1] = [n for n in c[1] if n.v >= BRUSH * top]
    if not cs:
        return [], 0.5, rep
    iois = [b[0] - a[0] for a, b in zip(cs, cs[1:])]
    u = pulse or statistics.median([x for x in iois if 0.2 <= x <= 1.0] or [0.5])
    rep["pulse_in_s"] = round(u, 3)
    u0 = u

    # flutters: drop the quieter of two onsets closer than FLUTTER pulses
    loud = lambda c: max(n.v for n in c[1])
    i = 1
    while i < len(cs):
        if cs[i][0] - cs[i - 1][0] < FLUTTER * u:
            drop = i if loud(cs[i]) <= loud(cs[i - 1]) else i - 1
            rep["flutters"] += len(cs[drop][1])
            cs.pop(drop)
        else:
            i += 1

    events, pos, tracked = [(0.0, cs[0][1])], 0.0, []
    for a, b in zip(cs, cs[1:]):
        dt = b[0] - a[0]
        r = dt / u
        if r > MAX_HOLD + 0.5:
            k = MAX_HOLD
            rep["splices"] += 1
            rep["spliced_s"] += dt - k * u
        else:
            k = min(STEPS, key=lambda s: abs(r - s) / s + (HALF_COST if s % 1 else 0))
            if k in (1.0, 2.0) and abs(r - k) / k < 0.25:
                # follow the rubato slowly, and never far from where it began:
                # an unclamped tracker walks to double time through 3/2s.
                u = min(1.3 * u0, max(0.77 * u0, 0.8 * u + 0.2 * dt / k))
                tracked.append(u)
        pos += k
        events.append((pos, b[1]))
    rep["spliced_s"] = round(rep["spliced_s"], 2)
    u = statistics.median(tracked) if tracked else u
    return events, u, rep


def choose_meter(events, fast):
    """Pulses per bar and the downbeat phase, from bass and accent weight."""
    best = None
    for m, bias in ((6, 1.0), (8, 1.04)) if fast else ((3, 1.0), (4, 1.04)):
        for ph2 in range(0, 2 * m):
            ph = ph2 / 2
            s = 0.0
            for pos, ns in events:
                if (pos - ph) % m == 0:
                    s += sum(1.5 for n in ns if n.p < SPLIT) + max(n.v for n in ns) / 127
                elif (pos - ph) % (m / 2) == 0 and m % 2 == 0:
                    s += 0.4 * sum(1.0 for n in ns if n.p < SPLIT)
            s = s * bias / max(1, events[-1][0] / m)
            if best is None or s > best[0]:
                best = (s, m, ph)
    _, m, ph = best
    return m, (m - ph) % m


def build(name, notes, t0, t1, *, pulse=None):
    events, u, rep = splice(notes, t0, t1, pulse=pulse)
    if not events:
        return []
    fast = u < 0.4
    m, shift = choose_meter(events, fast)
    end = shift + events[-1][0] + 1
    bars = int(-(-end // m))
    if bars <= MAX_BARS:
        return [Riff(name, t0, t1, events, u, fast, m, shift, bars, rep)]
    # cut at bar lines into chunks of <= MAX_BARS, as even as possible
    n = -(-bars // MAX_BARS)
    per = -(-bars // n)
    out = []
    for j in range(n):
        lo, hi = j * per * m, (j + 1) * per * m
        ev = [(shift + p - lo, ns) for p, ns in events if lo <= shift + p < hi]
        if not ev:
            continue
        first = ev[0][0]
        nb = int(-(-(ev[-1][0] + 1) // m))
        ts = [n.t for _, ns in ev for n in ns]
        r = dict(rep) if j == 0 else {"continues": name + "abc"[j - 1]}
        sub = Riff(name + "abc"[j], min(ts), max(ts) + 0.5,
                   [(p - first, ns) for p, ns in ev], u, fast, m, first, nb, r)
        out.append(sub)
    return out


def parse_edits(text):
    """The page's "Copy edits" line -> {riff: "drop" | [bars to cut]}."""
    import re
    out = {}
    for part in filter(None, (x.strip() for x in re.split(r"[;\n]", text or ""))):
        if m := re.fullmatch(r"drop riff (\S+)", part):
            out[m[1]] = "drop"
        elif m := re.fullmatch(r"riff (\S+): cut bars? ([\d, ]+)", part):
            out[m[1]] = sorted({int(x) for x in m[2].replace(" ", "").split(",") if x})
    return out


def cut_bars(r: Riff, bars):
    """Remove whole bars and close the gap. Bar 1 is the first bar drawn."""
    m, kept = r.meter, []
    for pos, ns in r.events:
        a = r.shift + pos
        b = int(a // m) + 1
        if b in bars:
            continue
        kept.append((a - m * sum(1 for x in bars if x < b), ns))
    if not kept:
        return None
    a0 = kept[0][0]
    shift = a0 % m
    ev = [(a - a0, ns) for a, ns in kept]
    nb = int(-(-(shift + ev[-1][0] + 1) // m))
    rep = dict(r.report, bars_cut=list(bars))
    return Riff(r.name, r.t0, r.t1, ev, r.pulse_s, r.fast, m, shift, nb, rep)


# ------------------------------------------------------------------ notation

def to_score(r: Riff, title=""):
    import music21 as m21
    from .transcribe import estimate_key, speller

    class _K:  # estimate_key wants .pitch and .duration
        def __init__(s, n): s.pitch, s.duration = n.p, n.d
    allnotes = [n for _, ns in r.events for n in ns]
    key, _ = estimate_key([_K(n) for n in allnotes])
    spell = speller(key)
    ql = 0.5 if r.fast else 1.0                 # one pulse, in quarter notes
    bar_ql = r.meter * ql
    ts = {3: "3/4", 4: "4/4", 6: "6/8", 8: "4/4"}[r.meter]
    total = r.bars * r.meter

    sc = m21.stream.Score()
    sc.insert(0, m21.metadata.Metadata(title=title or f"Riff {r.name}"))
    parts = []
    hi = [n.p for n in allnotes if n.p >= SPLIT]
    staves = []
    if hi:   # a line that lives above C6 reads better under an 8va clef than on ledger lines
        hiclef = (m21.clef.Treble8vaClef() if statistics.median(hi) >= 84
                  else m21.clef.TrebleClef())
        staves.append(("hi", hiclef))
    if len(hi) < len(allnotes):
        staves.append(("lo", m21.clef.BassClef()))
    for staff, clef in staves:
        p = m21.stream.PartStaff()
        p.append(clef)
        p.append(m21.key.KeySignature(key.sharps))
        p.append(m21.meter.TimeSignature(ts))
        if staff == staves[0][0]:
            bpm = 60 / r.pulse_s * ql
            p.append(m21.tempo.MetronomeMark(number=round(bpm)))
        on = [(r.shift + pos, [n for n in ns if (n.p < SPLIT) == (staff == "lo")])
              for pos, ns in r.events]
        on = [(pos, ns) for pos, ns in on if ns]
        cur = 0.0
        for i, (pos, ns) in enumerate(on):
            if pos > cur:
                p.append(m21.note.Rest(quarterLength=(pos - cur) * ql))
            nxt = on[i + 1][0] if i + 1 < len(on) else min(total, pos + r.meter)
            dur = min(nxt - pos, 4.0)
            if i + 1 == len(on):
                dur = max(0.5, min(dur, total - pos))
            ps = sorted({n.p for n in ns})
            el = (m21.chord.Chord([spell(x) for x in ps]) if len(ps) > 1
                  else m21.note.Note(spell(ps[0])))
            el.quarterLength = dur * ql
            el.volume.velocity = max(n.v for n in ns)
            p.append(el)
            cur = pos + dur
        if cur < total:
            p.append(m21.note.Rest(quarterLength=(total - cur) * ql))
        parts.append(p)
    for p in parts:
        sc.insert(0, p)
    if len(parts) > 1:
        sc.insert(0, m21.layout.StaffGroup(parts, symbol="brace"))
    return sc.makeNotation(), key


def strikes_of(r: Riff):
    """The spliced riff as Strikes at the tracked tempo, for the Rhodes."""
    from .performance import Strike, key_index
    out = []
    evs = [(r.shift + p, ns) for p, ns in r.events]
    for i, (pos, ns) in enumerate(evs):
        t = pos * r.pulse_s
        for n in ns:
            nxt = next((q for q, ms in evs[i + 1:] if any((m.p < SPLIT) == (n.p < SPLIT) for m in ms)), None)
            hold = (nxt - pos) if nxt is not None else 2.0
            out.append(Strike(t, t + min(hold, 4) * r.pulse_s * 0.98, key_index(n.p), n.v))
    return sorted(out)


def svg_of(xml_path):
    import verovio
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": 2000, "pageHeight": 60000, "adjustPageHeight": True,
                   "scale": 42, "breaks": "auto", "header": "none", "footer": "none",
                   "pageMarginTop": 20, "pageMarginBottom": 20})
    tk.loadFile(str(xml_path))
    return "".join(tk.renderToSVG(i) for i in range(1, tk.getPageCount() + 1))


# ------------------------------------------------------------------ page

PAGE_CSS = """
:root{--bg:#f6f4ef;--fg:#1d1b18;--mute:#6b665d;--card:#fff;--line:#e2ddd2;--acc:#a4442b;--cut:rgba(200,40,30,.22)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#17161a;--fg:#ece8e0;--mute:#9a958c;--card:#222127;--line:#34323a;--acc:#e07a5c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 80px}h1{font-size:1.6rem;margin:0 0 4px}
.lede{color:var(--mute);margin:0 0 20px}.riff{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 18px}
.riff.dropped{opacity:.35}.head{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:baseline}
.head h2{margin:0;font-size:1.25rem}.meta{color:var(--mute);font-size:.9rem}.cuts{font-size:.85rem;color:var(--acc)}
.audio{display:flex;flex-wrap:wrap;gap:8px 16px;margin:10px 0}.audio label{font-size:.8rem;color:var(--mute);display:block}
audio{height:32px;max-width:100%}.paper{background:#fff;border-radius:6px;padding:4px;overflow-x:auto}
.paper svg{width:100%;height:auto;min-width:640px}.paper g.measure{cursor:pointer}
.paper g.measure.cut rect.cutbox{fill:var(--cut)}.paper g.measure:hover rect.cutbox{fill:rgba(0,0,0,.05)}
.paper g.measure.cut:hover rect.cutbox{fill:var(--cut)}
button{font:inherit;font-size:.85rem;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
.bar{position:sticky;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
#edits{font-family:ui-monospace,monospace;font-size:.85rem;color:var(--mute);flex:1;min-width:200px}
.how{font-size:.9rem;color:var(--mute);margin:0 0 18px}
"""

PAGE_JS = """
const KEY='riffs-edits-'+document.body.dataset.take;
let st={};try{st=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
function save(){try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){};summary()}
function summary(){const out=[];document.querySelectorAll('.riff').forEach(r=>{const n=r.dataset.name,s=st[n]||{};
 if(s.drop)out.push('drop riff '+n);else if(s.cut&&s.cut.length)out.push('riff '+n+': cut bar'+(s.cut.length>1?'s ':' ')+s.cut.sort((a,b)=>a-b).join(','))});
 document.getElementById('edits').textContent=out.length?out.join('; '):'No edits yet. Click a bar to cut it; Drop removes a whole riff.'}
document.querySelectorAll('.riff').forEach(r=>{const n=r.dataset.name;st[n]=st[n]||{cut:[]};
 const ms=r.querySelectorAll('g.measure');ms.forEach((m,i)=>{const b=m.getBBox();
  const rc=document.createElementNS('http://www.w3.org/2000/svg','rect');rc.setAttribute('class','cutbox');
  ['x','y','width','height'].forEach(k=>rc.setAttribute(k,b[k]));rc.setAttribute('fill','transparent');m.insertBefore(rc,m.firstChild);
  if(st[n].cut.includes(i+1))m.classList.add('cut');
  m.addEventListener('click',()=>{const c=st[n].cut,j=c.indexOf(i+1);if(j<0)c.push(i+1);else c.splice(j,1);m.classList.toggle('cut');save()})});
 const d=r.querySelector('.drop');if(st[n].drop){r.classList.add('dropped');d.textContent='Restore'}
 d.addEventListener('click',()=>{st[n].drop=!st[n].drop;r.classList.toggle('dropped');d.textContent=st[n].drop?'Restore':'Drop';save()})});
document.getElementById('copy').addEventListener('click',async e=>{const t=document.getElementById('edits').textContent;
 try{await navigator.clipboard.writeText(t);e.target.textContent='Copied'}catch(_){e.target.textContent='Select the text'};setTimeout(()=>e.target.textContent='Copy edits',1500)});
document.getElementById('clear').addEventListener('click',()=>{st={};try{localStorage.removeItem(KEY)}catch(e){};location.reload()});
summary();
"""


def _data_uri(path, mime):
    import base64
    p = Path(path)
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


def _mmss(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def page(out, index, *, title, take):
    """One self-contained HTML page: score, both audios, bar-level cut marks."""
    cards = []
    for r in index:
        n = r["name"]
        cut = []
        if r.get("bars_cut"):
            cut.append(f"you cut bar{'s' * (len(r['bars_cut']) > 1)} {', '.join(map(str, r['bars_cut']))}")
        if r.get("flutters"):
            cut.append(f"{r['flutters']} grace/flutter note{'s' * (r['flutters'] > 1)} dropped")
        if r.get("ghosts"):
            cut.append(f"{r['ghosts']} ghost{'s' * (r['ghosts'] > 1)} dropped")
        if r.get("splices"):
            cut.append(f"{r['splices']} hesitation{'s' * (r['splices'] > 1)} spliced out ({r['spliced_s']} s)")
        if r.get("continues"):
            cut.append(f"continues riff {r['continues']}")
        svg = (out / f"riff-{n}.svg").read_text()
        sp = _data_uri(out / f"riff-{n}-spliced.m4a", "audio/mp4")
        ap = _data_uri(out / f"riff-{n}-asplayed.m4a", "audio/mp4")
        cards.append(f"""<section class="riff" id="riff-{n}" data-name="{n}">
<div class="head"><h2>Riff {n}</h2><span class="meta">{r['bars']} bars · {r['meter']} · ♩={r['bpm']} · {r['key']} · take {_mmss(r['t0'])}–{_mmss(r['t1'])}</span>
<span class="cuts">{html.escape('; '.join(cut))}</span><button class="drop">Drop</button></div>
<div class="audio"><div><label>Spliced (what the score says)</label><audio controls preload="none" src="{sp}"></audio></div>
<div><label>As played</label><audio controls preload="none" src="{ap}"></audio></div></div>
<div class="paper">{svg}</div></section>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{PAGE_CSS}</style></head><body data-take="{html.escape(take)}"><main>
<h1>{html.escape(title)}</h1>
<p class="lede">{len(index)} riffs from <code>{html.escape(take)}</code>. Each is held to a steady pulse: grace notes, ghosts and hesitations were cut so the rhythm repeats.</p>
<p class="how">Click a bar to mark it cut (click again to keep it). <b>Drop</b> removes a whole riff. Then <b>Copy edits</b> and paste the line back to Claude, which applies them and re-renders.</p>
{''.join(cards)}</main>
<div class="bar"><span id="edits"></span><button id="copy">Copy edits</button><button id="clear">Clear</button></div>
<script>{PAGE_JS}</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", help="clip .mid")
    ap.add_argument("--windows", required=True,
                    help='JSON list of {"name","t0","t1"[,"pulse"]} in clip seconds')
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--title", default="Riffs")
    ap.add_argument("--edits", help='file holding the page\'s "Copy edits" line(s)')
    a = ap.parse_args(argv)
    from . import slicekit
    from .performance import Performance

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    notes, _ = read_midi(a.src)
    perf = Performance.from_midi_file(a.src) if not a.no_audio else None
    riffs = []
    for w in json.loads(Path(a.windows).read_text()):
        riffs += build(w["name"], notes, w["t0"], w["t1"], pulse=w.get("pulse"))
    edits = parse_edits(Path(a.edits).read_text()) if a.edits else {}
    riffs = [r if r.name not in edits else None if edits[r.name] == "drop"
             else cut_bars(r, edits[r.name]) for r in riffs]
    riffs = [r for r in riffs if r is not None]
    for name in edits:
        if name not in {r.name for r in riffs} and edits[name] != "drop":
            print(f"edit names riff {name}, which does not exist")
    index = []
    for r in riffs:
        sc, key = to_score(r)
        xml = out / f"riff-{r.name}.musicxml"
        sc.write("musicxml", fp=str(xml))
        sc.write("midi", fp=str(out / f"riff-{r.name}.mid"))
        (out / f"riff-{r.name}.svg").write_text(svg_of(xml))
        if perf is not None:
            slicekit.render(strikes_of(r), [], out / f"riff-{r.name}-spliced.m4a")
            slicekit.clip(perf, r.t0, r.t1, out / f"riff-{r.name}-asplayed.m4a")
        kept = sum(len(ns) for _, ns in r.events)
        index.append({"name": r.name, "t0": round(r.t0, 2), "t1": round(r.t1, 2),
                      "bars": r.bars, "meter": {3: "3/4", 4: "4/4", 6: "6/8", 8: "4/4"}[r.meter],
                      "bpm": round(60 / r.pulse_s * (0.5 if r.fast else 1.0)),
                      "key": f"{key.tonic.name.replace('-', 'b')} {key.mode}",
                      "notes_kept": kept, **r.report})
        print(r.name, index[-1])
    (out / "riffs.json").write_text(json.dumps(index, indent=1))
    (out / "riffs.html").write_text(page(out, index, title=a.title, take=Path(a.src).name))


if __name__ == "__main__":
    main()
