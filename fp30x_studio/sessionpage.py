r"""One page for one composing session: blocks, figures, stumbles, arrangement.

A 22-minute free take is not a piece and not five pieces. It is material, and
what makes it usable is being able to *hear* any part of it on demand, next to
any other part. So this builds a page whose unit is the audible excerpt:

* **Blocks** -- the take cut at its own largest silences, recursively, until no
  block is longer than :data:`BLOCK_MAX`. Not a fixed grid: a grid cuts through
  phrases, and a phrase cut in half is not a building block.
* **Figures** -- from :mod:`fp30x_studio.motifs`, every occurrence rendered
  separately so recurrences can be played back to back and compared.
* **Stumbles** -- from :mod:`fp30x_studio.stumbles`, each as a *pair*: the bar
  as played and the same bar with the bad note deleted. Nothing is applied. The
  page exists so the choice is made by ear rather than from a table.
* **Arrangement** -- blocks and figures queued in any order and played through
  in the browser, so an order can be tried before anything is rendered.

Everything is rendered ahead of time into one directory that is symlinked into
the served root, so the page holds only relative paths and nothing depends on a
process still being alive.
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import motifs, slicekit, stumbles
from .performance import Performance

SESSIONS = Path("~/Music/FP-30X Studio/sessions").expanduser()
SERVED = Path("~/.claude/served").expanduser()
BLOCK_MAX = 26.0          # seconds; longer than this and a block is not a block
BLOCK_MIN = 4.0           # shorter than this and it is a fragment, not material
STUMBLE_PAD = 2.6         # seconds of context each side of a candidate
FIGURE_PAD = 0.9
MAX_FIGURES = 12
MAX_STUMBLES = 28


def blocks(perf: Performance, *, cap: float = BLOCK_MAX) -> list[tuple[float, float]]:
    """Cut at the take's own largest silences until every piece is short enough."""
    ss = perf.strikes()
    if not ss:
        return []
    run = ss[0].t_off
    gaps: list[tuple[float, float, float]] = []   # (gap, t_off, next_t_on)
    for s in ss[1:]:
        if s.t_on - run > 0.12:
            gaps.append((s.t_on - run, run, s.t_on))
        run = max(run, s.t_off)

    def split(a: float, b: float) -> list[tuple[float, float]]:
        if b - a <= cap:
            return [(a, b)]
        inner = [g for g in gaps if a + BLOCK_MIN < g[1] < b - BLOCK_MIN]
        if not inner:
            mid = (a + b) / 2                      # no silence to use: halve it
            return split(a, mid) + split(mid, b)
        g = max(inner, key=lambda g: g[0])
        return split(a, g[1]) + split(g[2], b)

    return split(ss[0].t_on, max(s.t_off for s in ss))


