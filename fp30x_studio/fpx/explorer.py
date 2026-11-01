"""The alignment explorer: a second page in the bundle, generated from the data.

`index.html` answers "what did I play". This answers "which of these are the
same music", and shows the evidence at each of the three stages rather than
only the conclusion — the correlation curve whose spike proposed a pair, the
census of what the correspondence could not match, and the warp that fitted the
rubato between them.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..align import load_segments
from ..align import corr as _corr
from ..align.passages import components, passages

__all__ = ["analyse", "build"]


def analyse(take: str, *, gap: float = 3.0) -> dict:
    segs = load_segments(take, gap=gap)
    by = {s.index: s for s in segs}
    M, spans = _corr.gram(segs)
    links = passages(segs, top=1)

    curves = {}
    for l in links:
        (i0, i1) = spans[[s.index for s in segs].index(l.a)]
        (j0, j1) = spans[[s.index for s in segs].index(l.b)]
        taus, c = _corr.diagonal_sums(M[i0:i1, j0:j1])
        n, m = i1 - i0, j1 - j0
        cnt = np.array([min(n, m, n + t, m - t) for t in taus])
        keep = cnt >= 40
        # Downsample for the page: the shape is the point, not every lag.
        idx = np.where(keep)[0]
        step = max(1, len(idx) // 400)
        idx = idx[::step]
        curves[f"{l.a}-{l.b}"] = {
            "tau": [int(taus[k]) for k in idx],
            "c": [round(float(c[k]), 4) for k in idx],
            "peak_tau": l.candidate.tau,
        }

    warps = {}
    for l in links:
        if not (l.verdict and l.verdict.trustworthy):
            continue
        w = l.alignment.warp
        t = np.linspace(0.0, w.T1, 120)
        warps[f"{l.a}-{l.b}"] = {
            "t": [round(float(x), 3) for x in t],
            "phi": [round(float(y), 3) for y in w(t)],
            "T1": round(float(w.T1), 3), "T2": round(float(w.T2), 3),
            "mean_rate": round(float(w.mean_rate), 4),
            "slopes": [round(float(x), 4) for x in w.slopes[:400]],
        }

    return {
        "take": str(take),
        "gap_s": gap,
        "n_events": int(M.shape[0]),
        "gemm_flops": int(12 * M.shape[0] ** 2),
        "segments": [
            {"index": s.index, "events": len(s.events), "notes": s.n_notes,
             "t0": round(s.t0, 2), "duration": round(s.duration, 2)}
            for s in segs
        ],
        "links": [l.row() for l in links],
        "curves": curves,
        "warps": warps,
        "groups": [[int(i) for i in g] for g in components(links)],
        "thresholds": {"min_matched": 40, "min_core_rate": 0.45,
                       "candidate_z": 6.0, "candidate_overlap": 40},
    }


PAGE_CSS = """
:root{--bg:#faf9f7;--panel:#fff;--ink:#1b1a18;--dim:#6b6763;--line:#e3dfd9;
  --accent:#8a5a2b;--ok:#3f7a4a;--bad:#a33f3f;--warm:#c8892f;--cool:#4a6b8a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#16151a;--panel:#1e1d23;--ink:#ece9e4;--dim:#9a948d;--line:#33313a;
  --accent:#d3a06a;--ok:#7fb98c;--bad:#d98080;--warm:#e0aa5c;--cool:#7fa3c4}}
:root[data-theme=dark]{--bg:#16151a;--panel:#1e1d23;--ink:#ece9e4;--dim:#9a948d;
  --line:#33313a;--accent:#d3a06a;--ok:#7fb98c;--bad:#d98080;--warm:#e0aa5c;--cool:#7fa3c4}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 24px 90px}
nav{display:flex;gap:16px;margin-bottom:22px;font-size:14px}
nav a{color:var(--accent);text-decoration:none;padding:5px 12px;border:1px solid var(--line);
  border-radius:20px}
nav a.here{background:var(--accent);color:var(--bg);border-color:var(--accent)}
h1{font-size:25px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:18px;margin:34px 0 10px;letter-spacing:-.01em}
p.sub{color:var(--dim);margin:0 0 20px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:16px 18px;margin-bottom:16px}
.math{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;
  background:var(--line);padding:2px 7px;border-radius:4px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:right;padding:6px 9px;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums}
th:first-child,td:first-child{text-align:left}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);font-weight:600}
tr.no td{opacity:.5}
.pill{font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--line)}
.pill.ok{color:var(--ok);border-color:var(--ok)}
.pill.no{color:var(--dim)}
.why{font-size:12.5px;color:var(--dim);text-align:left}
.grid{display:grid;gap:14px}
@media(min-width:820px){.grid.two{grid-template-columns:1fr 1fr}}
.cap{font-size:12.5px;color:var(--dim);margin:6px 2px 0}
svg{display:block;width:100%;height:auto;overflow:visible}
.mx{display:grid;gap:1px;margin:10px 0}
.mx div{aspect-ratio:1;border-radius:1px}
.mx div.hd{background:none;font-size:8px;color:var(--dim);display:flex;
  align-items:center;justify-content:center}
