"""Note-on/note-off pairing, as a layer that accounts for every message.

Why this is its own layer
-------------------------
Every measurement below the raw byte level lives on paired intervals: duration,
release velocity, polyphony, support, the total variation of *F*. So does every
data-quality problem. Pairing is therefore the one place where a silent repair
would poison everything downstream while leaving no trace, and the rule here is
that **nothing is dropped**. Each message leaves this layer with exactly one
:class:`Role`, and :func:`Pairer.audit` refuses to return a result in which the
number of roles issued differs from the number of messages consumed.

That is a stronger guarantee than a warning count. A warning can be ignored; a
message with no role is a failed assertion.

The defect classes, and what each one implies about the link
------------------------------------------------------------
``orphan_note_off``
    A note-off arrived for a key that was not down. Either a note-on was lost
    on the way here, or the take began with the key already held. Both are
    recorded; neither is silently discarded, and the message keeps its role.

``restrike_before_note_off``
    A note-on arrived for a key already down. On a real piano action this is
    impossible -- the key must return before it can be struck again -- so it
    means the note-off in between was lost. This is the single most informative
    defect about link health.

``held_at_end``
    The stream ended with the key down. On a clean stop that is usually the
    player's hands still on the keys, so it is a defect of the *take*, not
    necessarily of the link.

``implausible_duration``
    An interval longer than :data:`IMPLAUSIBLE_S`. Advisory only. It is
    physically possible to hold a key for a minute; it is also what a lost
    note-off looks like when the key is not struck again for a minute. Flagged,
    never repaired, because the two are indistinguishable from the bytes.

``non_monotone_timestamp``
    Timestamps went backwards. The capture tool guarantees they do not, so this
    would mean file corruption or two captures concatenated.

``undecodable_bytes``
    Bytes the MIDI framer could not read. Counted and kept, so the message
    total always reconciles.

Reconciling with the disjointness invariant
-------------------------------------------
:mod:`fp30x_studio.performance` represents a key as a sum of scaled indicators
on **disjoint** closed intervals and raises :class:`DisjointnessError` when they
overlap. That invariant is not negotiable and is not relaxed here: it is what
makes ``f`` a well-defined *R^88*-valued step function rather than a bag of
overlapping notes.

The data can violate the *physical* premise behind it -- that every note-on has
a matching note-off -- without ever violating the mathematics, provided the
repair is chosen honestly. On a re-strike this layer closes the open interval
**at the instant of the new onset**. That is the physically true reading (the
hammer has reset the string) and it yields two intervals meeting at a point, so
``b_i <= a_{i+1}`` holds and the a.e.-disjointness grade is satisfied exactly.

No interval is invented and no endpoint is moved to a time that was not observed
in the stream. What is lost is the *true* release time, which was never
captured. So each interval carries a :attr:`Interval.closure` recording which
observation ended it, and only ``closure == "note_off"`` means a release
velocity and a duration that were measured rather than inferred. Downstream code
filters on ``Interval.trusted``; the defect stays visible all the way out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Iterator

__all__ = [
    "Msg",
    "Interval",
    "Defect",
    "Pairer",
    "PairingResult",
    "classify",
    "IMPLAUSIBLE_S",
    "SUSTAIN_CC",
    "DEFECT_CLASSES",
    "ROLES",
]

#: Intervals longer than this are flagged, never repaired. See the module docstring.
IMPLAUSIBLE_S = 20.0

SUSTAIN_CC = 64

#: Every defect class this layer can emit. Fixed, so a report can list zeros.
DEFECT_CLASSES = (
    "orphan_note_off",
    "restrike_before_note_off",
    "held_at_end",
    "implausible_duration",
    "non_monotone_timestamp",
    "undecodable_bytes",
)

#: Every role a message can leave this layer with. Exactly one is issued each.
ROLES = (
    "interval_on",
    "interval_off",
    "orphan_note_off",
    "pedal",
    "control",
    "other",
    "undecodable",
)

# mido's own type names, so a census here is comparable with one taken from
# ``mido.Message.type`` without a translation table.
_CHANNEL_KIND = {
    0x80: "note_off",
    0x90: "note_on",
    0xA0: "polytouch",
    0xB0: "control_change",
    0xC0: "program_change",
    0xD0: "aftertouch",
    0xE0: "pitchwheel",
}

_SYSTEM_KIND = {
    0xF0: "sysex",
    0xF1: "quarter_frame",
    0xF2: "songpos",
    0xF3: "song_select",
    0xF6: "tune_request",
    0xF8: "clock",
    0xFA: "start",
    0xFB: "continue",
    0xFC: "stop",
    0xFE: "active_sensing",
    0xFF: "reset",
}


@dataclass(slots=True, frozen=True)
class Msg:
    """One MIDI message, already framed out of its packet.

    ``seq`` is the global message index in arrival order and is the identity
    every other table refers to, so a defect or an interval endpoint can always
    be traced back to the exact bytes that produced it.
    """

    seq: int
    packet_seq: int
    ns: int
    kind: str
    channel: int | None
    d1: int | None
    d2: int | None
    raw: bytes

    @property
    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.raw)

    @property
    def is_note_on(self) -> bool:
        """A note-on that actually sounds. Velocity 0 is a release, per MIDI."""
        return self.kind == "note_on" and (self.d2 or 0) > 0

    @property
    def is_note_off(self) -> bool:
        return self.kind == "note_off" or (self.kind == "note_on" and not self.d2)


def classify(raw: bytes) -> tuple[str, int | None, int | None, int | None]:
    """``(kind, channel, data1, data2)`` for one complete MIDI message.

    Anything that is not a well-formed message -- a truncated tail, a stray data
    byte -- comes back as ``"undecodable"`` rather than raising, because these
    bytes were genuinely received and the count has to reconcile.
    """
    if not raw:
        return ("undecodable", None, None, None)
    status = raw[0]
    if status < 0x80:
        return ("undecodable", None, None, None)
    if status < 0xF0:
        kind = _CHANNEL_KIND[status & 0xF0]
        need = 2 if (status & 0xF0) not in (0xC0, 0xD0) else 1
        if len(raw) < need + 1:
            return ("undecodable", None, None, None)
        d1 = raw[1]
        d2 = raw[2] if need == 2 else None
        return (kind, status & 0x0F, d1, d2)
    kind = _SYSTEM_KIND.get(status)
    if kind is None:
        return ("undecodable", None, None, None)
    if kind == "sysex" and raw[-1] != 0xF7:
        return ("undecodable", None, None, None)
    return (kind, None, None, None)


@dataclass(slots=True)
class Interval:
    """One closed interval ``[a, b]`` on one key, with its provenance.

    ``closure`` says which observation ended it:

    * ``note_off``    -- a real note-off. The only trusted case.
    * ``restrike``    -- closed at a later note-on for the same key, because the
      note-off was lost. The endpoint is an observed time, but it is an onset,
      not a release.
    * ``end_of_take`` -- the key was still down when the stream ended.
    """

    key: int
    note: int
    on_seq: int
    ns_on: int
    velocity_on: int
    off_seq: int | None = None
    ns_off: int | None = None
    velocity_off: int | None = None
    closure: str = "open"

    @property
    def trusted(self) -> bool:
        """True only when both endpoints were observed as what they claim to be."""
        return self.closure == "note_off"

    def duration_ns(self) -> int | None:
        if self.ns_off is None:
            return None
        return self.ns_off - self.ns_on


@dataclass(slots=True)
class Defect:
    cls: str
    ns: int
    msg_seq: int | None = None
    note: int | None = None
    detail: str = ""


@dataclass(slots=True)
class PairingResult:
    """What one :meth:`Pairer.feed` pass produced."""

    intervals: list[Interval] = field(default_factory=list)
    defects: list[Defect] = field(default_factory=list)
    roles: list[tuple[int, str]] = field(default_factory=list)
    n_messages: int = 0

    def audit(self) -> None:
        """Refuse to hand back a result in which a message went unaccounted for."""
        if len(self.roles) != self.n_messages:
            raise AssertionError(
                f"pairing accounted for {len(self.roles)} of {self.n_messages} "
                f"messages; every message must leave this layer with a role"
            )


@dataclass(slots=True)
class _Open:
    seq: int
    ns: int
    velocity: int


class Pairer:
    """Streaming note pairing with resumable state.

    The only state that has to survive between incremental ingests is the set
    of keys currently down, which is why it serialises to a few hundred bytes
    of JSON no matter how long the take is.
    """

    __slots__ = ("open", "last_ns", "n_messages", "implausible_s")

    def __init__(self, *, implausible_s: float = IMPLAUSIBLE_S):
        self.open: dict[int, _Open] = {}
        self.last_ns: int | None = None
        self.n_messages = 0
        self.implausible_s = implausible_s

    # -- resumability ------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({
            "open": {str(n): [o.seq, o.ns, o.velocity] for n, o in self.open.items()},
            "last_ns": self.last_ns,
            "n_messages": self.n_messages,
        })

    @classmethod
    def from_json(cls, blob: str | None, *, implausible_s: float = IMPLAUSIBLE_S
                  ) -> "Pairer":
        p = cls(implausible_s=implausible_s)
        if not blob:
            return p
        d = json.loads(blob)
        p.open = {int(n): _Open(*v) for n, v in d.get("open", {}).items()}
        p.last_ns = d.get("last_ns")
        p.n_messages = d.get("n_messages", 0)
        return p

    @property
    def open_notes(self) -> list[int]:
        return sorted(self.open)

    # -- the pass ----------------------------------------------------------

    def feed(self, messages: Iterable[Msg]) -> PairingResult:
        """Consume messages in arrival order and emit intervals, defects, roles."""
        out = PairingResult()

        def close(note: int, ns: int, closure: str, off_seq: int | None,
                  velocity_off: int | None) -> Interval:
            o = self.open.pop(note)
            iv = Interval(
                key=note - 21, note=note, on_seq=o.seq, ns_on=o.ns,
                velocity_on=o.velocity, off_seq=off_seq,
                # An out-of-order stamp must not produce b < a; the capture tool
                # guarantees monotone stamps, and the guarantee is enforced, not
                # assumed.
                ns_off=max(o.ns, ns), velocity_off=velocity_off, closure=closure,
            )
            if (iv.ns_off - iv.ns_on) / 1e9 > self.implausible_s:
                out.defects.append(Defect(
                    cls="implausible_duration", ns=iv.ns_on, msg_seq=o.seq,
                    note=note,
                    detail=f"{(iv.ns_off - iv.ns_on) / 1e9:.3f} s on key held "
                           f"open; longer than {self.implausible_s:g} s is "
                           f"indistinguishable from a lost note-off",
                ))
            out.intervals.append(iv)
            return iv

        for m in messages:
            out.n_messages += 1
            self.n_messages += 1
            if self.last_ns is not None and m.ns < self.last_ns:
                out.defects.append(Defect(
                    cls="non_monotone_timestamp", ns=m.ns, msg_seq=m.seq,
                    detail=f"stamp went back {self.last_ns - m.ns} ns",
                ))
            self.last_ns = m.ns if self.last_ns is None else max(self.last_ns, m.ns)

            if m.kind == "undecodable":
                out.defects.append(Defect(
                    cls="undecodable_bytes", ns=m.ns, msg_seq=m.seq,
                    detail=m.hex,
                ))
                out.roles.append((m.seq, "undecodable"))
                continue

            if m.kind in ("note_on", "note_off"):
                note = m.d1 or 0
                if m.is_note_on:
                    if note in self.open:
                        out.defects.append(Defect(
                            cls="restrike_before_note_off", ns=m.ns,
                            msg_seq=m.seq, note=note,
                            detail="key struck while already down; the action "
                                   "cannot do this, so the note-off was lost",
                        ))
                        close(note, m.ns, "restrike", None, None)
                    self.open[note] = _Open(seq=m.seq, ns=m.ns,
                                            velocity=int(m.d2 or 0))
                    out.roles.append((m.seq, "interval_on"))
                else:
                    if note not in self.open:
                        out.defects.append(Defect(
                            cls="orphan_note_off", ns=m.ns, msg_seq=m.seq,
                            note=note,
                            detail="release for a key that was not down; the "
                                   "note-on was lost or predates the take",
                        ))
                        out.roles.append((m.seq, "orphan_note_off"))
                        continue
                    close(note, m.ns, "note_off", m.seq, int(m.d2 or 0))
                    out.roles.append((m.seq, "interval_off"))
                continue

            if m.kind == "control_change":
                out.roles.append(
                    (m.seq, "pedal" if m.d1 == SUSTAIN_CC else "control"))
                continue

            out.roles.append((m.seq, "other"))

        out.audit()
        return out

    def finish(self, end_ns: int) -> PairingResult:
        """Close whatever is still down, because the stream really has ended.

        Only call this once the file is known complete. On a take still being
        written, the keys are simply still down and inventing a release for them
        would be a fabrication.
        """
        out = PairingResult()
        for note in sorted(self.open):
            o = self.open.pop(note)
            ns_off = max(o.ns, end_ns)
            out.intervals.append(Interval(
                key=note - 21, note=note, on_seq=o.seq, ns_on=o.ns,
                velocity_on=o.velocity, off_seq=None, ns_off=ns_off,
                velocity_off=None, closure="end_of_take",
            ))
            out.defects.append(Defect(
                cls="held_at_end", ns=o.ns, msg_seq=o.seq, note=note,
                detail=f"still down when the stream ended; interval closed at "
                       f"the last observed time, not at a release",
            ))
        return out


def framed(packet_seq: int, ns: int, first_seq: int, data: bytes) -> Iterator[Msg]:
    """Split one packet into :class:`Msg` objects, numbering from ``first_seq``.

    All messages in a packet share the packet's timestamp: CoreMIDI stamps the
    first byte, and the messages inside were simultaneous on the wire. Reading
    only the first message of a packet -- which is the obvious thing to do and
    is wrong -- silently deletes the rest, and because chords arrive as
    multi-message packets it deletes them exactly where the music is densest.
    """
    from .. import rawcapture

    seq = first_seq
    for raw in rawcapture.split_messages(data):
        kind, channel, d1, d2 = classify(raw)
        yield Msg(seq=seq, packet_seq=packet_seq, ns=ns, kind=kind,
                  channel=channel, d1=d1, d2=d2, raw=raw)
        seq += 1
