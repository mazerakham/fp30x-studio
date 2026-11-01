r"""Choosing a take per bar, and splicing the choices into one performance.

The problem
-----------
He can play the whole rag and he cannot play it cleanly end to end. Several
passes exist; different ones are good in different places. A release wants the
best of each, which means a selection function from bar to pass and a splice
that does not announce itself.

Why the weights are indicators
------------------------------
The obvious object is a partition of unity: weights :math:`w_i(t) \ge 0`
summing to one, each supported near where pass *i* is best, crossfading at the
overlaps. That is the right object and the wrong domain. Two passes of one bar
differ in tempo at the percent level, so a crossfade places two attacks 20-40 ms
apart and the ear hears a flam rather than a fusion; time-warping one onto the
other to fix it resamples every note.

Rendering happens once, from MIDI, so the fusion can happen on note events
instead -- and there the weights collapse to indicators, because half a note-on
is not a thing. The content that the smooth version carried moves into a
constraint on **where a support may end**: see :func:`seams`. What survives of
the soft part is the outgoing pass's pedal tail, which is allowed to decay under
the incoming one because a tail has no onset to smear.

Bar trajectories
----------------
A rag is notated once with repeat marks and played out as AABBACCDD, so a bar
number is not a position: bar 5 happens three times. :func:`trajectory` recovers
the order actually played -- the sequence of (time, bar) pairs in time order --
and a *group occurrence* is a contiguous stretch of that sequence, so a
candidate is always one real pass through the bars and never a splice of two
visits to them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .performance import Performance, Strike

#: Bars per decision. Four is a phrase in this repertoire and keeps the number
#: of decisions near twenty, which is a sitting rather than a project.
GROUP = 4

#: Within one run through a group, no step between consecutive mapped bars may
#: exceed this multiple of that run's median step. A whole-take map holds every
#: visit to a bar, so without this a run can be assembled from bar 0 of one pass
#: and bars 1-3 of a pass ninety seconds later -- which is not a performance of
#: those bars and was offered as one.
MAX_STEP_RATIO = 3.0

#: A seam may fall this far from the nominal bar line, to reach a moment where
#: nothing is sounding.
SEAM_SEARCH = 0.45

#: Below this, a gap between onsets is too tight to put a cut in.
SEAM_SILENCE = 0.035


@dataclass(frozen=True, slots=True)
class Candidate:
    """One pass's playing of one bar group."""
    pass_id: str
    group: int
    bars: tuple[int, ...]
    t0: float
    t1: float
    bars_matched: int = 0
    run: int = 0

    @property
    def key(self) -> str:
        """Identifies this offer: the take, and which run through the bars."""
        return f"{self.pass_id}#{self.run}"

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def seconds_per_bar(self) -> float:
        """How fast this pass took the group. Splicing across a big difference
        in this is a tempo jump, which no seam placement can hide."""
        return self.duration / max(len(self.bars), 1)


def trajectory(bars: dict) -> list[tuple[float, int]]:
    """The order the bars were actually played, as ``(seconds, bar)``."""
    out = [(t, int(b)) for b, ts in bars.items() for t in ts]
    out.sort()
    return out


def groups(n_measures: int, first_bar: int = 0, size: int = GROUP) -> list[tuple[int, ...]]:
    bars = list(range(first_bar, n_measures + 1))
    return [tuple(bars[i:i + size]) for i in range(0, len(bars), size)]


