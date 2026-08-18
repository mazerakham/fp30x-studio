"""What the player did, read out of the pipeline index -- never re-parsed here.

There is exactly one parser in this repository and it is
:mod:`fp30x_studio.pipeline`. This module opens the index, ingests if the file
has grown, and reads the ``interval`` and ``message`` tables. It does not touch
bytes, does not frame packets, and in particular does not repeat today's defect
of reading one MIDI message per CoreMIDI packet -- 293 of the piece's 4861
packets carry two or three messages, and taking only the first invents orphans
and a 37.97 s note that was never played.

Two things come out of here that a naive MIDI reader would throw away:

**Release velocity.** Every one of the piece's 1730 intervals was closed by a
real note-off, so ``velocity_off`` is measured rather than inferred on all of
them. It carries 3.684 bits of entropy against the strike byte's 3.140 and is
near-independent of it (mutual information 0.074 of a possible 2.16), so it is
a genuine second channel and the only one the instrument offers after the
strike. The renderer spends it on damper-fall speed.

**Continuous CC64.** The sustain pedal is a half-damper sensor, not a switch:
the piece take sends 1713 CC64 messages spanning all 128 positions, and only
303 of them are 0. On the open take 780 of 804 releases happened with the
damper already off the strings. A binary pedal model would damp notes the
player was still holding with the pedal, which is most of the piece.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Note", "Score", "read_score"]


@dataclass(slots=True, frozen=True)
class Note:
    """One key press, fully determined at strike and at release.

    There is no third field to add: the take contains zero aftertouch and zero
    polyphonic pressure, so nothing about this note changes between ``t_on``
    and ``t_off`` except the pedal, which is global.
    """

    note: int          # MIDI number, 21..108
    t_on: float        # s from the first packet
    t_off: float
    velocity_on: int   # 1..127 as sent; 1..96 on the piece, 1..103 on the open take
    velocity_off: int  # 0..127, measured whenever ``trusted``
    trusted: bool      # a real note-off closed it, so both endpoints mean what they say

    @property
    def duration(self) -> float:
        return self.t_off - self.t_on


@dataclass(slots=True)
class Score:
    name: str
    duration: float
    notes: list[Note] = field(default_factory=list)
    #: ``(t, position)`` for CC64, position 0..127, in arrival order.
    pedal: list[tuple[float, int]] = field(default_factory=list)
    source: str = ""

    def summary(self) -> str:
        if not self.notes:
            return f"{self.name}: empty"
        von = [n.velocity_on for n in self.notes]
        voff = [n.velocity_off for n in self.notes if n.trusted]
        pitches = [n.note for n in self.notes]
        ped = [p for _, p in self.pedal]
        untrusted = sum(1 for n in self.notes if not n.trusted)
        return (
            f"{self.name}: {self.duration:.2f} s, {len(self.notes)} notes, "
            f"pitch {min(pitches)}-{max(pitches)}, "
            f"strike vel {min(von)}-{max(von)}, "
            f"release vel {min(voff)}-{max(voff)} over {len(set(voff))} values, "
            f"{len(self.pedal)} CC64 messages"
            + (f" spanning {min(ped)}-{max(ped)}" if ped else "")
            + (f", {untrusted} intervals with an inferred endpoint" if untrusted else "")
        )

    def peak_polyphony(self) -> int:
        events = sorted(
            [(n.t_on, 1) for n in self.notes] + [(n.t_off, -1) for n in self.notes])
        cur = peak = 0
        for _, d in events:
            cur += d
            peak = max(peak, cur)
        return peak


def read_score(take: str | Path, *, index: str | Path | None = None) -> Score:
    """Open (and if necessary bring up to date) the take's index, and read it."""
    from ..pipeline import TakeStore

    take = Path(take).expanduser()
    with TakeStore(take, index=index) as store:
        store.ingest()
        if not store.accounted:
            raise AssertionError(
                f"{take.name}: the index does not account for every message; "
                f"refusing to render from it")
        notes = [
            Note(
                note=r["note"],
                t_on=store.seconds(r["ns_on"]),
                t_off=store.seconds(r["ns_off"]),
                velocity_on=r["velocity_on"],
                velocity_off=r["velocity_off"] if r["velocity_off"] is not None else 64,
                trusted=bool(r["trusted"]),
            )
            for r in store.intervals()
        ]
        pedal = [
            (store.seconds(r["ns"]), int(r["d2"]))
            for r in store.messages(kinds=["control_change"])
            if r["d1"] == 64
        ]
        return Score(
            name=take.stem,
            duration=store.duration,
            notes=sorted(notes, key=lambda n: (n.t_on, n.note)),
            pedal=sorted(pedal),
            source=str(take),
        )
