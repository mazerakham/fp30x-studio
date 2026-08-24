"""The viewer for an FPX bundle.

Generated from `fpx.json` into `index.html` at the bundle root, so that
pointing any static server at the bundle directory renders it. The viewer is
part of the format, not a separate tool: a bundle you cannot open is a
directory of numbers.

Nothing here is authored by hand and nothing here is a source of truth. Delete
index.html and regenerate it; the manifest is the record.
"""
from __future__ import annotations

import json
from pathlib import Path

CSS = """
:root{
  --bg:#faf9f7; --panel:#fff; --ink:#1b1a18; --dim:#6b6763; --line:#e3dfd9;
  --accent:#8a5a2b; --warm:#c8892f; --cool:#4a6b8a; --ok:#3f7a4a; --bad:#a33f3f;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#16151a; --panel:#1e1d23; --ink:#ece9e4; --dim:#9a948d; --line:#33313a;
  --accent:#d3a06a; --warm:#e0aa5c; --cool:#7fa3c4; --ok:#7fb98c; --bad:#d98080;
}}
:root[data-theme=dark]{
  --bg:#16151a; --panel:#1e1d23; --ink:#ece9e4; --dim:#9a948d; --line:#33313a;
  --accent:#d3a06a; --warm:#e0aa5c; --cool:#7fa3c4; --ok:#7fb98c; --bad:#d98080;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:28px 24px 96px}
nav{display:flex;gap:16px;margin-bottom:20px;font-size:14px}
nav a{color:var(--accent);text-decoration:none;padding:5px 12px;
  border:1px solid var(--line);border-radius:20px}
nav a.here{background:var(--accent);color:var(--bg);border-color:var(--accent)}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--dim);margin:0 0 24px}
.facts{display:flex;flex-wrap:wrap;gap:8px 28px;padding:14px 18px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;margin-bottom:22px}
.fact b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--dim);font-weight:600}
.fact span{font-variant-numeric:tabular-nums}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:12px 18px;margin-bottom:22px}
summary{cursor:pointer;font-weight:600}
details ul{margin:10px 0 2px;padding-left:18px;color:var(--dim)}
.timeline{position:relative;height:64px;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;margin-bottom:8px;overflow:hidden}
.tl{position:absolute;top:8px;bottom:8px;border-radius:3px;cursor:pointer;
  background:var(--cool);opacity:.75}
.tl:hover{opacity:1;outline:2px solid var(--ink)}
.tlnote{color:var(--dim);font-size:12px;margin-bottom:26px}
.bar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
select,button{font:inherit;color:var(--ink);background:var(--panel);
  border:1px solid var(--line);border-radius:7px;padding:6px 11px;cursor:pointer}
button:hover{border-color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin-bottom:12px;scroll-margin-top:16px}
.card.stub{opacity:.5}
.chead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.cid{font-weight:700;font-variant-numeric:tabular-nums}
.cwin{color:var(--dim);font-variant-numeric:tabular-nums}
.tag{font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--line);
  color:var(--dim)}
.tag.long{border-color:var(--ok);color:var(--ok)}
.env{width:100%;height:56px;display:block;margin:12px 0 10px}
.metrics{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:10px 16px}
.m b{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--dim);font-weight:600}
.m span{font-variant-numeric:tabular-nums}
.meter{height:4px;background:var(--line);border-radius:2px;margin-top:4px;overflow:hidden}
.meter i{display:block;height:100%;background:var(--accent)}
audio{width:100%;margin-top:12px;height:34px}
.lab{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center}
.lab input{flex:1;min-width:200px;font:inherit;padding:6px 10px;border-radius:7px;
  border:1px solid var(--line);background:var(--bg);color:var(--ink)}
.v{padding:4px 10px;border-radius:7px;border:1px solid var(--line);font-size:13px}
.v.on[data-v=keep]{background:var(--ok);border-color:var(--ok);color:#fff}
.v.on[data-v=maybe]{background:var(--warm);border-color:var(--warm);color:#221}
.v.on[data-v=drop]{background:var(--bad);border-color:var(--bad);color:#fff}
footer{margin-top:36px;color:var(--dim);font-size:13px;border-top:1px solid var(--line);
  padding-top:16px}
code{background:var(--line);padding:1px 6px;border-radius:4px;font-size:13px}
"""

