"""The performance as a vector-valued function of time.

Model
-----
Fix the 88 piano keys, indexed by ``k`` in ``{0, ..., 87}`` (``k = 0`` is A0,
MIDI note 21; ``k = 87`` is C8, MIDI note 108).

A **strike** of key ``k`` is the datum ``(a, b, P)`` with ``a <= b`` and
``P`` in ``{1, ..., 127}``: the actuator for key ``k`` closes at time ``a``,
is held closed through time ``b``, and the hammer was launched with momentum
``P``. Its contribution to the sound is the scaled indicator

    P * 1_[a, b] : R -> R,   t |-> P if a <= t <= b, else 0.

The **per-key function** is the finite sum over that key's strikes,

    f_k = sum_{i=1..n_k} P_i * 1_[a_i, b_i],                          (1)

and the structural invariant of a physical keyboard is that the intervals
``[a_i, b_i]`` belonging to one key are pairwise disjoint: one actuator
cannot be in two states at once.  Two grades of the invariant are used here,
and both are enforceable (see :meth:`KeyTrack.check_disjoint`):

  * **Strong** (``strict=True``): ``I_i n I_j = {}`` for ``i != j``.  Since
    the intervals are closed and ordered, this is ``b_i < a_{i+1}``.
  * **Weak** (``strict=False``, the default): ``lambda(I_i n I_j) = 0``, i.e.
    ``int(I_i) n int(I_j) = {}``, i.e. ``b_i <= a_{i+1}``.  This is the
    invariant that survives a re-strike at the exact instant of release, which
    is what a legato repeat and the MIDI truncation rule below produce.

Under either grade (1) is a real-valued step function of bounded variation
with finitely many jumps, so ``f_k`` is in ``BV(R) n L^inf(R)`` with compact
support.

The **performance** is the direct sum

    f = (+)_{k=0..87} f_k : R -> R^88,                                (2)

an ``R^88``-valued step function; ``f(t)`` is the vector of momenta currently
being sustained, component by component.  Everything this module computes is a
functional of (2).

Derived objects
---------------
*Jump measure.*  ``Df``, the distributional derivative of ``f_k``, is the
finite atomic measure

    Df_k = sum_i P_i * (delta_{a_i} - delta_{b_i}),                   (3)

after summing coefficients at coincident points.  A degenerate strike
``a_i = b_i`` cancels itself out of (3), consistent with the fact that it is
the zero element of ``L^1``.

*Total variation.*  The variation reported is the essential (measure-theoretic)
variation ``|Df|(R) = sum_t |Df({t})|``, the total mass of (3).  For a single
non-degenerate isolated strike this is ``2P``: up ``P``, down ``P``.  For the
vector-valued ``f`` of (2) the variation depends on the norm placed on ``R^88``,

    V_nu(f) = sum_t nu( Df({t}) ),                                    (4)

and ``nu = l^1`` recovers ``sum_k |Df_k|(R)``, the sum of the component
variations.

*Support measure.*  ``lambda(supp f_k) = lambda(U_i [a_i, b_i]) = sum_i (b_i - a_i)``
when the intervals are weakly disjoint.  For the whole performance the support
is the union over all keys, which is emphatically **not** the sum over keys;
it is computed by merging intervals.

*Polyphony.*  ``n(t) = #{(k, i) : t in [a_{k,i}, b_{k,i}]} = sum_k sum_i 1_{I_{k,i}}(t)``,
an integer-valued step function.  Evaluated with closed intervals, so at the
finitely many instants where one note ends exactly as another begins, ``n``
counts both.

*Cumulative energy.*  Componentwise,

    F_k(t) = int_{-inf}^{t} f_k(s) ds
           = sum_i P_i * max(0, min(t, b_i) - a_i),                   (5)

and ``F = sum_k F_k`` for the aggregate.  ``F`` is non-decreasing (the
integrand is non-negative), continuous, piecewise affine, with slope ``f(t)``
on each open segment between breakpoints.  It is constant exactly on the
complement of the support: plateaus where nothing sounds, rises while notes are
held.  This is the Cantor-function-shaped object -- monotone, continuous, flat
off a small set -- with the difference that here the exceptional set has
positive measure and finitely many components, so ``F`` is absolutely
continuous rather than singular.  ``F`` is a genuine devil's-staircase
approximant only in silhouette, and the docstring says so rather than pretend.

Sustain
-------
CC64 >= 64 is pedal down, < 64 is pedal up, giving pedal intervals
``[d_j, u_j]``.  :meth:`Performance.with_sustain` maps the *actuator*
representation to a *sounding* representation by the rule: if a strike's
release ``b`` lies in a pedal interval ``[d_j, u_j]``, the sounding interval
extends to ``u_j``.  Re-striking the same key while it rings truncates the
earlier sounding interval at the new onset, since the hammer resets the string;
this is what keeps the disjointness invariant true of the sounding
representation as well.  Half-pedalling and sostenuto are not modelled, and a
pedal pressed *after* a release does not resurrect the note.

Scaling
-------
``P`` is the raw MIDI velocity, an integer in ``[1, 127]``.  Every quantity
above is positively homogeneous of degree 1 in ``P`` (variation, cumulative
energy) or degree 0 (support, polyphony, plateaus), so composing with any
monotone velocity curve ``P -> g(P)`` rescales the results predictably; no
perceptual loudness curve is baked in.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Literal, Sequence

import numpy as np

from . import core

__all__ = [
    "N_KEYS",
    "MIDI_LOW",
    "MIDI_HIGH",
    "SUSTAIN_CC",
    "DisjointnessError",
    "ParseError",
    "Strike",
    "KeyTrack",
    "Performance",
    "ParseReport",
    "key_index",
    "midi_note",
    "synthesize_midi",
    "quantisation_step",
]

# Capture front ends, both consumed through ``Performance.from_capture``:
#   fp30x_studio.core.Capture        polling, ~2 ms floor, stamps on notice
#   fp30x_studio.rawcapture.RawCapture   CoreMIDI callback, driver timestamps

N_KEYS = 88
MIDI_LOW = 21  # A0
MIDI_HIGH = 108  # C8
SUSTAIN_CC = 64

Norm = Literal["l1", "l2", "linf"]
OrphanPolicy = Literal["truncate", "drop", "raise"]
RetriggerPolicy = Literal["truncate", "raise"]
RangePolicy = Literal["drop", "raise"]


class DisjointnessError(ValueError):
    """The pairwise-disjointness invariant on a single key's intervals failed."""


