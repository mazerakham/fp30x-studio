"""Measure number -> wall-clock time, for a performance of a notated piece.

The point of this module is one interaction: *play me bar 41 of every take I
have*. Everything else in the studio can compare two performances to each
other; nothing could point at a bar and find it in all of them.

**Why not one global warp.** The obvious approach -- fit a single monotone warp
between the score's onsets and the performance's -- fails on ragtime, and fails
silently. A rag is notated once with repeat marks and played out as AABBACCDD,
so a 7-minute performance of an 87-bar score is not a time-stretch of it; it
revisits the same bars up to three times. A monotone warp cannot revisit
anything. Tried on ``something.krn`` against a 451 s take it mapped 5 bars of
87 and collapsed them all onto one instant.

**What works instead** is chunked subsequence DTW. The performance is cut into
short chunks of consecutive onset clusters, and each chunk is located in the
score *independently*, free to land anywhere. A chunk from the third A section
matches bar 5 just as happily as one from the first. Nothing has to be monotone
across chunk boundaries, which is exactly the constraint the repeat structure
violates. Each accepted chunk carries the score's bar numbers back onto
performance time.

A chunk is accepted only if its match is good: a mean per-cluster pitch-set
distance under :data:`MAX_COST`. A rejected chunk leaves its bars unmapped
rather than guessed at, so an unmapped stretch means "I do not know", never a
wrong bar number. The census in the result says how much was mapped.

This is the same machinery used to transfer hand labels in
:mod:`fp30x_studio.hands`, generalised: there the score supplied the staff a
note was written on, here it supplies the bar.
"""

from __future__ import annotations

import argparse, json, statistics
from pathlib import Path

import numpy as np

from .align.score import load_score
from . import hands as H

#: Consecutive performance clusters located as one unit.
CHUNK = 40

#: Mean per-cluster distance above which a chunk's placement is not believed.
MAX_COST = 0.45

#: Chunks shorter than this are not placed: too little evidence to be sure.
MIN_CHUNK = 12


def _subseq_dtw(P: list[set[int]], S: list[set[int]]):
    """Locate the cluster sequence ``P`` anywhere inside ``S``.

    Free at both ends on the score side -- the chunk may start and finish at
    any bar -- which is what makes each chunk independent of its neighbours.
    """
    n, m = len(P), len(S)
    C = np.empty((n, m))
    for i, a in enumerate(P):
        for j, b in enumerate(S):
            C[i, j] = 1.0 - len(a & b) / max(len(a), len(b), 1)
    D = np.full((n, m), np.inf)
    B = np.zeros((n, m), dtype=np.int8)
    D[0] = C[0]                                  # free start: any score index
    for i in range(1, n):
        prev = D[i - 1]
        for j in range(m):
            diag = prev[j - 1] if j else np.inf
            up = prev[j]
            left = D[i, j - 1] if j else np.inf
            k = int(np.argmin((diag, up, left)))
            D[i, j] = (diag, up, left)[k] + C[i, j]
            B[i, j] = k
    jend = int(np.argmin(D[n - 1]))
    cost = float(D[n - 1, jend]) / n
    pairs, i, j = [], n - 1, jend
    while i >= 0:
        pairs.append((i, j))
        k = B[i, j]
        if i == 0:
            break
        if k == 0:
            i, j = i - 1, j - 1
        elif k == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return cost, pairs


def measure_map(take: str | Path, score_path: str | Path, *,
                segment: int | None = None, qpm: float = 100.0,
                chunk: int = CHUNK, max_cost: float = MAX_COST) -> dict:
    """Bar -> list of seconds into the take at which that bar is played.

    A bar appears once per pass through it, so a repeated section yields
    several times, in order. The UI plays the first and offers the rest.
    """
    sc = load_score(score_path, qpm=qpm)
    S = [set(e.notes) for e in sc.events]

    perf = H.notes_from_take(take, segment=segment)
    pc = H.clusters_of(perf)
    if not pc:
        raise ValueError(f"no playing found in {take}")
    t0 = pc[0].t

    bars: dict[int, list[float]] = {}
    costs, accepted = [], 0
    for s0 in range(0, len(pc), chunk):
        block = pc[s0:s0 + chunk]
        if len(block) < MIN_CHUNK:
            continue
        cost, pairs = _subseq_dtw([set(c.pitches) for c in block], S)
        costs.append(cost)
        if cost > max_cost:
            continue
        accepted += 1
        seen: dict[int, float] = {}
        for i, j in pairs:
            m = sc.measures[j]
            t = block[i].t - t0
            if m not in seen or t < seen[m]:
                seen[m] = t
        for m, t in seen.items():
            bars.setdefault(m, []).append(round(float(t), 3))

    for m in bars:
        bars[m] = sorted(bars[m])

    span = round(pc[-1].t - t0, 3)
    return {
        "take": str(take),
        "score": str(score_path),
        "title": sc.title,
        "n_measures": sc.n_measures,
        "mapped_measures": len(bars),
        "seconds": span,
        "chunks": len(costs),
        "chunks_accepted": accepted,
        "cost_median": round(statistics.median(costs), 3) if costs else None,
        "bars": {str(k): v for k, v in sorted(bars.items())},
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m fp30x_studio.measuremap",
                                description=__doc__.splitlines()[0])
    p.add_argument("take")
    p.add_argument("score")
    p.add_argument("-o", "--out")
    p.add_argument("--segment", type=int)
    p.add_argument("--qpm", type=float, default=100.0)
    a = p.parse_args(argv)
    m = measure_map(a.take, a.score, segment=a.segment, qpm=a.qpm)
    print(f"{m['title']}: {m['mapped_measures']}/{m['n_measures']} bars mapped "
          f"over {m['seconds']:.1f}s; "
          f"{m['chunks_accepted']}/{m['chunks']} chunks accepted, "
          f"cost median {m['cost_median']}")
    if a.out:
        Path(a.out).write_text(json.dumps(m, indent=1), encoding="utf-8")
        print(f"  wrote {a.out}")
    return 0 if m["bars"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