def _session_gain(perf: Performance, out: Path) -> float:
    """Probe the loudest 60 s of the take once, and use it for every excerpt."""
    cache = out / "gain.txt"
    if cache.exists():
        try:
            return float(cache.read_text())
        except ValueError:
            pass
    ss = perf.strikes()
    peak_t, best = perf.t_start, -1.0
    for s in ss[::40]:
        e = sum(x.velocity for x in ss if s.t_on <= x.t_on < s.t_on + 60)
        if e > best:
            best, peak_t = e, s.t_on
    strikes, pedal = slicekit.window(perf, peak_t, peak_t + 60)
    import tempfile
    from .performance import synthesize_midi
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "g.mid"
        synthesize_midi(strikes, pedal, mid)
        g = slicekit._gain_for(mid)
    cache.write_text(f"{g:.4f}")
    return g


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def build(take: Path, *, jobs: int = 4, open_it: bool = True) -> str:
    perf = Performance.from_raw_capture(take)
    stem = take.stem
    out = SESSIONS / stem
    (out / "audio").mkdir(parents=True, exist_ok=True)

    blks = [(a, b) for a, b in blocks(perf) if b - a >= BLOCK_MIN]
    figs = [m for m in motifs.find(perf) if m.n >= 5][:MAX_FIGURES]
    cands = sorted(stumbles.find(perf),
                   key=lambda c: (-c.confidence, c.t))[:MAX_STUMBLES]

    jobs_list: list[tuple[str, object]] = []
    for i, (a, b) in enumerate(blks):
        jobs_list.append((f"audio/block-{i:02d}.m4a", (a, b, None)))
    for fi, m in enumerate(figs):
        for oi, o in enumerate(m.occurrences):
            jobs_list.append((f"audio/fig-{fi:02d}-{oi}.m4a",
                              (o.t_on - FIGURE_PAD, o.t_off + FIGURE_PAD, None)))
    for ci, c in enumerate(cands):
        t0, t1 = c.t - STUMBLE_PAD, c.t + STUMBLE_PAD
        jobs_list.append((f"audio/stum-{ci:02d}-as-played.m4a", (t0, t1, None)))
        if c.removable:
            jobs_list.append((f"audio/stum-{ci:02d}-cleaned.m4a", (t0, t1, c)))

    # ONE gain for every excerpt, probed once on the whole take.
    # Per-clip auto-gain would level each excerpt to the same peak, which
    # erases the dynamics between blocks and makes an A/B pair differ in
    # loudness as well as in content -- the one comparison the page exists to
    # support. A shared gain keeps a quiet block quiet.
    gain = _session_gain(perf, out)

    def one(job):
        rel, (t0, t1, cand) = job
        dest = out / rel
        if dest.exists() and dest.stat().st_size > 0:
            return rel
        source = stumbles.excise(perf, [cand]) if cand is not None else perf
        return rel if slicekit.clip(source, max(0.0, t0), t1, dest,
                                    gain=gain) else None

    done = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for r in pool.map(one, jobs_list):
            done += r is not None
            print(f"\r  rendered {done}/{len(jobs_list)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    # The full take, if postprocess already made it.
    renders = Path("~/Music/FP-30X Studio/renders").expanduser()
    full = next((p for p in sorted(renders.glob(f"{stem}-COMPLETE-*.m4a"))), None)
    if full and not (out / "audio" / "full.m4a").exists():
        shutil.copy2(full, out / "audio" / "full.m4a")

    data = {
        "take": stem,
        "duration": perf.t_end,
        "notes": perf.n_strikes,
        "full": (out / "audio" / "full.m4a").exists(),
        "blocks": [
            {"i": i, "t0": a, "t1": b, "dur": b - a,
             "notes": sum(1 for s in perf.strikes() if a <= s.t_on <= b),
             "audio": f"audio/block-{i:02d}.m4a"}
            for i, (a, b) in enumerate(blks)
        ],
        "figures": [
            {"i": fi, "n": m.n, "shape": list(m.intervals),
             "weight": m.weight, "span": m.span,
             "label": motifs.describe(m),
             "occ": [{"t": o.t_on, "dur": o.duration, "pitch0": o.pitch0,
                      "audio": f"audio/fig-{fi:02d}-{oi}.m4a"}
                     for oi, o in enumerate(m.occurrences)]}
            for fi, m in enumerate(figs)
        ],
        "stumbles": [
            {"i": ci, "kind": c.kind, "t": c.t, "conf": c.confidence,
             "note": c.note, "removable": c.removable,
             "as_played": f"audio/stum-{ci:02d}-as-played.m4a",
             "cleaned": f"audio/stum-{ci:02d}-cleaned.m4a" if c.removable else None}
            for ci, c in enumerate(cands)
        ],
    }
    (out / "session.json").write_text(json.dumps(data, indent=1))
    (out / "index.html").write_text(_page(data))

    SERVED.mkdir(parents=True, exist_ok=True)
    link = SERVED / f"session-{stem}"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(out)

    url = f"http://127.0.0.1:8777/session-{stem}/index.html"
    if open_it:
        subprocess.run([str(Path("~/.claude/bin/show").expanduser()),
                        str(out / "index.html")], capture_output=True)
    return url


def _page(d: dict) -> str:
    return (_TEMPLATE.replace("__DATA__", json.dumps(d))
            .replace("<title>Session</title>",
                     f"<title>{html.escape(d['take'])}</title>"))


_TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Session</title>
<style>
 :root{--bg:#0e1013;--card:#171a1f;--line:#23272e;--fg:#e8eaed;--dim:#8b9199;
       --acc:#4aa3ff;--warm:#e0a33e;--ok:#1f9d4d;}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);padding-bottom:150px;
      font:15px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}
 .wrap{max-width:1000px;margin:0 auto;padding:0 20px}
 header{padding:34px 0 18px}
 h1{font-size:27px;margin:0 0 6px;letter-spacing:-.02em}
 .meta{color:var(--dim);font-size:14px}
 nav{position:sticky;top:0;z-index:20;background:rgba(14,16,19,.94);
     backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
     padding:10px 0;margin-bottom:26px}
 nav .wrap{display:flex;gap:6px;flex-wrap:wrap}
 nav a{color:var(--dim);text-decoration:none;padding:5px 12px;border-radius:999px;
       font-size:13px;border:1px solid transparent}
 nav a:hover{color:var(--fg);border-color:var(--line)}
 h2{font-size:19px;margin:38px 0 6px;letter-spacing:-.01em}
 .lede{color:var(--dim);font-size:14px;margin:0 0 16px;max-width:66ch}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
       padding:14px 16px;margin-bottom:10px}
 .row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
 .grow{flex:1;min-width:120px}
 button{font:inherit;font-size:13px;color:var(--fg);background:#20242b;
        border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
 button:hover{background:#2a2f37}
 button.play{min-width:74px}
 button.play.on{background:var(--acc);border-color:var(--acc);color:#04121f}
 button.add{color:var(--acc);border-color:#22384d}
 .t{color:var(--dim);font-variant-numeric:tabular-nums;font-size:13px}
 .pill{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
       padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--dim)}
 .pill.grab{color:#ff8b7a;border-color:#4a2a26}
 .pill.double{color:#e0a33e;border-color:#4a3a1e}
 .pill.brush{color:#b98bff;border-color:#372a4a}
 .pill.hesitation{color:#6fb7ff;border-color:#22384d}
 .bar{height:5px;border-radius:3px;background:#262b33;overflow:hidden;min-width:70px}
 .bar i{display:block;height:100%;background:var(--acc)}
 .shape{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--dim)}
 .occ{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
 .occ button{font-size:12px;padding:4px 9px}
 .ab{display:flex;gap:8px;align-items:center;margin-top:9px;flex-wrap:wrap}
 .note{color:var(--dim);font-size:13px;margin-top:5px}
 .tray{position:fixed;left:0;right:0;bottom:0;background:rgba(18,21,26,.97);
       border-top:1px solid var(--line);backdrop-filter:blur(10px);z-index:30}
 .tray .wrap{padding:12px 20px}
 .queue{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;min-height:38px;align-items:center}
 .chip{display:flex;align-items:center;gap:6px;background:#20242b;border:1px solid var(--line);
       border-radius:999px;padding:4px 6px 4px 12px;font-size:12px;white-space:nowrap}
 .chip b{font-weight:600}
 .chip x{cursor:pointer;color:var(--dim);padding:0 5px;border-radius:50%}
 .chip x:hover{color:#ff8b7a}
 .chip.now{border-color:var(--acc);background:#16293b}
 .empty{color:var(--dim);font-size:13px}
 .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:8px}
 .tile{background:var(--card);border:1px solid var(--line);border-radius:10px;
       padding:9px 11px;cursor:pointer;position:relative;transition:border-color .15s}
 .tile:hover{border-color:#39414c}
 .tile.on{border-color:var(--acc);background:#16293b}
 .tile.queued{border-left:3px solid var(--acc)}
 .tile .tt{font-variant-numeric:tabular-nums;font-size:14px;font-weight:600}
 .tile .ts{color:var(--dim);font-size:12px;margin-top:2px}
 .tile .len{height:3px;border-radius:2px;background:#2b313a;margin-top:7px;overflow:hidden}
 .tile .len i{display:block;height:100%;background:#4a5563}
 .tile q{position:absolute;top:6px;right:7px;font-size:15px;line-height:1;color:var(--dim);
         padding:1px 4px;border-radius:5px}
 .tile q:hover{color:var(--acc);background:#20242b}
 table{width:100%;border-collapse:collapse;font-size:14px}
 td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
 tr:last-child td{border-bottom:none}
</style>
<nav><div class="wrap">
  <a href="#whole">Whole take</a><a href="#blocks">Blocks</a>
  <a href="#figures">Recurring figures</a><a href="#stumbles">Stumbles</a>
</div></nav>
<div class="wrap">
<header>
  <h1 id="ttl"></h1>
  <div class="meta" id="meta"></div>
</header>

<section id="whole">
  <h2>The whole take</h2>
  <p class="lede">Electric, linear gain, hall.</p>
  <div class="card" id="fullcard"></div>
</section>

<section id="blocks">
  <h2>Blocks</h2>
  <p class="lede">Cut at the take's own longest silences, never at a fixed grid, so no
     block opens in the middle of a phrase. Click a tile to hear it; the <b>+</b> adds it
     to the arrangement.</p>
  <div class="row" style="margin-bottom:12px">
    <button id="bsort">Reorder by length</button>
    <span class="grow"></span>
    <span class="t" id="bstat"></span>
  </div>
  <div class="tiles" id="blocklist"></div>
</section>

<section id="figures">
  <h2>Recurring figures</h2>
  <p class="lede">Shapes played more than once, matched by interval so a figure counts
     when it comes back in another key. Each occurrence plays separately: the point is
     hearing two of them next to each other.</p>
  <div id="figlist"></div>
</section>

<section id="stumbles">
  <h2>Stumbles</h2>
  <p class="lede">Places where a key was closed briefly and abandoned, and a neighbour
     taken instead. Nothing here is applied. Each one plays as recorded, and again with
     that one note deleted — the second button is the edit, and it only exists as audio
     until you say otherwise.</p>
  <div id="stumlist"></div>
</section>
</div>

<div class="tray"><div class="wrap">
  <div class="row" style="margin-bottom:8px">
    <b style="font-size:14px">Arrangement</b>
    <span class="t" id="qstat"></span>
    <span class="grow"></span>
    <button id="qplay">Play through</button>
    <button id="qclear">Clear</button>
  </div>
  <div class="queue" id="queue"><span class="empty">Add blocks or figures and they play in this order.</span></div>
</div></div>

<script>
const D = __DATA__;
const BASE = '/session-' + D.take + '/';   // root-absolute: the page
// is served both from inside its own directory and, when published by
// `show`, from the served root under a hashed name. Relative paths break
// in the second case; this works in both.
const mmss = t => `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`;
const PITCH = n => ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][n%12] + (Math.floor(n/12)-1);

document.getElementById('ttl').textContent = D.take;
document.getElementById('meta').textContent =
  `${mmss(D.duration)} · ${D.notes.toLocaleString()} notes · ${D.blocks.length} blocks · ` +
  `${D.figures.length} recurring figures · ${D.stumbles.length} flagged`;

/* ---- one audio element, so two excerpts never play over each other ------- */
let cur = null, curBtn = null, onEnd = null;
const audio = new Audio();
audio.addEventListener('ended', () => { const f = onEnd; stop(); if (f) f(); });
function stop(){ audio.pause(); if(curBtn) curBtn.classList.remove('on');
                 if(curBtn) curBtn.textContent = curBtn.dataset.label || 'Play';
                 curBtn = null; cur = null; onEnd = null; }
function play(src, btn, after){
  if (cur === src && !audio.paused) { stop(); return; }
  stop();
  audio.src = BASE + src; audio.currentTime = 0;
  audio.play().catch(()=>{});
  cur = src; curBtn = btn; onEnd = after || null;
  if (btn){ btn.dataset.label = btn.dataset.label || btn.textContent;
            btn.classList.add('on'); btn.textContent = 'Stop'; }
}
function mk(tag, cls, txt){ const e=document.createElement(tag);
  if(cls) e.className=cls; if(txt!==undefined) e.textContent=txt; return e; }
function playBtn(src, label, after){
  const b = mk('button','play',label||'Play');
  b.onclick = () => play(src, b, after); return b;
}

/* ---- whole take --------------------------------------------------------- */
const fc = document.getElementById('fullcard');
if (D.full){
  const r = mk('div','row');
  r.append(playBtn('audio/full.m4a','Play'),
           mk('span','t', `${mmss(D.duration)} · as recorded, nothing removed`));
  fc.append(r);
} else {
  fc.append(mk('span','empty','Still rendering.'));
}

/* ---- blocks ------------------------------------------------------------- */
const bl = document.getElementById('blocklist');
const LONGEST = Math.max(...D.blocks.map(b=>b.dur));
let byTime = true;
function drawBlocks(){
  bl.innerHTML = '';
  const list = byTime ? D.blocks : [...D.blocks].sort((x,y)=>y.dur-x.dur);
  list.forEach(b => {
    const t = mk('div','tile');
    t.append(Object.assign(mk('div','tt', mmss(b.t0)), {}),
             mk('div','ts', `${b.dur.toFixed(0)}s · ${b.notes} notes`));
    const len = mk('div','len'), i = mk('i');
    i.style.width = (100*b.dur/LONGEST)+'%'; len.append(i); t.append(len);
    const q = mk('q',null,'+');
    q.title = 'add to the arrangement';
    q.onclick = e => { e.stopPropagation();
      enqueue({label:`Block ${b.i}`, sub:mmss(b.t0), src:b.audio});
      t.classList.add('queued'); };
    t.append(q);
    t.onclick = () => {
      const was = t.classList.contains('on');
      document.querySelectorAll('.tile.on').forEach(x=>x.classList.remove('on'));
      if (was) { stop(); return; }
      t.classList.add('on');
      play(b.audio, null, () => t.classList.remove('on'));
    };
    bl.append(t);
  });
  document.getElementById('bstat').textContent =
    `${D.blocks.length} blocks · ${mmss(D.blocks.reduce((a,b)=>a+b.dur,0))} total`;
}
document.getElementById('bsort').onclick = e => {
  byTime = !byTime;
  e.target.textContent = byTime ? 'Reorder by length' : 'Reorder by time';
  drawBlocks();
};
drawBlocks();

/* ---- figures ------------------------------------------------------------ */
const fl = document.getElementById('figlist');
D.figures.forEach(f => {
  const c = mk('div','card');
  const r = mk('div','row');
  r.append(mk('b', null, `${f.n} notes, ${f.occ.length}×`),
           mk('span','shape', f.shape.map(d=>(d>0?'+':'')+d).join(' ')));
  const g = mk('span','grow'); r.append(g);
  const all = mk('button',null,'Play all, back to back');
  all.onclick = () => { let i = 0;
    const step = () => { if (i >= f.occ.length) return;
      play(f.occ[i].audio, i === 0 ? all : null, () => { i++; step(); }); };
    step(); };
  r.append(all);
  c.append(r);
  const o = mk('div','occ');
  f.occ.forEach((oc, k) => {
    const b = mk('button', null, `${mmss(oc.t)} · ${PITCH(oc.pitch0)}`);
    b.onclick = () => play(oc.audio, b);
    b.oncontextmenu = e => { e.preventDefault();
      enqueue({label:`Figure ${f.i}.${k}`, sub:mmss(oc.t), src:oc.audio}); };
    o.append(b);
  });
  const add = mk('button','add','+ all to arrangement');
  add.onclick = () => f.occ.forEach((oc,k) =>
      enqueue({label:`Figure ${f.i}.${k}`, sub:mmss(oc.t), src:oc.audio}));
  o.append(add);
  c.append(o);
  c.append(mk('div','note',
    `${f.occ.length} occurrences, ${new Set(f.occ.map(x=>x.pitch0)).size} starting pitch(es). ` +
    `Right-click one occurrence to add just that one.`));
  fl.append(c);
});

/* ---- stumbles ----------------------------------------------------------- */
const sl = document.getElementById('stumlist');
D.stumbles.forEach(s => {
  const c = mk('div','card');
  const r = mk('div','row');
  r.append(mk('span','pill '+s.kind, s.kind),
           mk('span','t', mmss(s.t)),
           mk('span','grow', s.note));
  const bar = mk('div','bar'); const i = mk('i'); i.style.width = (s.conf*100)+'%';
  bar.append(i); r.append(bar, mk('span','t', s.conf.toFixed(2)));
  c.append(r);
  const ab = mk('div','ab');
  ab.append(playBtn(s.as_played,'As played'));
  if (s.cleaned) ab.append(playBtn(s.cleaned,'Cleaned'));
  else ab.append(mk('span','t','a gap, not a note: nothing to delete'));
  c.append(ab);
  sl.append(c);
});

/* ---- arrangement -------------------------------------------------------- */
let Q = [];
const qel = document.getElementById('queue');
function enqueue(item){ Q.push(item); drawQ(); }
function drawQ(pos){
  qel.innerHTML = '';
  if (!Q.length){ qel.append(mk('span','empty',
      'Add blocks or figures and they play in this order.'));
    document.getElementById('qstat').textContent = ''; return; }
  Q.forEach((it, k) => {
    const ch = mk('div','chip' + (pos===k?' now':''));
    ch.append(mk('b',null,it.label), mk('span','t',it.sub));
    const x = mk('x',null,'×'); x.onclick = () => { Q.splice(k,1); drawQ(); };
    ch.append(x); qel.append(ch);
  });
  document.getElementById('qstat').textContent = `${Q.length} parts`;
}
document.getElementById('qclear').onclick = () => { stop(); Q = []; drawQ(); };
// Toggling keys on whether THE ARRANGEMENT is running, not on whether any
// audio is. Pressing this while auditioning a single excerpt should start the
// arrangement, not silently stop the excerpt and do nothing else.
let qRunning = false;
const qbtn = document.getElementById('qplay');
document.getElementById('qplay').onclick = () => {
  if (!Q.length) return;
  if (qRunning){ qRunning = false; stop(); qbtn.textContent = 'Play through';
                 drawQ(); return; }
  qRunning = true; qbtn.textContent = 'Stop';
  let i = 0;
  const step = () => {
    if (!qRunning) return;
    if (i >= Q.length){ qRunning = false; qbtn.textContent = 'Play through';
                        drawQ(); return; }
    drawQ(i);
    play(Q[i].src, null, () => { i++; step(); });
  };
  step();
};
drawQ();
</script>
"""


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m fp30x_studio.sessionpage",
        description="Build the session page for one take.")
    ap.add_argument("take", type=Path)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    url = build(a.take.expanduser(), jobs=a.jobs, open_it=not a.no_open)
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
