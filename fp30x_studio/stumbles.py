r"""Candidate stumbles in a free-playing take, and the edit that removes one.

What a stumble is, here
-----------------------
Nothing in a MIDI stream says *mistake*. What the stream does carry is the
**shape of a correction**: a key closed briefly and abandoned, immediately
followed by a neighbour held properly. That shape is recoverable, and it is the
only kind of stumble this module claims to find.

Four shapes, in descending order of how confident the evidence is:

``grab``
    A strike of duration below :data:`SHORT` whose release is followed within
    :data:`REACH` by a strike one or two semitones away lasting at least
    :data:`HOLD_RATIO` times as long. The finger landed next to the note and
    corrected. This is the classic fumble and the only one detected with real
    confidence.

``double``
    The *same* key re-struck within :data:`RETRIGGER` of its own release, the
    first instance short. A repeat the player did not intend, or a key that
    bounced.

``brush``
    A short strike sounding **simultaneously** with a held strike a semitone
    away, at a velocity well below it. A finger fouling an adjacent key inside
    a chord. The held note is the music; the short one is the accident.

``hesitation``
    A silence far longer than the local inter-onset spacing, after which the
    player re-attacks pitch material already stated just before the gap. Not a
    wrong note -- a restart. Reported because cutting the gap often improves a
    part, and never removed automatically: the gap may be the music.

Deliberately not detected
-------------------------
**Dissonance is not evidence.** He plays scales like G A B-flat C-sharp D
E-flat F-sharp G (see the recording skill), so an interval-based detector would
flag his own harmonic language as error, at volume. Every rule above keys on
*duration and correction shape*, never on which pitch was played.

**Timing irregularity is not evidence** either, for the same reason: rubato and
a stumble are indistinguishable by tempo deviation alone, and he is composing,
not performing a metered score.

Removing one
------------
:func:`excise` deletes the offending strikes and nothing else, returning a new
:class:`~fp30x_studio.performance.Performance`. It never shifts what follows:
closing the hole in time would move every later onset against the pedal spans,
which are recorded in wall-clock and would then land on the wrong notes. A
removed grab leaves a rest exactly as long as the wrong note was.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from .performance import Performance, Strike

# A strike this short was not a note the player meant to sound. 90 ms is above
# the fastest deliberate grace note observed in the takes on disk (~55 ms) and
# below the shortest note of an ordinary fast run (~130 ms at 4.9 notes/s).
SHORT = 0.090
# How long after a bad note's release the correction may land and still read as
# the same gesture.
REACH = 0.220
# The correction must be held this many times longer than the note it replaces.
HOLD_RATIO = 2.5
# Same-key re-strike inside this window is a bounce, not a repeat.
RETRIGGER = 0.120
# A brushed neighbour is at least this much quieter than the note it fouls.
BRUSH_VEL_RATIO = 0.70
# A hesitation is a gap this many times the local median inter-onset interval.
HESITATION_RATIO = 6.0
# ...and at least this long in absolute terms, so a dense passage cannot
# manufacture hesitations out of ordinary phrase breathing.
HESITATION_MIN = 1.2

Kind = Literal["grab", "double", "brush", "hesitation"]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One place an edit could be made. Never applied without being chosen."""

    kind: Kind
    t: float                      # where it happens, seconds into the take
    strikes: tuple[Strike, ...]   # what :func:`excise` would delete
    keeps: tuple[Strike, ...]     # the note it corrected to, if any
    confidence: float             # 0..1, how strongly the shape matched
    note: str                     # human summary, in this take's own notes

    @property
    def removable(self) -> bool:
        """A hesitation has no strike to delete; it is a cut, not a removal."""
        return self.kind != "hesitation"