def occurrences(traj: Sequence[tuple[float, int]], group: Sequence[int],
                *, min_fraction: float = 0.75) -> list[tuple[float, float, int]]:
    """Stretches of one pass that run through ``group`` in order.

    Returns ``(t0, t1, bars_matched)``. A pass need not have every bar mapped --
    measuremap leaves one out rather than guess it -- so a stretch qualifies on
    covering ``min_fraction`` of the group with its bars ascending, and on
    having matched the group's **last** bar, without which the clip would stop
    early and the splice would leave a hole.

    The end is one bar's worth of time after the last bar, or the next bar's
    onset when that arrives sooner. Ending at "the next event in the
    trajectory" instead swallows hesitations: in `2026-09-19-joplin-b-5` the
    player waits six seconds between bars 11 and 12, and a clip bounded that way
    runs 16 s for four bars against a neighbouring pass's 8.7 s.
    """
    want = list(group)
    found: list[tuple[float, float, int]] = []
    i = 0
    while i < len(traj):
        if traj[i][1] != want[0]:
            i += 1
            continue
        j, k = i, 0
        hits: list[tuple[float, int]] = []
        while j < len(traj) and k < len(want):
            if traj[j][1] == want[k]:
                hits.append(traj[j])
                j += 1
                k += 1
            elif traj[j][1] > want[k]:
                k += 1                      # this bar was not mapped; skip it
            else:
                break                       # went backwards: a different pass
        steps = [b[0] - a[0] for a, b in zip(hits, hits[1:])]
        steps = [x for x in steps if x > 0]
        bar_dur = sorted(steps)[len(steps) // 2] if steps else 1.0
        contiguous = not steps or max(steps) <= MAX_STEP_RATIO * bar_dur
        ok = (len(hits) >= max(2, int(min_fraction * len(want)))
              and hits and hits[-1][1] == want[-1]
              and contiguous)
        if ok:
            t0 = hits[0][0]
            t_last = hits[-1][0]
            t1 = t_last + bar_dur
            nxt = next((t for t, b in traj
                        if b == want[-1] + 1 and t > t_last), None)
            if nxt is not None and nxt < t1:
                t1 = nxt
            found.append((t0, t1, len(hits)))
            i = j
        else:
            i += 1
    return found


def candidates(maps: dict[str, dict], *, size: int = GROUP,
               per_pass: int = 3) -> dict[int, list[Candidate]]:
    """Every offer for every bar group, keyed by group index.

    A map over a whole take holds *several* runs through the piece -- he plays
    it three times in a sitting -- and those are different performances of the
    same bars, so each is its own candidate rather than one winning on behalf of
    the file it came from. At most ``per_pass`` runs per take per group, longest
    match first, so a group that was practised twenty times does not produce
    twenty buttons.
    """
    n = max(m["n_measures"] for m in maps.values())
    first = min(int(b) for m in maps.values() for b in m["bars"])
    out: dict[int, list[Candidate]] = {}
    for gi, g in enumerate(groups(n, first_bar=first, size=size)):
        offers: dict[str, list[tuple[float, float, int]]] = {}
        for pid, m in maps.items():
            occ = occurrences(trajectory(m["bars"]), g)
            if occ:
                offers[pid] = occ
        if not offers:
            continue
        spans = [b - a for occ in offers.values() for a, b, _ in occ]
        typical = sorted(spans)[len(spans) // 2]
        row: list[Candidate] = []
        for pid, occ in offers.items():
            ranked = sorted(occ, key=lambda o: (-o[2], abs((o[1] - o[0]) - typical)))
            for k, (t0, t1, hits) in enumerate(ranked[:per_pass]):
                row.append(Candidate(pid, gi, tuple(g), t0, t1, hits, k))
        row.sort(key=lambda c: (-c.bars_matched, c.pass_id, c.run))
        out[gi] = row
    return out


def seams(perf: Performance, around: float, *,
          search: float = SEAM_SEARCH, silence: float = SEAM_SILENCE) -> float | None:
    """The moment near ``around`` with no **onset** on either side of it.

    An earlier version also demanded the pedal be up and nothing sustaining.
    On this repertoire that is almost never true: measured over the automatic
    comp, 2 of 40 boundaries qualified, so 38 cuts fell back to the nominal bar
    line and the search bought nothing. A rag is pedalled continuously; waiting
    for silence means waiting for the end of the piece.

    What a cut must actually avoid is an *attack* landing on the wrong side of
    it. Notes still ringing are handled instead by letting them ring: see
    :func:`assemble`.
    """
    onsets = sorted(s.t_on for s in perf.strikes()
                    if around - search <= s.t_on <= around + search)
    if not onsets:
        return around
    edges = [around - search] + onsets + [around + search]
    gaps = [(a, b) for a, b in zip(edges, edges[1:]) if b - a >= silence]
    if not gaps:
        return None
    a, b = min(gaps, key=lambda ab: abs((ab[0] + ab[1]) / 2 - around))
    return (a + b) / 2


def assemble(chosen: Sequence[tuple[Candidate, Performance]], *,
             tail: float = 2.0) -> tuple[list[Strike], list[tuple[float, float]]]:
    """Lay the chosen groups end to end on one clock.

    Each group is placed immediately after the previous one and keeps its own
    internal timing; nothing is stretched, because stretching a performance to
    meet its neighbour is the thing that makes a comp sound like a comp.

    **A note is assigned to the group its onset falls in, and is then allowed to
    ring past the seam** for up to ``tail`` seconds, under whatever the next
    group has begun playing. This is the only place the smooth picture survives:
    the outgoing pass's sustain decays beneath the incoming one, which is safe
    precisely because a tail carries no attack to misalign. Truncating at the
    seam instead is what a cut sounds like.

    The pedal is treated the same way, so a group entered mid-pedal is played
    with the damper already off the strings rather than opening dry.
    """
    strikes: list[Strike] = []
    pedal: list[tuple[float, float]] = []
    clock = 0.0
    for cand, perf in chosen:
        t0 = seams(perf, cand.t0) or cand.t0
        t1 = seams(perf, cand.t1) or cand.t1
        for s in perf.strikes():
            if not (t0 <= s.t_on < t1):
                continue
            end = min(s.t_off, t1 + tail)
            strikes.append(Strike(clock + s.t_on - t0,
                                  clock + max(end, s.t_on) - t0, s.key, s.velocity))
        for a, b in perf.pedal:
            if b <= t0 or a >= t1:
                continue
            pedal.append((clock + max(a, t0) - t0,
                          clock + min(b, t1 + tail) - t0))
        clock += t1 - t0
    return strikes, pedal


def load_maps(dir: str | Path, *, min_accepted: int = 5,
              min_bars: int = 12) -> dict[str, dict]:
    """Every measure map in a directory that really contains the piece.

    Judged on how much the map placed, not on ``cost_median``. A take that is
    fifty minutes of assorted rags with one pass at this piece inside it has a
    median cost near 0.73 -- the median is over the whole take and most of the
    take is other music -- while being the best source on disk. Filtering on it
    threw away `2026-09-23-compose-a`, which carries 79 of 87 bars.

    What does separate signal from noise is the number of chunks the DTW was
    willing to accept: the genuine sources accept 20 to 58, and a take with no
    pass at the piece accepts zero or one.
    """
    out = {}
    for f in sorted(Path(dir).expanduser().glob("*.json")):
        if f.name == "summary.json":
            continue
        d = json.loads(f.read_text())
        if (d.get("chunks_accepted", 0) >= min_accepted
                and d.get("mapped_measures", 0) >= min_bars):
            out[f.stem.replace("sweep-", "")] = d
    return out
