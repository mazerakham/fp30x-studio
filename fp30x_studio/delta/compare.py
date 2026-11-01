r"""Comparing two performances of one piece.

Two deltas of the same score live in the same vector space by construction:
both are indexed by score note, and the index of a score note does not depend
on who played it. So the comparison is the obvious one,

.. math::   \cos(\delta^A,\delta^B)
            = \frac{\langle \delta^A,\delta^B\rangle}
                   {\lVert\delta^A\rVert\,\lVert\delta^B\rVert},

taken component by component and restricted to
:math:`D_A \cap D_B`, the score notes **both** performances actually played.
That intersection is reported with the number, because a cosine over 300 notes
and a cosine over 1300 notes are not the same claim, and a matching that quietly
threw half the piece away would otherwise look like agreement.

The categorical part of the operation set does not have a cosine and is not
given one. Insertions and deletions are reported as counts, per performance,
and a note both players left out is not evidence that they play alike.

What the components mean when compared
--------------------------------------
``timing`` is the whole onset residual, ``rubato`` its smooth part and ``micro``
what is left; ``rubato`` is the one that answers "do these two shape the piece
the same way", and it will always be the highest of the three because it is the
part with structure in it. ``velocity`` is the raw dynamic in :math:`[0,1]`,
whose cosine sits near one for any two pianists because both vectors are close
to a positive multiple of the all ones vector; ``velocity_centred`` subtracts
each performance's own mean and is the vector that discriminates. Read the
centred one.
"""

from __future__ import annotations

import numpy as np

from .fit import Delta

__all__ = ["compare_deltas", "cosine", "COMPARED"]

#: The components a comparison reports, in the order it reports them.
COMPARED = ("timing", "rubato", "micro", "velocity", "velocity_centred",
            "articulation")


def cosine(u: np.ndarray, v: np.ndarray) -> float | None:
    nu, nv = float(np.linalg.norm(u)), float(np.linalg.norm(v))
    if nu == 0.0 or nv == 0.0:
        return None
    return float(np.dot(u, v) / (nu * nv))


def compare_deltas(a: Delta, b: Delta) -> dict:
    """Cosine similarity per component over the notes both performances played."""
    same_score = a.score_sha256 == b.score_sha256
    shared = sorted(set(a.index().tolist()) & set(b.index().tolist()))
    out = {
        "a": a.performance_source,
        "b": b.performance_source,
        "score": a.score_source,
        "same_score": same_score,
        "matched_a": len(a.matched),
        "matched_b": len(b.matched),
        "score_notes": a.score_notes,
        "intersection": len(shared),
        "intersection_of_score": len(shared) / max(a.score_notes, 1),
        "tempo_a_qpm": a.tempo["qpm"],
        "tempo_b_qpm": b.tempo["qpm"],
        "edits_a": {"insertions": a.counts["insertions"],
                    "deletions": a.counts["deletions"]},
        "edits_b": {"insertions": b.counts["insertions"],
                    "deletions": b.counts["deletions"]},
        "cosine": {},
        "l2_distance": {},
        "norms_a": a.norms,
        "norms_b": b.norms,
    }
    if not same_score:
        out["warning"] = ("the two deltas were fitted against different score "
                          "files; the shared indices do not name the same notes")
    if not shared:
        out["warning"] = "no score note was matched by both performances"
        return out

    for name in COMPARED:
        ma, mb = a.as_map(name), b.as_map(name)
        u = np.array([ma[i] for i in shared])
        v = np.array([mb[i] for i in shared])
        ok = np.isfinite(u) & np.isfinite(v)
        u, v = u[ok], v[ok]
        out["cosine"][name] = {"value": cosine(u, v) if len(u) else None,
                               "n": int(len(u))}
        out["l2_distance"][name] = (float(np.linalg.norm(u - v))
                                    if len(u) else None)
    return out


def format_comparison(c: dict) -> str:
    """The comparison as the block the CLI prints."""
    lines = [
        f"score        {c['score']}",
        f"A            {c['a']}",
        f"B            {c['b']}",
        f"tempo        A {c['tempo_a_qpm']:.2f} qpm    B {c['tempo_b_qpm']:.2f} qpm",
        f"matched      A {c['matched_a']} of {c['score_notes']} score notes"
        f"    B {c['matched_b']} of {c['score_notes']}",
        f"intersection {c['intersection']} score notes "
        f"({100 * c['intersection_of_score']:.1f}% of the score)",
        f"edits        A {c['edits_a']['insertions']} inserted, "
        f"{c['edits_a']['deletions']} deleted"
        f"    B {c['edits_b']['insertions']} inserted, "
        f"{c['edits_b']['deletions']} deleted",
        "",
        f"{'component':18s} {'cosine':>9s} {'n':>6s} {'|a - b|':>10s} "
        f"{'rms A':>10s} {'rms B':>10s}",
    ]
    for name in COMPARED:
        cos = c["cosine"].get(name, {})
        val = cos.get("value")
        na = c["norms_a"].get(name, {}).get("rms")
        nb = c["norms_b"].get(name, {}).get("rms")
        lines.append(
            f"{name:18s} {('%9.4f' % val) if val is not None else '        .'} "
            f"{cos.get('n', 0):6d} "
            f"{('%10.4f' % c['l2_distance'][name]) if c['l2_distance'].get(name) is not None else '         .'} "
            f"{('%10.4f' % na) if na is not None else '         .'} "
            f"{('%10.4f' % nb) if nb is not None else '         .'}")
    if c.get("warning"):
        lines += ["", f"warning: {c['warning']}"]
    return "\n".join(lines)