JS = """
const M = window.FPX;
const KEY = 'fpx:' + M.capture.name;
// labels.json on disk is the durable record; localStorage is the live edit
// layer on top of it. A cleared browser loses edits made since the last save
// to disk, never the saved ones.
let labels = Object.assign({}, window.FPX_LABELS || {});
try { Object.assign(labels, JSON.parse(localStorage.getItem(KEY) || '{}')); }
catch (e) {}
function save(){ try { localStorage.setItem(KEY, JSON.stringify(labels)); } catch(e){} }

function envSVG(c){
  const e = c.envelope, n = e.buckets || 0;
  if(!n) return '';
  const w = 700, h = 56, bw = w / n;
  const dmax = Math.max(...e.density, 1);
  let s = `<svg class="env" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`;
  for(let i=0;i<n;i++){
    const d = e.density[i];
    if(!d) continue;
    const bh = Math.max(2, d / dmax * (h - 6));
    // Colour is velocity, height is note density. Two dimensions, one bar.
    const v = e.velocity[i] / 127;
    const col = `hsl(${28 + (1-v)*180} ${35 + v*40}% ${38 + v*22}%)`;
    s += `<rect x="${(i*bw).toFixed(2)}" y="${(h-bh).toFixed(2)}" `
      +  `width="${(bw*0.86).toFixed(2)}" height="${bh.toFixed(2)}" fill="${col}"/>`;
  }
  return s + '</svg>';
}

function metric(label, value, frac){
  const bar = frac == null ? '' :
    `<div class="meter"><i style="width:${Math.max(0,Math.min(1,frac))*100}%"></i></div>`;
  return `<div class="m"><b>${label}</b><span>${value}</span>${bar}</div>`;
}

function card(c){
  const p = c.timbre.performed, w = c.window, mu = c.musical;
  const ped = p.pedal ? p.pedal.down_fraction : null;
  const stub = w.duration_s < 2;
  const wav = c.files.wav ? c.files.wav.path : null;
  const lb = labels[c.id] || {verdict:null, note:''};
  const arc = p.dynamic_arc;
  return `<div class="card${stub?' stub':''}" id="${c.id}">
    <div class="chead">
      <span class="cid">${c.id}</span>
      <span class="cwin">${w.start_mmss}–${w.end_mmss} · ${w.duration_s.toFixed(1)}s</span>
      <span class="tag${w.duration_s>120?' long':''}">${mu.notes} notes</span>
      <span class="tag">${mu.key_signature}</span>
      ${stub ? '<span class="tag">stub</span>' : ''}
    </div>
    ${envSVG(c)}
    <div class="metrics">
      ${metric('pedal down', ped==null?'—':(ped*100).toFixed(0)+'%', ped)}
      ${metric('legato', p.legato_index==null?'—':p.legato_index.toFixed(2), p.legato_index)}
      ${metric('velocity', p.velocity?Math.round(p.velocity.mean):'—', p.velocity?p.velocity.mean/127:null)}
      ${metric('dyn spread', p.velocity?'±'+Math.round(p.velocity.sd):'—', p.velocity?p.velocity.sd/50:null)}
      ${metric('dyn arc', arc==null?'—':(arc>0?'+':'')+Math.round(arc), null)}
      ${metric('voicing lead', p.voicing_top_lead==null?'—':'+'+Math.round(p.voicing_top_lead), p.voicing_top_lead/25)}
      ${metric('attacks/s', p.attack_rate_hz==null?'—':p.attack_rate_hz.toFixed(1), (p.attack_rate_hz||0)/12)}
      ${metric('in key', (mu.in_key_fraction*100).toFixed(0)+'%', mu.in_key_fraction)}
      ${metric('breaks', mu.stops, null)}
    </div>
    ${wav ? `<audio controls preload="none" src="${wav}"></audio>` : ''}
    <div class="lab" data-id="${c.id}">
      <button class="v${lb.verdict==='keep'?' on':''}" data-v="keep">keep</button>
      <button class="v${lb.verdict==='maybe'?' on':''}" data-v="maybe">maybe</button>
      <button class="v${lb.verdict==='drop'?' on':''}" data-v="drop">drop</button>
      <input placeholder="which bars? what went wrong?" value="${(lb.note||'').replace(/"/g,'&quot;')}">
    </div>
  </div>`;
}

function render(){
  const how = document.getElementById('sort').value;
  const cs = M.clips.slice();
  if(how==='time') cs.sort((a,b)=>a.window.start_s-b.window.start_s);
  if(how==='long') cs.sort((a,b)=>b.window.duration_s-a.window.duration_s);
  if(how==='dense') cs.sort((a,b)=>b.musical.density_hz-a.musical.density_hz);
  if(how==='clean') cs.sort((a,b)=>a.musical.stops-b.musical.stops||b.window.duration_s-a.window.duration_s);
  const hide = document.getElementById('hidestubs').checked;
  document.getElementById('cards').innerHTML =
    cs.filter(c=>!(hide && c.window.duration_s<2)).map(card).join('');
}

function timeline(){
  const total = M.capture.duration_s;
  const el = document.getElementById('tl');
  el.innerHTML = M.clips.map(c=>{
    const l = c.window.start_s/total*100, w = Math.max(0.25, c.window.duration_s/total*100);
    const p = c.timbre.performed.pedal;
    const hue = p ? 210 - p.down_fraction*180 : 210;
    return `<div class="tl" style="left:${l}%;width:${w}%;background:hsl(${hue} 45% 48%)"
      title="${c.id} ${c.window.start_mmss} ${c.window.duration_s.toFixed(0)}s"
      data-go="${c.id}"></div>`;
  }).join('');
}

document.addEventListener('click', e=>{
  const go = e.target.closest('[data-go]');
  if(go){ const t=document.getElementById(go.dataset.go);
          if(t) t.scrollIntoView({behavior:'smooth',block:'center'}); return; }
  const v = e.target.closest('.v');
  if(v){
    const id = v.closest('.lab').dataset.id;
    labels[id] = labels[id] || {verdict:null, note:''};
    labels[id].verdict = labels[id].verdict === v.dataset.v ? null : v.dataset.v;
    save(); render(); return;
  }
});
document.addEventListener('input', e=>{
  const i = e.target.closest('.lab input');
  if(!i) return;
  const id = i.closest('.lab').dataset.id;
  labels[id] = labels[id] || {verdict:null, note:''};
  labels[id].note = i.value; save();
});
document.getElementById('sort').addEventListener('change', render);
document.getElementById('hidestubs').addEventListener('change', render);
document.getElementById('export').addEventListener('click', ()=>{
  const out = JSON.stringify({capture:M.capture.name, labels}, null, 2);
  navigator.clipboard.writeText(out).then(()=>{
    const b=document.getElementById('export'); const t=b.textContent;
    b.textContent='copied — paste into labels.json'; setTimeout(()=>b.textContent=t,2200);
  });
});
timeline(); render();
"""


