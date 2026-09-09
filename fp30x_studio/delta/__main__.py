"""python -m fp30x_studio.delta -- fit, compare and report deltas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compare import compare_deltas, format_comparison
from .fit import fit_delta, read_delta, write_delta
from .report import write_report


def _summary(d) -> str:
    n, c, t = d.norms, d.counts, d.tempo
    return "\n".join([
        f"piece        {d.score_title} ({d.score_edition})",
        f"score        {d.score_source}",
        f"performance  {d.performance_source}",
        f"tempo        {t['qpm']:.2f} qpm ({t['seconds_per_beat']:.4f} s/beat), "
        f"onset residual rms {t['rms_residual_s'] * 1000:.0f} ms",
        f"matched      {c['matched']} of {d.score_notes} score notes "
        f"({100 * c['match_rate_score']:.1f}%), "
        f"{c['matched']} of {d.performance_notes} played "
        f"({100 * c['match_rate_performed']:.1f}%)",
        f"edits        {c['insertions']} insertions, {c['deletions']} deletions",
        f"norms        |r| {n['timing']['l2']:.3f}  "
        f"|rubato| {n['rubato']['l2']:.3f}  |touch| {n['micro']['l2']:.3f}  "
        f"|articulation| {n['articulation']['l2']:.3f}  "
        f"|velocity centred| {n['velocity_centred']['l2']:.3f}",
        f"rms          timing {n['timing']['rms']:.4f} beats "
        f"({n['timing_rms_ms']:.1f} ms), touch {n['micro']['rms']:.4f} beats "
        f"({n['micro_rms_ms']:.1f} ms), articulation "
        f"{n['articulation']['rms']:.4f}, velocity {n['velocity_centred']['rms']:.4f} "
        f"about {n['mean_velocity']:.3f}",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m fp30x_studio.delta",
        description="A performance as the score plus a difference vector.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fit", help="fit a delta from a score and a take")
    f.add_argument("score", help="a .krn, .xml or .mid score")
    f.add_argument("performance", help="a .mid or .fp30x take")
    f.add_argument("--out", required=True, help="where to write the delta JSON")
    f.add_argument("--html", help="also write the report to this path")
    f.add_argument("--band", type=int, default=None,
                   help="half width of the note level DP band, in notes")

    c = sub.add_parser("compare", help="compare two deltas of one piece")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--json", action="store_true", help="print the raw record")

    r = sub.add_parser("report", help="render a delta as an HTML document")
    r.add_argument("delta")
    r.add_argument("--html", required=True)

    a = ap.parse_args(argv)

    if a.cmd == "fit":
        d = fit_delta(a.score, a.performance, band=a.band)
        out = write_delta(d, a.out)
        print(_summary(d))
        print(f"wrote        {out}")
        if a.html:
            print(f"wrote        {write_report(d, a.html)}")
        return 0

    if a.cmd == "compare":
        rec = compare_deltas(read_delta(a.a), read_delta(a.b))
        print(json.dumps(rec, indent=1) if a.json else format_comparison(rec))
        return 0

    print(f"wrote        {write_report(read_delta(a.delta), Path(a.html))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