def _local_ioi(onsets: Sequence[float], i: int, span: int = 12) -> float:
    lo, hi = max(0, i - span), min(len(onsets), i + span)
    gaps = [onsets[j + 1] - onsets[j] for j in range(lo, hi - 1)]
    if not gaps:
        return 0.0
    gaps.sort()
    return gaps[len(gaps) // 2]


def find(perf: Performance) -> list[Candidate]:
    """Every candidate in one performance, in time order."""
    ss = perf.strikes()
    out: list[Candidate] = []
    onsets = [s.t_on for s in ss]

    for i, s in enumerate(ss):
        # --- grab: short note, then a neighbour held properly ---------------
        if s.duration < SHORT:
            for t in ss[i + 1:]:
                if t.t_on > s.t_off + REACH:
                    break
                if t is s or t.key == s.key:
                    continue
                if abs(t.key - s.key) <= 2 and t.duration >= max(
                        HOLD_RATIO * s.duration, SHORT):
                    conf = min(1.0, (t.duration / max(s.duration, 1e-3)) / 8.0
                               + (1.0 - s.duration / SHORT) * 0.35)
                    out.append(Candidate(
                        "grab", s.t_on, (s,), (t,), round(conf, 2),
                        f"{s.name} for {s.duration * 1000:.0f} ms, then "
                        f"{t.name} held {t.duration * 1000:.0f} ms"))
                    break

        # --- double: same key bounced --------------------------------------
        for t in ss[i + 1:]:
            if t.t_on > s.t_off + RETRIGGER:
                break
            if t.key == s.key and s.duration < SHORT and t.duration > s.duration:
                out.append(Candidate(
                    "double", s.t_on, (s,), (t,), 0.6,
                    f"{s.name} struck twice "
                    f"{(t.t_on - s.t_off) * 1000:.0f} ms apart"))
                break

        # --- brush: short quiet semitone inside a held note ------------------
        if s.duration < SHORT:
            for t in ss:
                if t.t_on > s.t_on:
                    break
                if (t is not s and abs(t.key - s.key) == 1
                        and t.t_off > s.t_on + 0.5 * s.duration
                        and s.velocity < BRUSH_VEL_RATIO * t.velocity):
                    out.append(Candidate(
                        "brush", s.t_on, (s,), (t,), 0.55,
                        f"{s.name} at velocity {s.velocity} brushed against "
                        f"{t.name} at {t.velocity}, still sounding"))
                    break

    # --- hesitation: a hole, then material already stated -------------------
    for i in range(len(ss) - 1):
        gap = ss[i + 1].t_on - ss[i].t_off
        med = _local_ioi(onsets, i)
        if gap < HESITATION_MIN or med <= 0 or gap < HESITATION_RATIO * med:
            continue
        before = {x.key for x in ss[max(0, i - 8):i + 1]}
        after = {x.key for x in ss[i + 1:i + 9]}
        if not before or not after:
            continue
        overlap = len(before & after) / len(after)
        if overlap >= 0.5:
            out.append(Candidate(
                "hesitation", ss[i].t_off, (), (),
                round(min(1.0, overlap), 2),
                f"{gap:.1f} s gap, then {int(overlap * 100)}% of the same keys "
                f"again (local spacing {med * 1000:.0f} ms)"))

    # One edit per instant: a grab and a brush can describe the same strike.
    seen: set[tuple[float, int]] = set()
    uniq: list[Candidate] = []
    rank = {"grab": 0, "double": 1, "brush": 2, "hesitation": 3}
    for c in sorted(out, key=lambda c: (c.t, rank[c.kind])):
        key = (round(c.t, 4), c.strikes[0].key if c.strikes else -1)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def excise(perf: Performance, chosen: Iterable[Candidate]) -> Performance:
    """A copy of ``perf`` with the chosen candidates' strikes deleted.

    Time is not closed up: see the module docstring. A hesitation contributes
    nothing here, since it names a silence and not a strike.
    """
    from .performance import KeyTrack

    drop = {(s.key, s.t_on, s.t_off) for c in chosen for s in c.strikes}
    tracks = [
        KeyTrack(k, [s for s in perf[k]
                     if (s.key, s.t_on, s.t_off) not in drop], check=False)
        for k in range(len(perf))
    ]
    return Performance(tracks, perf.pedal, t_start=perf.t_start,
                       t_end=perf.t_end, report=perf.report,
                       sounding=perf.sounding)
