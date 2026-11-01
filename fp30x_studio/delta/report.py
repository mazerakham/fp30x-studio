"""The delta as a page to read.

A plain document: headings, paragraphs, four charts and a table. The charts are
inline SVG with no library behind them, so the file is one artefact that opens
anywhere and keeps working when nothing is running.

The x axis of every chart is beat position, not time, because beats are the
score's own coordinate and two performances of one piece put the same music at
the same x. Bar numbers are printed along it.
"""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np

from .fit import Delta, local_tempo_curve

__all__ = ["render_html", "write_report"]

CSS = """
:root { color-scheme: light dark; }
body { max-width: 54rem; margin: 3rem auto; padding: 0 1.5rem;
       font: 16px/1.6 Georgia, "Iowan Old Style", serif; color: #1c1c1c;
       background: #fbfaf8; }
h1 { font-size: 1.7rem; line-height: 1.25; margin-bottom: .2rem; }
h2 { font-size: 1.2rem; margin-top: 2.6rem; border-bottom: 1px solid #ddd;
     padding-bottom: .3rem; }
.sub { color: #555; margin-top: 0; }
figure { margin: 1.4rem 0; }
figcaption { font-size: .88rem; color: #555; margin-top: .4rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem;
        font-family: ui-monospace, Menlo, monospace; }
th, td { text-align: right; padding: .25rem .5rem; border-bottom: 1px solid #e6e3dd; }
th:first-child, td:first-child { text-align: left; }
thead th { border-bottom: 2px solid #bbb; }
code { font-family: ui-monospace, Menlo, monospace; font-size: .9em; }
.note { color: #555; font-size: .92rem; }
svg { width: 100%; height: auto; display: block;
      --frame: #c9c4bb; --grid: #e8e4dc; --tick: #7a7469; --ink: var(--light); }
.scroll { overflow-x: auto; }
@media (prefers-color-scheme: dark) {
  body { background: #14161a; color: #e6e3dd; }
  h2 { border-color: #333; }
  th, td { border-color: #2a2d33; }
  thead th { border-color: #555; }
  .sub, .note, figcaption { color: #a6a29b; }
  svg { --frame: #4a505a; --grid: #23272d; --tick: #8d9098; --ink: var(--dark); }
}
"""

W, H = 900, 230
PAD_L, PAD_R, PAD_T, PAD_B = 58, 12, 14, 34


def _axes(xmin, xmax, ymin, ymax, ylabel, xlabel, yticks, xticks):
    def sx(x):
        return PAD_L + (x - xmin) / max(xmax - xmin, 1e-9) * (W - PAD_L - PAD_R)

    def sy(y):
        return PAD_T + (ymax - y) / max(ymax - ymin, 1e-9) * (H - PAD_T - PAD_B)

    tick = ('font-size="11" font-family="monospace" '
            'style="fill:var(--tick)"')
    parts = [f'<rect x="{PAD_L}" y="{PAD_T}" width="{W - PAD_L - PAD_R}" '
             f'height="{H - PAD_T - PAD_B}" fill="none" '
             f'style="stroke:var(--frame)"/>']
    for v in yticks:
        y = sy(v)
        parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" '
                     f'y2="{y:.1f}" style="stroke:var(--grid)"/>')
        parts.append(f'<text x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end" '
                     f'{tick}>{v:g}</text>')
    for v, label in xticks:
        x = sx(v)
        parts.append(f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" '
                     f'y2="{H - PAD_B}" style="stroke:var(--grid)"/>')
        parts.append(f'<text x="{x:.1f}" y="{H - PAD_B + 15}" '
                     f'text-anchor="middle" {tick}>{label}</text>')
    parts.append(f'<text x="{PAD_L}" y="{H - 4}" {tick}>'
                 f'{html.escape(xlabel)}</text>')
    parts.append(f'<text x="4" y="{PAD_T + 10}" {tick}>'
                 f'{html.escape(ylabel)}</text>')
    return parts, sx, sy


def _nice_ticks(lo, hi, n=5):
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** np.floor(np.log10(raw))
    step = min([m * mag for m in (1, 2, 2.5, 5, 10)],
               key=lambda s: abs(s - raw))
    start = np.ceil(lo / step) * step
    return [round(start + k * step, 10)
            for k in range(int((hi - start) / step) + 1)]


def _bar_ticks(beats, measures, n=8):
    if len(beats) == 0:
        return []
    idx = np.linspace(0, len(beats) - 1, min(n, len(beats))).astype(int)
    seen, out = set(), []
    for k in idx:
        m = int(measures[k])
        if m not in seen:
            seen.add(m)
            out.append((float(beats[k]), f"b{m}"))
    return out