.grp{border-left:3px solid var(--accent);padding-left:14px;margin:14px 0}
.grp h3{margin:0 0 4px;font-size:15px}
.clips{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.clip{border:1px solid var(--line);border-radius:9px;padding:8px 11px;font-size:13px;
  background:var(--bg);min-width:150px}
.clip b{font-variant-numeric:tabular-nums}
.clip audio{width:100%;height:30px;margin-top:6px}
.clip a{color:var(--accent);text-decoration:none;font-size:12px}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--dim);font-size:13px}
code{background:var(--line);padding:1px 6px;border-radius:4px;font-size:13px}
"""


def _curve_svg(cur: dict, w=460, h=110) -> str:
    tau, c = cur["tau"], cur["c"]
    if not tau:
        return ""
    lo, hi = min(c), max(c)
    rng = (hi - lo) or 1.0
    t0, t1 = min(tau), max(tau)
    tr = (t1 - t0) or 1.0
    pts = " ".join(f"{(t - t0) / tr * w:.1f},{h - (v - lo) / rng * (h - 10) - 5:.1f}"
                   for t, v in zip(tau, c))
    px = (cur["peak_tau"] - t0) / tr * w
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<line x1="{px:.1f}" y1="0" x2="{px:.1f}" y2="{h}" '
            f'stroke="var(--warm)" stroke-width="1.5" stroke-dasharray="3 3"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--cool)" '
            f'stroke-width="1.6" stroke-linejoin="round"/></svg>')


def _warp_svg(wp: dict, w=460, h=170) -> str:
    t, phi = wp["t"], wp["phi"]
    T1, T2 = wp["T1"] or 1.0, wp["T2"] or 1.0
    pts = " ".join(f"{x / T1 * w:.1f},{h - y / T2 * h:.1f}" for x, y in zip(t, phi))
    return (f'<svg viewBox="0 0 {w} {h}">'
            f'<line x1="0" y1="{h}" x2="{w}" y2="0" stroke="var(--line)" '
            f'stroke-width="1.5" stroke-dasharray="4 4"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--accent)" '
            f'stroke-width="2" stroke-linejoin="round"/></svg>')


def build(bundle_dir: Path) -> Path:
    d = json.loads((bundle_dir / "results" / "alignment.json").read_text())
    m = json.loads((bundle_dir / "fpx.json").read_text())
    clip_by = {c["index"]: c for c in m["clips"]}
    seg_by = {s["index"]: s for s in d["segments"]}

    # --- the pair table -------------------------------------------------
    rows = []
    for r in d["links"]:
        ok = r["trustworthy"]
        res = (f"{r['residual_rms_s']:.3f}" if r["residual_informative"]
               else "—")
        rows.append(
            f"<tr class=\"{'' if ok else 'no'}\">"
            f"<td>s{r['a']:02d} ~ s{r['b']:02d}</td>"
            f"<td>{r['z']:.1f}</td><td>{r['cos']:.2f}</td>"
            f"<td>{r['matched']}</td>"
            f"<td>{(r['core_rate'] or 0) * 100:.0f}%</td>"
            f"<td>{r['mean_rate']:.3f}</td>"
            f"<td>{res}</td>"
            f"<td><span class=\"pill {'ok' if ok else 'no'}\">"
            f"{'trusted' if ok else 'rejected'}</span></td>"
            f"<td class=\"why\">{r['why_not'] or r['error'] or ''}</td></tr>")

    # --- the matrix -----------------------------------------------------
    idx = sorted(seg_by)
    best = {}
    for r in d["links"]:
        best[(r["a"], r["b"])] = best[(r["b"], r["a"])] = r
    cells = ['<div class="hd"></div>'] + [f'<div class="hd">{i}</div>' for i in idx]
    for i in idx:
        cells.append(f'<div class="hd">{i}</div>')
        for j in idx:
            r = best.get((i, j))
            if i == j:
                cells.append('<div style="background:var(--line)"></div>')
            elif r:
                a = min(1.0, r["z"] / 12)
                col = ("var(--ok)" if r["trustworthy"] else "var(--dim)")
                cells.append(f'<div style="background:{col};opacity:{0.15 + a * 0.85:.2f}" '
                             f'title="s{i:02d}~s{j:02d} z={r["z"]:.1f} '
                             f'matched={r["matched"]}"></div>')
            else:
                cells.append('<div style="background:var(--bg)"></div>')
    mx = (f'<div class="mx" style="grid-template-columns:repeat({len(idx) + 1},1fr)">'
          + "".join(cells) + "</div>")

    # --- curves and warps ----------------------------------------------
    trusted = [r for r in d["links"] if r["trustworthy"]]
    figs = []
    for r in trusted[:6]:
        k = f"{r['a']}-{r['b']}"
        cur = d["curves"].get(k)
        wp = d["warps"].get(k)
        figs.append(
            f'<div class="panel"><b>s{r["a"]:02d} ~ s{r["b"]:02d}</b>'
            f'{_curve_svg(cur) if cur else ""}'
            f'<p class="cap">Stage 1 — mean cosine along each diagonal. The dashed '
            f'line is the peak the GEMM proposed, at &tau; = {r["tau"]:+d}.</p>'
            f'{_warp_svg(wp) if wp else ""}'
            f'<p class="cap">Stage 3 — &phi; against the straight line. '
            f'Mean rate {r["mean_rate"]:.3f}× — s{r["b"]:02d} runs '
            f'{"faster" if r["mean_rate"] > 1 else "slower"} than s{r["a"]:02d}. '
            f'{r["matched"]} onsets matched, {(r["core_rate"] or 0) * 100:.0f}% of the '
            f'shared core.</p></div>')

    # --- groups ---------------------------------------------------------
    grps = []
    for n, g in enumerate(d["groups"], 1):
        clips = []
        for i in g:
            c = clip_by.get(i)
            s = seg_by.get(i, {})
            wav = c["files"]["wav"]["path"] if c and "wav" in c["files"] else None
            clips.append(
                f'<div class="clip"><b>s{i:02d}</b> · {c["window"]["start_mmss"] if c else ""}'
                f' · {s.get("duration", 0):.0f}s<br>'
                f'<a href="index.html#polfan-s{i:02d}">open in clips</a>'
                + (f'<audio controls preload="none" src="{wav}"></audio>' if wav else "")
                + "</div>")
        span = f'{min(seg_by[i]["t0"] for i in g) / 60:.0f}–{max(seg_by[i]["t0"] for i in g) / 60:.0f} min'
        grps.append(
            f'<div class="grp"><h3>Passage {n} — {len(g)} attempts, spread over {span}</h3>'
            f'<div class="clips">{"".join(clips)}</div></div>')

    unlinked = [i for i in idx if not any(i in g for g in d["groups"])]

    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Alignment — Polonaise-Fantaisie, 2026-08-24</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{PAGE_CSS}</style>
<div class="wrap">
<nav><a href="index.html">Clips</a><a class="here" href="alignment.html">Alignment</a></nav>

<h1>Which attempts are the same music</h1>
<p class="sub">{len(d['segments'])} attempts · {d['n_events']:,} onset events ·
one {d['gemm_flops'] / 1e9:.2f} GFLOP matrix multiply · {len(d['links'])} pairs fitted ·
<b>{len(trusted)} trusted</b> · {len(d['groups'])} passages found</p>

<div class="panel">
<b>Three stages, cheapest first.</b>
<p>Every onset becomes its pitch-class indicator in <span class="math">R¹²</span>,
L2-normalised; stack all {d['n_events']:,} of them into <span class="math">Q</span> and form
<span class="math">M = QQᵀ</span>. That single GEMM contains every pair of attempts as a
block. Shared music appears as a <b>diagonal streak</b> — matching passages advance both
indices together — and summing along diagonals,
<span class="math">c(τ) = Σᵢ M[i, i+τ]</span>, collapses each streak to a spike.
<b>That sum is exactly the cross-correlation</b>, so the spikes are the offsets.</p>
<p>Correlation only proposes. A diagonal is a rigid translation, so it survives a
tempo difference (working in event index, not seconds) but not an added ornament or a
dropped note, each of which shears it. Stage&nbsp;2 (<code>correspond</code>) finds the real
onset pairing by local alignment and counts what it could not match; stage&nbsp;3
(<code>warp</code>) fits the least-energy homeomorphism <span class="math">φ</span> through
that correspondence.</p>
</div>

<h2>The matrix</h2>
{mx}
<p class="cap">Every proposed pair, green where the fit was trusted and grey where it was
rejected; opacity is the correlation z-score. Blank means the GEMM proposed nothing
above z = {d['thresholds']['candidate_z']:.0f}. Hover for the numbers.</p>

<h2>Every pair, and why each was kept or dropped</h2>
<div class="panel"><table>
<tr><th>pair</th><th>z</th><th>cos</th><th>matched</th><th>core</th><th>rate</th>
<th>resid</th><th></th><th class="why">verdict</th></tr>
{''.join(rows)}
</table></div>
<div class="panel">
<b>Why <code>resid</code> is blank on most rows.</b> When GCV drives
<span class="math">λ</span> to the floor of the sweep, <span class="math">φ</span> becomes the
interpolant through every matched onset, so the residual is zero <i>by construction</i> and
"100% explained" would be true for three matched onsets as readily as for three hundred. The
number carries no information there, so it is not shown. What a link is judged on instead is
how much music it actually matched: at least {d['thresholds']['min_matched']} onsets and
{d['thresholds']['min_core_rate'] * 100:.0f}% of the shared core.
</div>

<h2>The evidence, pair by pair</h2>
<div class="grid two">{''.join(figs)}</div>

<h2>Passages</h2>
{''.join(grps)}
<p class="cap">Groups are connected components over the trusted links, so transitivity
carries: A–B and B–C puts A and C together even where they share nothing directly.
Unlinked attempts: {', '.join('s%02d' % i for i in unlinked) or 'none'} — mostly the
one- and two-second stubs, which have too few events to match anything.</p>

<footer>
<b>These are not measure numbers, and cannot be until a symbolic score exists.</b> Every
offset on this page is in <i>event index within another attempt</i>. The 1846 Brandus first
edition and the autograph are downloaded as page scans
(<code>~/workspace/audio/scores/chopin-op61/</code>), and a scan carries no note data — the
bar numbers need an OMR pass or a MusicXML encoding. When one exists it enters as one more
row block in the same <span class="math">Q</span>, and everything on this page carries over
unchanged.<br><br>
Generated from <code>results/alignment.json</code> by <code>fp30x_studio.fpx.explorer</code>.
</footer>
</div>
"""
    out = bundle_dir / "alignment.html"
    out.write_text(html)
    return out
