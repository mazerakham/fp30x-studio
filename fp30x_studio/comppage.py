r"""The page where the comp is chosen: one bar group at a time, by ear.

Every candidate is rendered ahead of time at one shared gain, so two takes of
the same four bars differ in what was played and not in how loud they were
normalised -- the comparison the page exists for.

The selection is the deliverable, so it leaves the browser: it lives in
``localStorage`` while he works and downloads as ``selection.json``, which is
what :func:`fp30x_studio.comp.assemble` reads. Nothing here depends on a server
that outlives the session.
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import comp, slicekit
from .performance import Performance

SESSIONS = Path("~/Music/FP-30X Studio/sessions").expanduser()
SERVED = Path("~/.claude/served").expanduser()
CLIPS = Path("~/Music/FP-30X Studio/takes/clips").expanduser()
PAD = 0.35


def build(name: str, *, jobs: int = 5) -> str:
    out = SESSIONS / name
    maps = comp.load_maps(out / "measuremaps")
    if not maps:
        raise SystemExit(f"no accepted measure maps under {out/'measuremaps'}")
    # The map records the file it was built from, so a map may come from a
    # whole take, a clip, or a sweep segment without the page caring which.
    perfs = {pid: Performance.from_raw_capture(_source(m))
             for pid, m in maps.items()}
    cands = comp.candidates(maps)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    gain = _gain(perfs, cands, out)

    jobs_list = [(gi, c) for gi in sorted(cands) for c in cands[gi]]

    def one(job):
        gi, c = job
        dest = out / "audio" / f"g{gi:02d}-{_slug(c.key)}.m4a"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return slicekit.clip(perfs[c.pass_id], max(0.0, c.t0 - PAD),
                             c.t1 + PAD, dest, gain=gain)

    done = 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for r in pool.map(one, jobs_list):
            done += r is not None
            print(f"\r  rendered {done}/{len(jobs_list)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    data = {
        "name": name,
        "title": next(iter(maps.values()))["title"],
        "n_measures": max(m["n_measures"] for m in maps.values()),
        "passes": {pid: {"bars": len(m["bars"]), "cost": m["cost_median"],
                         "seconds": m["seconds"]} for pid, m in maps.items()},
        "groups": [
            {"i": gi,
             "bars": list(cands[gi][0].bars),
             "candidates": [
                 {"pass": c.key, "take": c.pass_id, "t0": round(c.t0, 2), "t1": round(c.t1, 2),
                  "dur": round(c.duration, 2), "spb": round(c.seconds_per_bar, 2),
                  "matched": c.bars_matched,
                  "audio": f"audio/g{gi:02d}-{_slug(c.key)}.m4a"}
                 for c in cands[gi]]}
            for gi in sorted(cands)],
        "auto": (json.loads((out / "selection-auto.json").read_text())["chosen"]
                 if (out / "selection-auto.json").exists() else None),
        "auto_audio": ("audio/AUTO-COMP.m4a"
                       if (out / "audio" / "AUTO-COMP.m4a").exists() else None),
        "missing": sorted(
            set(range(0, max(m["n_measures"] for m in maps.values()) + 1))
            - {b for m in maps.values() for b in map(int, m["bars"])}),
    }
    (out / "comp.json").write_text(json.dumps(data, indent=1))
    (out / "comp.html").write_text(_page(data))

    SERVED.mkdir(parents=True, exist_ok=True)
    link = SERVED / f"comp-{name}"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(out)
    return f"http://127.0.0.1:8777/comp-{name}/comp.html"


def _source(m: dict) -> Path:
    p = Path(m["take"]).expanduser()
    if p.exists():
        return p
    alt = CLIPS / p.name
    if alt.exists():
        return alt
    raise SystemExit(f"the take this map was built from is gone: {p}")


def _slug(s: str) -> str:
    return s.replace("/", "-").replace("#", "-r")


def _gain(perfs: dict[str, Performance], cands: dict, out: Path) -> float:
    """One gain for every excerpt, probed on exactly the audio it will render.

    One gain is the point -- two takes of the same four bars must differ in what
    was played, not in how loud they were normalised. *Which* gain took three
    goes. An arbitrary minute of one pass left clips at -0.40 dBTP; each pass's
    loudest minute by velocity left two of fifty-eight over, because a velocity
    sum is not a peak. So the probe now runs on every candidate window laid end
    to end, which is the material itself and cannot be unrepresentative of it.
    """
    cache = out / "gain.txt"
    if cache.exists():
        try:
            return float(cache.read_text())
        except ValueError:
            pass
    import tempfile
    from .performance import Strike, synthesize_midi

    strikes: list[Strike] = []
    pedal: list[tuple[float, float]] = []
    clock = 0.0
    for gi in sorted(cands):
        for c in cands[gi]:
            w, pd = slicekit.window(perfs[c.pass_id], max(0.0, c.t0 - PAD), c.t1 + PAD)
            strikes += [Strike(clock + s.t_on, clock + s.t_off, s.key, s.velocity)
                        for s in w]
            pedal += [(clock + a, clock + b) for a, b in pd]
            clock += c.duration + 2 * PAD
    with tempfile.TemporaryDirectory() as td:
        mid = Path(td) / "g.mid"
        synthesize_midi(strikes, pedal, mid)
        g = slicekit._gain_for(mid)
    cache.write_text(f"{g:.4f}")
    return g


def _page(d: dict) -> str:
    return (_TEMPLATE.replace("__DATA__", json.dumps(d))
            .replace("__TITLE__", html.escape(d["title"])))


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fp30x_studio.comppage")
    ap.add_argument("name", help="session directory name under sessions/")
    ap.add_argument("--jobs", type=int, default=5)
    a = ap.parse_args(argv)
    print(build(a.name, jobs=a.jobs))
    return 0



_TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ — comp</title>
<style>
 :root{--bg:#0e1013;--card:#171a1f;--line:#23272e;--fg:#e8eaed;--dim:#8b9199;
       --acc:#4aa3ff;--ok:#1f9d4d;--warn:#e0a33e}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);padding-bottom:120px;
      font:15px/1.55 -apple-system,BlinkMacSystemFont,system-ui,sans-serif}
 .wrap{max-width:940px;margin:0 auto;padding:0 20px}
 header{padding:30px 0 14px}
 h1{font-size:25px;margin:0 0 5px;letter-spacing:-.02em}
 .meta{color:var(--dim);font-size:14px}
 .lede{color:var(--dim);font-size:14px;max-width:70ch;margin:16px 0 22px}
 .grp{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:14px 16px;margin-bottom:10px}
 .grp.chosen{border-left:3px solid var(--ok)}
 .grp.jump{border-left:3px solid var(--warn)}
 .hd{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:10px}
 .bars{font-size:16px;font-weight:650}
 .t{color:var(--dim);font-size:13px;font-variant-numeric:tabular-nums}
 .grow{flex:1}
 .cands{display:flex;gap:7px;flex-wrap:wrap}
 button{font:inherit;font-size:13px;color:var(--fg);background:#20242b;
        border:1px solid var(--line);border-radius:8px;padding:6px 11px;cursor:pointer}
 button:hover{background:#2a2f37}
 .c{display:flex;flex-direction:column;align-items:flex-start;gap:1px;min-width:118px}
 .c b{font-weight:600;font-size:13px}
 .c span{color:var(--dim);font-size:11.5px}
 .c.on{border-color:var(--acc);background:#16293b}
 .c.pick{border-color:var(--ok);background:#12271a}
 .c.pick b::before{content:"✓ ";color:var(--ok)}
 .none{color:var(--dim);font-size:13px;padding:6px 0}
 .bar{position:fixed;left:0;right:0;bottom:0;background:rgba(18,21,26,.97);
      border-top:1px solid var(--line);backdrop-filter:blur(10px);z-index:10}
 .bar .wrap{padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
 .prog{height:6px;border-radius:3px;background:#262b33;flex:1;min-width:140px;overflow:hidden}
 .prog i{display:block;height:100%;background:var(--ok);width:0}
 .warn{color:var(--warn);font-size:12.5px}
 .note{color:var(--dim);font-size:13px;margin-top:26px}
 code{background:#20242b;border:1px solid var(--line);border-radius:5px;padding:1px 5px;font-size:12.5px}
</style>
<div class="wrap">
<header>
  <h1>__TITLE__</h1>
  <div class="meta" id="meta"></div>
</header>
<div class="grp" id="autocard" hidden>
  <div class="hd">
    <span class="bars">A first pass, chosen for you</span>
    <span class="t" id="autometa"></span>
    <span class="grow"></span>
    <button id="autoplay">Play it</button>
    <button id="autoload">Start from this</button>
  </div>
  <div class="t" id="autonote"></div>
</div>
<p class="lede">One row per four bars. Press a candidate to hear those bars from that
pass; press it again to choose it. Choices are kept as you go. <b>Play the comp</b>
runs your current selection end to end.</p>
<div id="groups"></div>
<p class="note" id="gap"></p>
</div>

<div class="bar"><div class="wrap">
  <b style="font-size:14px">Comp</b>
  <span class="t" id="count"></span>
  <div class="prog"><i id="progi"></i></div>
  <span class="warn" id="warn"></span>
  <button id="playall">Play the comp</button>
  <button id="save">Download selection.json</button>
</div></div>

<script>
const D = __DATA__;
const BASE = '/comp-' + D.name + '/';
const KEY = 'comp:' + D.name;
let pick = {};
try { pick = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { pick = {}; }

const short = p => p.replace(/^2026-09-/, '').replace(/something-doing-/, 'sd-')
                    .replace(/-mirror$/, '');
const mk = (t, c, x) => { const e = document.createElement(t);
  if (c) e.className = c; if (x !== undefined) e.textContent = x; return e; };

document.getElementById('meta').textContent =
  `${Object.keys(D.passes).length} passes · ${D.groups.length} decisions · `
  + `${D.n_measures} bars in the score`;
if (D.missing.length)
  document.getElementById('gap').textContent =
    `No pass was placed in bar${D.missing.length > 1 ? 's' : ''} `
    + D.missing.join(', ') + '. Those bars are not offered here and the comp skips them.';

/* one audio element: two excerpts must never overlap */
const audio = new Audio();
let curBtn = null, after = null;
audio.addEventListener('ended', () => { const f = after; stop(); if (f) f(); });
function stop(){ audio.pause(); if (curBtn) curBtn.classList.remove('on');
                 curBtn = null; after = null; }
function play(src, btn, then){
  stop(); audio.src = BASE + src; audio.currentTime = 0;
  audio.play().catch(()=>{});
  curBtn = btn || null; after = then || null;
  if (btn) btn.classList.add('on');
}

function save(){ try { localStorage.setItem(KEY, JSON.stringify(pick)); } catch (e) {} }

const host = document.getElementById('groups');
function draw(){
  host.innerHTML = '';
  D.groups.forEach(g => {
    const row = mk('div', 'grp');
    const hd = mk('div', 'hd');
    const b = g.bars;
    hd.append(mk('span', 'bars', `Bars ${b[0]}–${b[b.length-1]}`),
              mk('span', 't', `${g.candidates.length} candidate${g.candidates.length>1?'s':''}`),
              mk('span', 'grow'));
    const chosen = pick[g.i];
    if (chosen){
      const c = g.candidates.find(x => x.pass === chosen);
      if (c) hd.append(mk('span','t', `${short(c.pass)} · ${c.spb.toFixed(2)} s/bar`));
      row.classList.add('chosen');
    }
    row.append(hd);
    const cs = mk('div', 'cands');
    g.candidates.forEach(c => {
      const btn = mk('button', 'c' + (chosen === c.pass ? ' pick' : ''));
      btn.append(mk('b', null, short(c.pass)),
                 mk('span', null, `${c.dur.toFixed(1)}s · ${c.spb.toFixed(2)} s/bar`
                    + (c.matched < b.length ? ` · ${c.matched}/${b.length} bars` : '')));
      btn.onclick = () => {
        if (curBtn === btn){ pick[g.i] = c.pass; save(); stop(); draw(); return; }
        play(c.audio, btn);
      };
      cs.append(btn);
    });
    if (chosen){
      const clr = mk('button', null, 'clear');
      clr.onclick = () => { delete pick[g.i]; save(); draw(); };
      cs.append(clr);
    }
    row.append(cs);
    host.append(row);
  });
  status();
}

function status(){
  const n = Object.keys(pick).length, tot = D.groups.length;
  document.getElementById('count').textContent = `${n} of ${tot} chosen`;
  document.getElementById('progi').style.width = (100*n/tot) + '%';
  // A seam cannot hide a tempo change. Warn where consecutive choices differ
  // by more than a quarter, which is audible as the music speeding up.
  let jumps = 0;
  D.groups.forEach((g, k) => {
    const el = host.children[k]; if (el) el.classList.remove('jump');
    const a = pick[g.i], prev = k > 0 ? pick[D.groups[k-1].i] : null;
    if (!a || !prev) return;
    const ca = g.candidates.find(x => x.pass === a);
    const cp = D.groups[k-1].candidates.find(x => x.pass === prev);
    if (!ca || !cp) return;
    const r = ca.spb / cp.spb;
    if (r > 1.25 || r < 0.8){ jumps++; if (el) el.classList.add('jump'); }
  });
  document.getElementById('warn').textContent =
    jumps ? `${jumps} tempo jump${jumps>1?'s':''} between chosen groups` : '';
}

document.getElementById('playall').onclick = () => {
  const seq = D.groups.map(g => {
    const p = pick[g.i]; if (!p) return null;
    return g.candidates.find(x => x.pass === p);
  }).filter(Boolean);
  if (!seq.length) return;
  if (!audio.paused){ stop(); return; }
  let i = 0;
  const step = () => { if (i >= seq.length) return;
    play(seq[i].audio, null, () => { i++; step(); }); };
  step();
};

document.getElementById('save').onclick = () => {
  const doc = { session: D.name, chosen: pick, written: new Date().toISOString() };
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 1)],
                                        {type:'application/json'}));
  a.download = 'selection.json';
  a.click();
};

if (D.auto) {
  const card = document.getElementById('autocard');
  card.hidden = false;
  const used = new Set(Object.values(D.auto)).size;
  document.getElementById('autometa').textContent =
    `${Object.keys(D.auto).length} groups from ${used} passes`;
  document.getElementById('autonote').textContent =
    'Picked by tempo alone: at each group, whichever pass is closest in speed to the '
    + 'one before it. Nothing here judges the playing. It is a starting point to argue '
    + 'with, and every group is still yours to change.';
  const ap = document.getElementById('autoplay');
  ap.onclick = () => {
    if (!audio.paused && curBtn === ap) { stop(); ap.textContent = 'Play it'; return; }
    play(D.auto_audio, ap, () => { ap.textContent = 'Play it'; });
    ap.textContent = 'Stop';
  };
  document.getElementById('autoload').onclick = () => {
    pick = Object.assign({}, D.auto); save(); draw();
  };
  if (!D.auto_audio) ap.disabled = true;
}

draw();
</script>
"""

if __name__ == "__main__":
    raise SystemExit(main())