def _chart(beats, values, measures, *, ylabel, kind="line", zero=False,
           color=("#2b5d8a", "#8fc0ea")):
    x = np.asarray(beats, float)
    y = np.asarray(values, float)
    ok = np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) == 0:
        return "<p class='note'>no data for this component</p>"
    lo, hi = float(np.min(y)), float(np.max(y))
    if zero:
        r = max(abs(lo), abs(hi)) or 1.0
        lo, hi = -r, r
    if hi == lo:
        hi = lo + 1.0
    pad = 0.08 * (hi - lo)
    lo, hi = lo - pad, hi + pad
    ticks = _nice_ticks(lo, hi)
    parts, sx, sy = _axes(float(x[0]), float(x[-1]), lo, hi, ylabel,
                          "beat position (quarter notes), bar numbers below",
                          ticks, _bar_ticks(beats, measures))
    if zero:
        parts.append(f'<line x1="{PAD_L}" y1="{sy(0):.1f}" x2="{W - PAD_R}" '
                     f'y2="{sy(0):.1f}" style="stroke:var(--tick)" '
                     f'stroke-dasharray="3 3"/>')
    if kind == "line":
        pts = " ".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(x, y))
        parts.append(f'<polyline points="{pts}" fill="none" '
                     f'style="stroke:var(--ink)" stroke-width="1.6"/>')
    else:
        parts.append('<g style="fill:var(--ink)" fill-opacity="0.55">')
        parts += [f'<circle cx="{sx(a):.1f}" cy="{sy(b):.1f}" r="1.5"/>'
                  for a, b in zip(x, y)]
        parts.append("</g>")
    light, dark = color
    return (f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" style="--light:{light};--dark:{dark}">'
            + "".join(parts) + "</svg>")


def _runs(items, key_bar, key_pos):
    """Group a sorted edit list into consecutive runs, for the table."""
    out = []
    for it in items:
        if out and it[key_pos] == out[-1]["_last"] + 1 and it[key_bar] == out[-1]["bar"]:
            out[-1]["n"] += 1
            out[-1]["_last"] = it[key_pos]
            out[-1]["pitches"].append(it["pitch"])
        else:
            out.append({"bar": it[key_bar], "n": 1, "_last": it[key_pos],
                        "pitches": [it["pitch"]]})
    return out


NAMES = "C C# D D# E F F# G G# A A# B".split()


def _pitch(p: int) -> str:
    return f"{NAMES[p % 12]}{p // 12 - 1}"


def _edit_table(d: Delta) -> str:
    ins = _runs(sorted(d.insertions, key=lambda r: r["j"]), "near_measure", "j")
    dels = _runs(sorted(d.deletions, key=lambda r: r["i"]), "measure", "i")
    rows = ([("inserted", r) for r in ins] + [("missed", r) for r in dels])
    rows.sort(key=lambda t: (t[1]["bar"], t[0]))
    if not rows:
        return "<p class='note'>Every note on the page was played, and nothing else was.</p>"
    body = []
    for kind, r in rows:
        pitches = ", ".join(_pitch(p) for p in r["pitches"][:8])
        if len(r["pitches"]) > 8:
            pitches += f", and {len(r['pitches']) - 8} more"
        body.append(f"<tr><td>bar {r['bar']}</td><td>{kind}</td>"
                    f"<td>{r['n']}</td><td style='text-align:left'>"
                    f"{html.escape(pitches)}</td></tr>")
    return ("<div class='scroll'><table><thead><tr><th>where</th><th>what</th>"
            "<th>notes</th><th>pitches</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table></div>")


