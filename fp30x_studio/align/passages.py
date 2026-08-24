r"""Which attempts are the same passage — correlation, then correspondence, then warp.

Three stages, cheapest first, each one only ever *proposing* work to the next.

1. **One GEMM** (:mod:`.corr`). All 32 attempts at once, :math:`M=QQ^{\mathsf T}`
   over pitch-class chroma; diagonal sums of each block are the cross-correlation
   of that pair. Cost is milliseconds and it prunes the 496 ordered pairs to the
   dozen worth fitting.
2. **Local alignment** (:mod:`.correspond`). Smith-Waterman on pitch alone,
   which decides *which onset is which* and counts everything it could not match.
3. **The least-energy homeomorphism** (:mod:`.warp`). Fitted only through the
   surviving correspondence, with :math:`\lambda` by GCV.

**The guard, and why it is not optional.** Stage 3 will happily fit a perfect
warp through four matched events and report a zero residual, because a
piecewise-linear :math:`\varphi` through :math:`k` knots interpolates :math:`k`
points exactly. Observed on this session: six pairs reported
``explained = 100.0%`` off as few as three matched onsets. So a link is judged
on how much music it actually matched — :data:`MIN_MATCHED` onsets and
:data:`MIN_CORE_RATE` of the shared core — and never on its residual.

**Residual is not evidence when lambda reaches the floor of the sweep.** There
:math:`\varphi` is the interpolant through every knot, so ``residual_rms`` is
zero by construction and ``explained`` is 100% by construction, for three
matched onsets and for three hundred alike. Both numbers are reported with
``residual_informative`` beside them, false whenever that happened. It happens
often here and it is not a defect: it says these attempts differ by a
smooth tempo scaling and little else, which is the answer, just not one the
residual is entitled to state.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .corr import Candidate, all_pairs
from .events import Segment
from .report import DEFAULT_LAMBDAS, Alignment, align_segments

__all__ = ["Verdict", "Link", "passages", "components"]

#: Fewest matched onsets before a residual means anything. Below this the warp
#: has as many degrees of freedom as it has data.
MIN_MATCHED = 40

#: Fewest matched onsets, as a share of the shorter core, before a link counts.
MIN_CORE_RATE = 0.45


@dataclass(frozen=True)
class Verdict:
    trustworthy: bool
    reason: str
    matched: int
    core_rate: float
    lam_interior: bool


def judge(al: Alignment) -> Verdict:
    cen = al.correspondence.census()
    matched = len(al.correspondence.pairs)
    core_rate = min(cen.get("match_rate_core_a", 0.0),
                    cen.get("match_rate_core_b", 0.0))
    lo, hi = min(DEFAULT_LAMBDAS), max(DEFAULT_LAMBDAS)
    interior = lo < al.lam < hi
    if matched < MIN_MATCHED:
        return Verdict(False, f"only {matched} matched onsets — the warp has "
                              f"as much freedom as it has data", matched,
                       core_rate, interior)
    if core_rate < MIN_CORE_RATE:
        return Verdict(False, f"matched {core_rate:.0%} of the shared core — "
                              f"too little of it is actually shared", matched,
                       core_rate, interior)
    return Verdict(True, "", matched, core_rate, interior)


@dataclass
class Link:
    a: int
    b: int
    candidate: Candidate
    alignment: Alignment | None
    verdict: Verdict | None
    error: str = ""

    def row(self) -> dict:
        al, v = self.alignment, self.verdict
        return {
            "a": self.a, "b": self.b,
            "tau": self.candidate.tau,
            "cos": round(self.candidate.score, 3),
            "z": round(self.candidate.z, 1),
            "matched": v.matched if v else 0,
            "core_rate": round(v.core_rate, 3) if v else None,
            "mean_rate": round(al.warp.mean_rate, 4) if al else None,
            "residual_rms_s": round(al.residual_rms, 4) if al else None,
            "explained": round(al.explained, 4) if al else None,
            "lam": al.lam if al else None,
            "residual_informative": bool(al and al.lam > min(DEFAULT_LAMBDAS)),
            "gcv_interior": bool(al and al.gcv_interior),
            "trustworthy": bool(v and v.trustworthy),
            "why_not": (v.reason if v and not v.trustworthy else ""),
            "error": self.error,
        }


def passages(segments: list[Segment], *, top: int = 1) -> list[Link]:
    """Every pair the GEMM proposes, fitted and judged."""
    by = {s.index: s for s in segments}
    _M, cands = all_pairs(segments, top=top)
    out: list[Link] = []
    for c in sorted([c for c in cands if c.strong], key=lambda c: -c.z):
        try:
            al = align_segments(by[c.a], by[c.b],
                                label_a=f"s{c.a:02d}", label_b=f"s{c.b:02d}")
        except Exception as e:                       # not the same music
            out.append(Link(c.a, c.b, c, None, None, f"{type(e).__name__}: {e}"))
            continue
        out.append(Link(c.a, c.b, c, al, judge(al)))
    return out


def components(links: list[Link], *, trusted_only: bool = True) -> list[list[int]]:
    """Connected components over the trustworthy links.

    A component is a set of attempts at one passage. Transitivity is doing real
    work here: A overlaps B and B overlaps C puts A and C in one group even
    where they share nothing directly, which is what lets short attempts at the
    start and end of a section be recognised as the same section.
    """
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for l in links:
        if trusted_only and not (l.verdict and l.verdict.trustworthy):
            continue
        ra, rb = find(l.a), find(l.b)
        if ra != rb:
            parent[ra] = rb

    groups: dict[int, list[int]] = {}
    for x in parent:
        groups.setdefault(find(x), []).append(x)
    return [sorted(g) for g in sorted(groups.values(), key=min)]