class ParseError(ValueError):
    """A MIDI stream could not be put in the closed-interval representation."""


def key_index(note: int) -> int:
    """MIDI note number -> key index in ``{0, ..., 87}``. Raises off the keyboard."""
    if not MIDI_LOW <= note <= MIDI_HIGH:
        raise ValueError(f"MIDI note {note} is outside the 88-key range "
                         f"[{MIDI_LOW}, {MIDI_HIGH}]")
    return note - MIDI_LOW


def midi_note(k: int) -> int:
    """Key index -> MIDI note number."""
    if not 0 <= k < N_KEYS:
        raise ValueError(f"key index {k} outside [0, {N_KEYS})")
    return k + MIDI_LOW


@dataclass(frozen=True, slots=True, order=True)
class Strike:
    """One scaled indicator ``P * 1_[a, b]`` on key ``k``.

    Ordering is lexicographic in ``(t_on, t_off, key)`` so that a list of
    strikes sorts into performance order.
    """

    t_on: float
    t_off: float
    key: int
    velocity: int

    def __post_init__(self) -> None:
        if self.t_off < self.t_on:
            raise ValueError(f"strike with t_off {self.t_off} < t_on {self.t_on}")
        if not 0 <= self.key < N_KEYS:
            raise ValueError(f"key index {self.key} outside [0, {N_KEYS})")
        if not 1 <= self.velocity <= 127:
            raise ValueError(f"velocity {self.velocity} outside [1, 127]")

    @property
    def duration(self) -> float:
        """``lambda([a, b]) = b - a``."""
        return self.t_off - self.t_on

    @property
    def is_degenerate(self) -> bool:
        """``a == b``: a single point, Lebesgue-null, the zero element of L^1."""
        return self.t_off == self.t_on

    @property
    def note(self) -> int:
        """MIDI note number."""
        return midi_note(self.key)

    @property
    def name(self) -> str:
        """Scientific pitch name, e.g. ``A0``, via :func:`fp30x_studio.core.note_name`."""
        return core.note_name(self.note)

    def __contains__(self, t: float) -> bool:
        """Closed-interval membership ``t in [a, b]``."""
        return self.t_on <= t <= self.t_off

    def __call__(self, t: float) -> float:
        """``(P * 1_[a, b])(t)``."""
        return float(self.velocity) if self.t_on <= t <= self.t_off else 0.0

    def energy(self) -> float:
        """``int P * 1_[a,b] = P * (b - a)``."""
        return self.velocity * self.duration