def build(bundle_dir: Path) -> Path:
    m = json.loads((bundle_dir / "fpx.json").read_text())
    c = m["capture"]
    pc = m["piece"]

    facts = [
        ("captured", c["started_utc"]),
        ("length", c["duration_mmss"]),
        ("notes", f"{c['notes']:,}"),
        ("pedal messages", f"{c['cc64_messages']:,}"),
        ("attempts", str(len(m["clips"]))),
        ("reconnect holes", str(c["setup_changed_reconnects"])),
    ]
    facts_html = "".join(
        f'<div class="fact"><b>{k}</b><span>{v}</span></div>' for k, v in facts)
    gaps = "".join(f"<li>{g}</li>" for g in m["known_gaps"])

    lab_path = bundle_dir / "labels.json"
    seed = {}
    if lab_path.exists():
        seed = json.loads(lab_path.read_text()).get("labels", {})

    r = m["clips"][0]["timbre"]["rendered"] or {}
    rend = (f"{r.get('engine','—')} · {r.get('soundfont', r.get('preset_name','—'))} "
            f"· gain {r.get('gain','—')}")

    html = f"""<!doctype html>
<meta charset="utf-8">
<title>{m['title']}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style>
<div class="wrap">
<nav><a class="here" href="index.html">Clips</a><a href="alignment.html">Alignment</a></nav>
<h1>{pc['work']}</h1>
<p class="sub">{pc['composer']} · {pc['key']} · {pc['composed']}, published {pc['published']}
 — practice session of {c['started_utc'][:10]}</p>

<div class="facts">{facts_html}</div>

<details><summary>What this cannot tell you</summary><ul>{gaps}</ul></details>

<div class="timeline" id="tl"></div>
<p class="tlnote">The whole {c['duration_mmss']}. Each block is one attempt, width is its
 length, colour is how much of it was pedalled — blue dry, orange held. Click one to jump.</p>

<div class="bar">
  <select id="sort">
    <option value="time">in time order</option>
    <option value="long">longest first</option>
    <option value="clean">fewest breaks first</option>
    <option value="dense">densest first</option>
  </select>
  <label><input type="checkbox" id="hidestubs" checked> hide stubs under 2s</label>
  <button id="export">copy labels as JSON</button>
</div>

<div id="cards"></div>

<footer>
Rendered with {rend}. Bars are note density, colour is velocity — warm is loud.<br>
Verdicts seed from <code>labels.json</code> in the bundle and are edited in this browser.
Press <b>copy labels as JSON</b> and paste into <code>labels.json</code> to make new edits
permanent — until you do, an edit lives only in this browser.<br>
Generated from <code>fpx.json</code> by <code>fp30x_studio.fpx.viewer</code>. Never edit this
file — regenerate it.
</footer>
</div>
<script>window.FPX = {json.dumps(m)}; window.FPX_LABELS = {json.dumps(seed)};</script>
<script>{JS}</script>
"""
    out = bundle_dir / "index.html"
    out.write_text(html)
    return out