def render_html(d: Delta) -> str:
    beats = d.component("beat")
    measures = [row["measure"] for row in d.matched]
    spb = d.tempo["seconds_per_beat"]
    grid, qpm = local_tempo_curve(beats, d.component("rubato"), spb,
                                  sigma=d.rubato_sigma_beats)
    gbars = np.interp(grid, beats, np.array(measures, float))

    n = d.norms
    c = d.counts
    summary = [
        ("piece", f"{html.escape(d.score_title)}, {html.escape(d.score_edition)}"),
        ("score", f"<code>{html.escape(d.score_source)}</code>"),
        ("performance", f"<code>{html.escape(d.performance_source)}</code>"),
        ("fitted tempo", f"{d.tempo['qpm']:.2f} quarter notes per minute "
                         f"({spb:.4f} s per beat)"),
        ("matched", f"{c['matched']} of {d.score_notes} score notes "
                    f"({100 * c['match_rate_score']:.1f}%), "
                    f"{c['matched']} of {d.performance_notes} played notes "
                    f"({100 * c['match_rate_performed']:.1f}%)"),
        ("edit counts", f"{c['insertions']} inserted, {c['deletions']} missed"),
        ("distance from the page",
         f"timing rms {n['timing']['rms']:.4f} beats "
         f"({n['timing_rms_ms']:.1f} ms), of which rubato "
         f"{n['rubato']['rms']:.4f} and touch {n['micro']['rms']:.4f} beats "
         f"({n['micro_rms_ms']:.1f} ms); articulation rms "
         f"{n['articulation']['rms']:.4f} in log units; velocity rms "
         f"{n['velocity_centred']['rms']:.4f} about a mean of "
         f"{n['mean_velocity']:.3f}"),
        ("norms", f"|r| = {n['timing']['l2']:.3f}, "
                  f"|rubato| = {n['rubato']['l2']:.3f}, "
                  f"|touch| = {n['micro']['l2']:.3f}, "
                  f"|articulation| = {n['articulation']['l2']:.3f}, "
                  f"|velocity centred| = {n['velocity_centred']['l2']:.3f}"),
    ]
    dl = "".join(f"<tr><td>{k}</td><td style='text-align:left'>{v}</td></tr>"
                 for k, v in summary)

    charts = [
        ("Tempo", _chart(grid, qpm, gbars, ylabel="qpm", kind="line",
                         color=("#8a4b2b", "#e0a184")),
         f"Local tempo implied by the smoothed timing residual, "
         f"60 / (sigma (1 + rho')), against the fitted constant "
         f"{d.tempo['qpm']:.2f} qpm. Above the line is faster than the fit."),
        ("Rubato", _chart(beats, d.component("rubato"), measures,
                          ylabel="beats", kind="line", zero=True),
         "The smooth part of the onset residual, a Gaussian of width "
         f"{d.rubato_sigma_beats:g} beats. Positive is behind the baseline."),
        ("Touch", _chart(beats, d.component("micro"), measures,
                         ylabel="beats", kind="dot", zero=True,
                         color=("#3f6f4a", "#8fd0a2")),
         "What is left of the onset residual after the rubato is removed: the "
         "placement of each note inside its own moment."),
        ("Velocity", _chart(beats, d.component("velocity"), measures,
                            ylabel="0 to 1", kind="dot", color=("#5b3f8a", "#bda6e8")),
         "Struck velocity of every matched note. The baseline is the flat line "
         f"at {n['mean_velocity']:.3f}."),
        ("Articulation", _chart(beats, d.component("articulation"), measures,
                                ylabel="log ratio", kind="dot", zero=True,
                                color=("#8a2b4b", "#e893ad")),
         "log(played length / notated length). Zero is exactly as written, "
         "positive is held over, negative is detached. Grace notes have no "
         "notated length and are not plotted."),
    ]
    body = []
    for title, svg, cap in charts:
        body.append(f"<h2>{title}</h2><figure>{svg}"
                    f"<figcaption>{html.escape(cap)}</figcaption></figure>")

    extra = ""
    if d.matching_note:
        extra = f"<p class='note'>{html.escape(d.matching_note)}</p>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Delta: {html.escape(d.score_title)}</title>
<style>{CSS}</style>
</head>
<body>
<h1>{html.escape(d.score_title)}</h1>
<p class="sub">The difference between the page and one performance of it.</p>

<h2>The fit</h2>
<div class="scroll"><table><tbody>{dl}</tbody></table></div>
{extra}
<p class="note">The baseline is the score played at one constant tempo, with
one constant velocity and every note held exactly as long as it is written.
Every chart below is a departure from that. Timing is measured in quarter
notes, so it does not change if the whole performance is faster or slower.</p>

{''.join(body)}

<h2>Notes added and notes missed</h2>
<p class="note">These are the part of the difference that no vector can carry:
a note that is on the page and was not played, or was played and is not on the
page. Consecutive ones in the same bar are grouped. A written out ornament that
the page states as a symbol appears here as an insertion, which is a fact about
the notation and not about the playing.</p>
{_edit_table(d)}

<h2>How the notes were matched</h2>
<p class="note">{html.escape(_matching_prose(d))}</p>
<pre class="note" style="white-space:pre-wrap">{html.escape(d.parse_report)}</pre>
</body>
</html>
"""


def _matching_prose(d: Delta) -> str:
    cen = d.event_census or {}
    bits = [
        "Stage one is the event level Smith-Waterman alignment in "
        "fp30x_studio.align.correspond, run over onset clusters.",
    ]
    if cen:
        bits.append(
            f"It paired {cen.get('matched', 0)} of {cen.get('events_a', 0)} "
            f"score events with {cen.get('events_b', 0)} played events, leaving "
            f"{cen.get('dropped_in_core_a', 0)} score events and "
            f"{cen.get('added_in_core_b', 0)} played events unpaired inside the "
            f"span it accepted.")
    bits.append(
        "Stage two is a note level Needleman-Wunsch banded on that result: only "
        "equal MIDI pitches may pair, and the band restricts the search to the "
        f"region the event alignment points at. {d.counts.get('at_band_edge', 0)} "
        "matches landed against the edge of the band, which is the number to "
        "watch if the band is too narrow.")
    bits.append(
        f"Stage three paired {d.counts.get('voice_crossings_repaired', 0)} "
        "leftovers of equal pitch that lie within a beat of each other but in "
        "the wrong onset order for a monotone alignment to reach, which is what "
        "a melody note delayed behind its own bass looks like.")
    return " ".join(bits)


def write_report(d: Delta, path: str | Path) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(d), encoding="utf-8")
    return path