def _merge(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Union of closed intervals, as a minimal sorted list of closed intervals.

    Intervals that touch (``b == a'``) are merged, since their union is the
    single closed interval ``[a, b']``.
    """
    if not intervals:
        return []
    out: list[tuple[float, float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def _atoms(pairs: Iterable[tuple[float, float]], tol: float = 0.0
           ) -> list[tuple[float, float]]:
    """Collect signed point masses into ``(t, mass)``, summed at coincident ``t``.

    Zero atoms (exact cancellation, e.g. a degenerate strike) are dropped.
    """
    acc: dict[float, float] = {}
    for t, m in pairs:
        acc[t] = acc.get(t, 0.0) + m
    return [(t, m) for t, m in sorted(acc.items()) if abs(m) > tol]


class KeyTrack:
    """The per-key function ``f_k = sum_i P_i 1_[a_i, b_i]`` of equation (1).

    The strikes are held sorted by onset and, unless the caller opts out, are
    checked for pairwise disjointness at construction.
    """

    __slots__ = ("key", "strikes")

    def __init__(self, key: int, strikes: Iterable[Strike], *, check: bool = True,
                 strict: bool = False):
        if not 0 <= key < N_KEYS:
            raise ValueError(f"key index {key} outside [0, {N_KEYS})")
        self.key = key
        self.strikes: tuple[Strike, ...] = tuple(sorted(strikes))
        for s in self.strikes:
            if s.key != key:
                raise ValueError(f"strike on key {s.key} placed in track {key}")
        if check:
            self.check_disjoint(strict=strict)

    # -- invariant ---------------------------------------------------------

    def check_disjoint(self, *, strict: bool = False, atol: float = 0.0) -> None:
        """Enforce pairwise disjointness of this key's closed intervals.

        ``strict=True`` demands ``I_i n I_j = {}`` (so ``b_i < a_{i+1}``);
        ``strict=False`` demands only ``lambda(I_i n I_j) = 0`` (so
        ``b_i <= a_{i+1}``), which permits release-and-restrike at one instant.

        Because the strikes are sorted by onset, checking consecutive pairs
        suffices: if ``b_i <= a_{i+1}`` for every ``i`` then for ``j > i+1``,
        ``a_j >= a_{i+1} >= b_i``, so every pair is handled.

        Raises :class:`DisjointnessError` on the first violation.
        """
        for prev, cur in zip(self.strikes, self.strikes[1:]):
            gap = cur.t_on - prev.t_off
            bad = gap < -atol if not strict else gap <= atol
            if bad:
                grade = "strictly disjoint" if strict else "a.e. disjoint"
                raise DisjointnessError(
                    f"key {self.key} ({core.note_name(midi_note(self.key))}): "
                    f"intervals [{prev.t_on:.6f}, {prev.t_off:.6f}] and "
                    f"[{cur.t_on:.6f}, {cur.t_off:.6f}] are not {grade} "
                    f"(gap {gap:+.6f})"
                )

    # -- evaluation --------------------------------------------------------

    def __call__(self, t: float) -> float:
        """``f_k(t)``: the momentum sustained on this key at time ``t``.

        By disjointness at most one strike (two, at a shared endpoint under the
        weak grade) can contain ``t``; the value returned is the sum, so a
        shared endpoint reads as ``P_i + P_{i+1}`` -- the honest pointwise value
        of (1), which differs from the a.e. representative on a null set.
        """
        i = bisect.bisect_right([s.t_on for s in self.strikes], t)
        total = 0.0
        for s in self.strikes[max(0, i - 2):i]:
            total += s(t)
        return total

    def __len__(self) -> int:
        return len(self.strikes)

    def __iter__(self) -> Iterator[Strike]:
        return iter(self.strikes)

    def __repr__(self) -> str:
        return (f"KeyTrack(key={self.key}, {core.note_name(midi_note(self.key))}, "
                f"{len(self.strikes)} strikes)")

    # -- functionals -------------------------------------------------------

    def jump_measure(self) -> list[tuple[float, float]]:
        """The atoms of ``Df_k`` from equation (3), as sorted ``(t, mass)``."""
        pairs: list[tuple[float, float]] = []
        for s in self.strikes:
            pairs.append((s.t_on, float(s.velocity)))
            pairs.append((s.t_off, -float(s.velocity)))
        return _atoms(pairs)

    def total_variation(self) -> float:
        """``|Df_k|(R)``, the essential variation. ``2 * sum P_i`` when isolated."""
        return sum(abs(m) for _, m in self.jump_measure())

    def support(self) -> list[tuple[float, float]]:
        """``supp f_k`` as a minimal list of disjoint closed intervals."""
        return _merge([(s.t_on, s.t_off) for s in self.strikes if not s.is_degenerate])

    def support_measure(self) -> float:
        """``lambda(supp f_k) = sum_i (b_i - a_i)`` under disjointness."""
        return sum(b - a for a, b in self.support())

    def energy(self) -> float:
        """``int f_k = sum_i P_i (b_i - a_i)``, the total cumulative energy."""
        return sum(s.energy() for s in self.strikes)

    def cumulative(self, t: float) -> float:
        """``F_k(t)`` by the closed form (5). Exact, not quadrature."""
        return sum(s.velocity * max(0.0, min(t, s.t_off) - s.t_on)
                   for s in self.strikes)


@dataclass(slots=True)
class ParseReport:
    """An audit trail of every repair the parser had to make.

    A parse that silently fixes a malformed stream is a parse you cannot trust,
    so every departure from a literal reading of the MIDI is counted here.
    """

    n_messages: int = 0
    n_note_on: int = 0
    n_note_off: int = 0
    n_strikes: int = 0
    orphan_note_on: int = 0
    orphan_note_off: int = 0
    retrigger_truncated: int = 0
    degenerate: int = 0
    out_of_range: int = 0
    pedal_events: int = 0
    pedal_unclosed: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True when the stream needed no repair at all."""
        return not (self.orphan_note_on or self.orphan_note_off
                    or self.retrigger_truncated or self.out_of_range
                    or self.pedal_unclosed)

    def summary(self) -> str:
        bits = [f"{self.n_messages} messages",
                f"{self.n_strikes} strikes",
                f"{self.pedal_events} pedal events"]
        for label, n in (("orphan note-on", self.orphan_note_on),
                         ("orphan note-off", self.orphan_note_off),
                         ("retrigger truncation", self.retrigger_truncated),
                         ("degenerate", self.degenerate),
                         ("out of range", self.out_of_range),
                         ("unclosed pedal", self.pedal_unclosed)):
            if n:
                bits.append(f"{n} {label}")
        return ", ".join(bits)


class Performance:
    """The direct sum ``f = (+)_k f_k`` of equation (2), plus the pedal track.

    ``tracks`` always has length 88, one :class:`KeyTrack` per key, most of them
    usually empty; that is what makes ``f`` a genuine ``R^88``-valued object
    rather than a sparse bag of notes.
    """

    __slots__ = ("tracks", "pedal", "t_start", "t_end", "report", "sounding")

    def __init__(self, tracks: Sequence[KeyTrack],
                 pedal: Sequence[tuple[float, float]] = (),
                 *, t_start: float | None = None, t_end: float | None = None,
                 report: ParseReport | None = None, sounding: bool = False):
        if len(tracks) != N_KEYS:
            raise ValueError(f"expected {N_KEYS} tracks, got {len(tracks)}")
        self.tracks: tuple[KeyTrack, ...] = tuple(tracks)
        self.pedal: tuple[tuple[float, float], ...] = tuple(_merge(list(pedal)))
        self.report = report or ParseReport()
        self.sounding = sounding
        ts = [s.t_on for tr in self.tracks for s in tr] + [a for a, _ in self.pedal]
        te = [s.t_off for tr in self.tracks for s in tr] + [b for _, b in self.pedal]
        self.t_start = t_start if t_start is not None else (min(ts) if ts else 0.0)
        self.t_end = t_end if t_end is not None else (max(te) if te else 0.0)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_messages(
        cls,
        messages: Iterable[tuple[float, "object"]],
        *,
        t_end: float | None = None,
        orphan_policy: OrphanPolicy = "truncate",
        retrigger_policy: RetriggerPolicy = "truncate",
        range_policy: RangePolicy = "drop",
        strict: bool = False,
    ) -> "Performance":
        """Build the representation from ``(seconds, mido.Message)`` pairs.

        This is the shape :class:`fp30x_studio.core.Capture` records, so a live
        take needs no file round-trip.

        Pairing rules, stated so they can be argued with:

        * ``note_on`` with velocity 0 is a ``note_off`` (MIDI running-status
          convention); both close the key.
        * ``note_off`` for a key that is not held is an **orphan off**: counted
          and discarded, since it names no interval.
        * ``note_on`` for a key already held is a **retrigger**.  Under
          ``retrigger_policy="truncate"`` the open interval is closed at the new
          onset -- the physical truth, the hammer resets the string -- yielding
          two intervals that meet at a point and so satisfy the weak
          disjointness grade.  ``"raise"`` refuses instead.
        * a key still held when the stream ends is an **orphan on**: closed at
          ``t_end`` under ``"truncate"``, discarded under ``"drop"``, refused
          under ``"raise"``.
        * notes outside ``[21, 108]`` are dropped (``"drop"``) or refused
          (``"raise"``).  MIDI channel is ignored throughout: a key has one
          actuator regardless of which channel addressed it.

        The resulting per-key tracks are checked for disjointness at grade
        ``strict``; a failure is a bug in this parser, not in the input, and it
        raises :class:`DisjointnessError`.
        """
        report = ParseReport()
        open_notes: dict[int, tuple[float, int]] = {}
        strikes: list[Strike] = []
        pedal: list[tuple[float, float]] = []
        pedal_down: float | None = None
        last_t = 0.0

        def close(k: int, t: float) -> None:
            a, vel = open_notes.pop(k)
            s = Strike(t_on=a, t_off=max(a, t), key=k, velocity=vel)
            if s.is_degenerate:
                report.degenerate += 1
            strikes.append(s)

        for t, msg in messages:
            report.n_messages += 1
            last_t = max(last_t, t)
            mtype = getattr(msg, "type", None)
            if mtype in ("note_on", "note_off"):
                note = msg.note
                is_on = mtype == "note_on" and msg.velocity > 0
                report.n_note_on += is_on
                report.n_note_off += not is_on
                try:
                    k = key_index(note)
                except ValueError:
                    report.out_of_range += 1
                    if range_policy == "raise":
                        raise ParseError(
                            f"note {note} at t={t:.6f} is off the 88-key range"
                        ) from None
                    report.warnings.append(
                        f"t={t:.6f}: dropped out-of-range note {note}")
                    continue
                if is_on:
                    if k in open_notes:
                        report.retrigger_truncated += 1
                        if retrigger_policy == "raise":
                            raise ParseError(
                                f"key {core.note_name(note)} re-struck at "
                                f"t={t:.6f} while still held"
                            )
                        report.warnings.append(
                            f"t={t:.6f}: {core.note_name(note)} re-struck while "
                            f"held; previous interval truncated")
                        close(k, t)
                    open_notes[k] = (t, int(msg.velocity))
                else:
                    if k not in open_notes:
                        report.orphan_note_off += 1
                        report.warnings.append(
                            f"t={t:.6f}: note-off for {core.note_name(note)} "
                            f"which was not held; discarded")
                        continue
                    close(k, t)
            elif mtype == "control_change" and msg.control == SUSTAIN_CC:
                report.pedal_events += 1
                down = msg.value >= 64
                if down and pedal_down is None:
                    pedal_down = t
                elif not down and pedal_down is not None:
                    pedal.append((pedal_down, t))
                    pedal_down = None

        end = t_end if t_end is not None else last_t
        for k in sorted(open_notes):
            report.orphan_note_on += 1
            name = core.note_name(midi_note(k))
            if orphan_policy == "raise":
                raise ParseError(f"{name} still held when the stream ended")
            if orphan_policy == "drop":
                report.warnings.append(f"{name} still held at end; dropped")
                open_notes.pop(k)
                continue
            report.warnings.append(
                f"{name} still held at end; truncated at t={end:.6f}")
            close(k, end)
        if pedal_down is not None:
            report.pedal_unclosed += 1
            report.warnings.append(
                f"sustain pedal still down at end; closed at t={end:.6f}")
            pedal.append((pedal_down, max(pedal_down, end)))

        report.n_strikes = len(strikes)
        by_key: dict[int, list[Strike]] = {}
        for s in strikes:
            by_key.setdefault(s.key, []).append(s)
        tracks = [KeyTrack(k, by_key.get(k, ()), check=True, strict=strict)
                  for k in range(N_KEYS)]
        return cls(tracks, pedal, t_end=end, report=report)

    @classmethod
    def from_midi_file(cls, path: str | Path, **kwargs) -> "Performance":
        """Parse a standard MIDI file, resolving ticks to seconds via its tempo map.

        Iterating a :class:`mido.MidiFile` merges its tracks and reports
        ``msg.time`` as a delta in seconds, already tempo-corrected; this
        accumulates those deltas into absolute times.
        """
        import mido

        mid = mido.MidiFile(str(path))
        t = 0.0
        msgs: list[tuple[float, object]] = []
        for msg in mid:
            t += msg.time
            if not msg.is_meta:
                msgs.append((t, msg))
        return cls.from_messages(msgs, **kwargs)

    @classmethod
    def from_capture(cls, capture, **kwargs) -> "Performance":
        """Parse anything exposing ``.messages`` as ``[(seconds, mido.Message)]``.

        That is the single interface both capture front ends satisfy: the
        polling :class:`fp30x_studio.core.Capture` and the
        :class:`fp30x_studio.rawcapture.RawCapture` read back from the native
        CoreMIDI tool. Nothing downstream of here knows or cares which one it
        was handed, which is why there is no second copy of this analysis.
        """
        return cls.from_messages(capture.messages, **kwargs)

    @classmethod
    def from_raw_capture(cls, path: str | Path, *, origin: str = "first",
                         **kwargs) -> "Performance":
        """Parse a ``.fp30x`` file from the native CoreMIDI capture tool.

        Convenience only: it reads the file with
        :func:`fp30x_studio.rawcapture.read` and hands the result straight to
        :meth:`from_capture`, so the timestamps carried here are the ones
        CoreMIDI applied near the driver rather than ones a poll loop invented.
        """
        from . import rawcapture

        return cls.from_capture(rawcapture.read(path, origin=origin), **kwargs)

    # -- basic access ------------------------------------------------------

    def __getitem__(self, k: int) -> KeyTrack:
        return self.tracks[k]

    def __len__(self) -> int:
        return N_KEYS

    def __repr__(self) -> str:
        kind = "sounding" if self.sounding else "actuator"
        return (f"Performance({kind}, {self.n_strikes} strikes on "
                f"{len(self.active_keys())} keys, "
                f"[{self.t_start:.3f}, {self.t_end:.3f}] s, "
                f"{len(self.pedal)} pedal spans)")

    def strikes(self) -> list[Strike]:
        """Every strike, in performance order."""
        return sorted(s for tr in self.tracks for s in tr)

    @property
    def n_strikes(self) -> int:
        return sum(len(tr) for tr in self.tracks)

    def active_keys(self) -> list[int]:
        """Indices of keys with at least one strike."""
        return [k for k, tr in enumerate(self.tracks) if len(tr)]

    def check_disjoint(self, *, strict: bool = False, atol: float = 0.0) -> None:
        """Re-run the per-key invariant over all 88 tracks. Raises on violation."""
        for tr in self.tracks:
            tr.check_disjoint(strict=strict, atol=atol)

    # -- the direct-sum vector --------------------------------------------

    def evaluate(self, t: float) -> np.ndarray:
        """``f(t)`` in ``R^88``: the vector of momenta sustained at time ``t``."""
        v = np.zeros(N_KEYS, dtype=float)
        for k, tr in enumerate(self.tracks):
            if len(tr):
                v[k] = tr(t)
        return v

    def evaluate_many(self, ts: Sequence[float] | np.ndarray) -> np.ndarray:
        """``[f(t)]`` as an ``(len(ts), 88)`` array."""
        ts = np.asarray(ts, dtype=float)
        out = np.zeros((ts.size, N_KEYS), dtype=float)
        for s in self.strikes():
            out[(ts >= s.t_on) & (ts <= s.t_off), s.key] += s.velocity
        return out

    # -- functionals -------------------------------------------------------

    def jump_measure(self) -> list[tuple[float, np.ndarray]]:
        """The atoms of the ``R^88``-valued measure ``Df``, sorted by time."""
        acc: dict[float, np.ndarray] = {}
        for s in self.strikes():
            for t, m in ((s.t_on, float(s.velocity)), (s.t_off, -float(s.velocity))):
                vec = acc.get(t)
                if vec is None:
                    vec = acc[t] = np.zeros(N_KEYS, dtype=float)
                vec[s.key] += m
        return [(t, acc[t]) for t in sorted(acc) if np.any(acc[t] != 0.0)]

    def total_variation(self, norm: Norm = "l1") -> float:
        """``V_nu(f)`` from equation (4), for ``nu`` in ``{l^1, l^2, l^inf}``.

        With ``norm="l1"`` this equals ``sum_k |Df_k|(R)``, the sum of the
        component variations; the other norms see cancellation between
        simultaneous events on different keys and are therefore smaller.
        """
        ords = {"l1": 1, "l2": 2, "linf": np.inf}
        if norm not in ords:
            raise ValueError(f"unknown norm {norm!r}; use one of {sorted(ords)}")
        p = ords[norm]
        return float(sum(np.linalg.norm(v, ord=p) for _, v in self.jump_measure()))

    def support(self) -> list[tuple[float, float]]:
        """``supp f = U_k supp f_k`` as merged disjoint closed intervals."""
        return _merge([(s.t_on, s.t_off) for s in self.strikes()
                       if not s.is_degenerate])

    def support_measure(self) -> float:
        """``lambda(supp f)``: total time during which *anything* sounds."""
        return sum(b - a for a, b in self.support())

    def energy(self) -> float:
        """``int sum_k f_k = F(t_end)``, total cumulative energy."""
        return sum(tr.energy() for tr in self.tracks)

    # -- polyphony ---------------------------------------------------------

    def polyphony_at(self, t: float) -> int:
        """``n(t)``, counted with closed intervals."""
        return sum(1 for s in self.strikes() if s.t_on <= t <= s.t_off)

    def polyphony_steps(self) -> tuple[np.ndarray, np.ndarray]:
        """Breakpoints and the value of ``n`` on each open segment.

        Returns ``(times, counts)`` with ``len(counts) == len(times)``: on the
        open segment ``(times[j], times[j+1])`` the polyphony is ``counts[j]``.
        The value *at* a breakpoint may exceed the neighbouring segment values
        by the number of intervals meeting there; use :meth:`polyphony_at` when
        the closed-interval value at a specific instant is what is wanted.
        """
        events: dict[float, int] = {}
        for s in self.strikes():
            if s.is_degenerate:
                continue
            events[s.t_on] = events.get(s.t_on, 0) + 1
            events[s.t_off] = events.get(s.t_off, 0) - 1
        if not events:
            return np.array([self.t_start]), np.array([0])
        ts = np.array(sorted(events))
        counts = np.cumsum([events[t] for t in ts]).astype(int)
        return ts, counts

    def max_polyphony(self) -> int:
        """``max_t n(t)``, attained at a breakpoint."""
        _, counts = self.polyphony_steps()
        breaks = {s.t_on for s in self.strikes()} | {s.t_off for s in self.strikes()}
        closed = max((self.polyphony_at(t) for t in breaks), default=0)
        return max(int(counts.max(initial=0)), closed)

    # -- cumulative energy -------------------------------------------------

    def cumulative(self, t: float, key: int | None = None) -> float:
        """``F(t)`` (aggregate) or ``F_k(t)`` (when ``key`` is given), exactly."""
        if key is not None:
            return self.tracks[key].cumulative(t)
        return sum(tr.cumulative(t) for tr in self.tracks)

    def cumulative_curve(self, key: int | None = None
                         ) -> tuple[np.ndarray, np.ndarray]:
        """Exact breakpoint representation of ``F``: ``(times, values)``.

        ``F`` is piecewise affine with slope ``f`` on each open segment, so the
        polyline through these points *is* ``F``, with no sampling error.  The
        returned times are every interval endpoint, padded with ``t_start`` and
        ``t_end``.
        """
        src = [self.tracks[key]] if key is not None else list(self.tracks)
        strikes = sorted(s for tr in src for s in tr)
        pts = {self.t_start, self.t_end}
        for s in strikes:
            pts.add(s.t_on)
            pts.add(s.t_off)
        ts = np.array(sorted(pts), dtype=float)
        fs = np.array([sum(s.velocity * max(0.0, min(t, s.t_off) - s.t_on)
                           for s in strikes) for t in ts])
        return ts, fs

    def plateaus(self, key: int | None = None) -> list[tuple[float, float]]:
        """The maximal intervals of ``[t_start, t_end]`` on which ``F`` is constant.

        These are the components of the complement of ``supp f`` -- the silences.
        Only non-degenerate components are returned.
        """
        src = [self.tracks[key]] if key is not None else list(self.tracks)
        sup = _merge([(s.t_on, s.t_off) for tr in src for s in tr
                      if not s.is_degenerate])
        out: list[tuple[float, float]] = []
        cursor = self.t_start
        for a, b in sup:
            if a > cursor:
                out.append((cursor, a))
            cursor = max(cursor, b)
        if self.t_end > cursor:
            out.append((cursor, self.t_end))
        return out

    def plateau_measure(self, key: int | None = None) -> float:
        """Total silent time inside ``[t_start, t_end]``."""
        return sum(b - a for a, b in self.plateaus(key))

    # -- sustain -----------------------------------------------------------

    def pedal_down_at(self, t: float) -> bool:
        return any(a <= t <= b for a, b in self.pedal)

    def with_sustain(self, *, strict: bool = False) -> "Performance":
        """Map the actuator representation to the sounding one under CC64.

        Each release that falls inside a pedal span is extended to the end of
        that span; the same key struck again while ringing truncates the earlier
        sounding interval at the new onset.  Returns a new
        :class:`Performance` with ``sounding=True``; the original is untouched.
        With no pedal data this is the identity up to object identity.
        """
        new_tracks: list[KeyTrack] = []
        for k, tr in enumerate(self.tracks):
            extended: list[Strike] = []
            for s in tr:
                off = s.t_off
                for a, b in self.pedal:
                    if a <= off <= b:
                        off = max(off, b)
                        break
                extended.append(Strike(t_on=s.t_on, t_off=off, key=k,
                                       velocity=s.velocity))
            extended.sort()
            for i in range(len(extended) - 1):
                if extended[i].t_off > extended[i + 1].t_on:
                    extended[i] = Strike(
                        t_on=extended[i].t_on, t_off=extended[i + 1].t_on,
                        key=k, velocity=extended[i].velocity)
            new_tracks.append(KeyTrack(k, extended, check=True, strict=strict))
        t_end = max([self.t_end] + [s.t_off for tr in new_tracks for s in tr])
        return Performance(new_tracks, self.pedal, t_start=self.t_start,
                           t_end=t_end, report=self.report, sounding=True)

    # -- export ------------------------------------------------------------

    DTYPE = np.dtype([("key", "i2"), ("note", "i2"), ("name", "U4"),
                      ("t_on", "f8"), ("t_off", "f8"), ("duration", "f8"),
                      ("velocity", "i2"), ("energy", "f8")])

    def to_array(self) -> np.ndarray:
        """One structured-array row per strike, in performance order."""
        rows = [(s.key, s.note, s.name, s.t_on, s.t_off, s.duration,
                 s.velocity, s.energy()) for s in self.strikes()]
        return np.array(rows, dtype=self.DTYPE)

    def to_records(self) -> list[dict]:
        """The same rows as plain dicts, for JSON or CSV without numpy."""
        return [dict(zip(self.DTYPE.names, tuple(r))) for r in self.to_array()]

    def to_dataframe(self):
        """The same rows as a :mod:`pandas` DataFrame. Requires pandas."""
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "to_dataframe() needs pandas; use to_array() or to_records() "
                "for a dependency-free export"
            ) from exc
        return pd.DataFrame.from_records(self.to_array())

    def summary(self) -> str:
        """A one-screen description of the object, for logs and ticket comments."""
        keys = self.active_keys()
        lines = [
            repr(self),
            f"  span            [{self.t_start:.3f}, {self.t_end:.3f}] s "
            f"({self.t_end - self.t_start:.3f} s)",
            f"  keys touched    {len(keys)}"
            + (f" ({core.note_name(midi_note(keys[0]))}"
               f"..{core.note_name(midi_note(keys[-1]))})" if keys else ""),
            f"  max polyphony   {self.max_polyphony()}",
            f"  support measure {self.support_measure():.3f} s",
            f"  silence         {self.plateau_measure():.3f} s "
            f"in {len(self.plateaus())} plateaus",
            f"  variation       l1 {self.total_variation('l1'):.1f}, "
            f"l2 {self.total_variation('l2'):.1f}, "
            f"linf {self.total_variation('linf'):.1f}",
            f"  energy F(T)     {self.energy():.1f} velocity-seconds",
            f"  parse           {self.report.summary()}",
        ]
        return "\n".join(lines)


# -- synthesis (the inverse map, for tests and figures) ---------------------

def synthesize_midi(
    strikes: Iterable[Strike | tuple[int, float, float, int]],
    pedal: Iterable[tuple[float, float]] = (),
    path: str | Path | None = None,
    *,
    ticks_per_beat: int = 480,
    bpm: float = 120.0,
    note_off_as_zero_velocity: bool = False,
):
    """Right inverse of the parser: intervals -> a standard MIDI file.

    ``strikes`` are :class:`Strike` objects or ``(midi_note, t_on, t_off,
    velocity)`` tuples with times in seconds.  Simultaneous events are ordered
    ``note_off < note_on < note_off-of-a-degenerate-strike``, which is the only
    ordering that (a) lets a key be released and re-struck at one instant
    without the release closing the *new* interval, and (b) still allows a
    zero-length strike to be expressed at all.

    Round-tripping through :meth:`Performance.from_midi_file` recovers the same
    intervals up to tick quantisation (``1 / (ticks_per_beat * bpm / 60)`` s,
    about 1.04 ms at the defaults).  Returns the ``mido.MidiFile``; also writes
    it when ``path`` is given.
    """
    import mido

    norm: list[tuple[int, float, float, int]] = []
    for s in strikes:
        if isinstance(s, Strike):
            norm.append((s.note, s.t_on, s.t_off, s.velocity))
        else:
            note, a, b, vel = s
            norm.append((int(note), float(a), float(b), int(vel)))

    ON, OFF, DEGEN_OFF, PEDAL = 1, 0, 2, -1
    events: list[tuple[float, int, int, "mido.Message"]] = []
    for i, (note, a, b, vel) in enumerate(norm):
        if b < a:
            raise ValueError(f"strike {i}: t_off {b} < t_on {a}")
        events.append((a, ON, i, mido.Message("note_on", note=note, velocity=vel)))
        off = (mido.Message("note_on", note=note, velocity=0)
               if note_off_as_zero_velocity
               else mido.Message("note_off", note=note, velocity=64))
        events.append((b, DEGEN_OFF if b == a else OFF, i, off))
    for a, b in pedal:
        events.append((a, PEDAL, 0,
                       mido.Message("control_change", control=SUSTAIN_CC, value=127)))
        events.append((b, PEDAL, 1,
                       mido.Message("control_change", control=SUSTAIN_CC, value=0)))

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    tempo = mido.bpm2tempo(bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    prev_tick = 0
    for t, _, _, msg in events:
        tick = int(round(mido.second2tick(t, ticks_per_beat, tempo)))
        track.append(msg.copy(time=tick - prev_tick))
        prev_tick = tick
    if path is not None:
        mid.save(str(path))
    return mid


def quantisation_step(ticks_per_beat: int = 480, bpm: float = 120.0) -> float:
    """Seconds per tick: the resolution a MIDI round-trip can preserve."""
    return 60.0 / (bpm * ticks_per_beat)
