"""Health readings on the *link*, computed at ingest and written beside the data.

These are not analysis results. They say nothing about the playing. They answer
one question -- "did the bytes arrive intact?" -- and they have to be computed
every time, because the failure they look for is invisible by construction from
anywhere else.

The invisible failure
---------------------
``native/fp30x_capture.c`` reports ``dropped 0`` on every take so far, and that
number is honest but nearly meaningless for this purpose: it counts packets
*our own ring buffer* refused, i.e. packets CoreMIDI already handed us. A MIDI
message lost on the Bluetooth radio never reaches CoreMIDI, so CoreMIDI never
counts it, so neither do we. **Radio-side loss is invisible to the drop counter
by construction.** The only evidence for it is structural: a note-off with no
note-on, a key struck while already down, a note-on and note-off census that
does not balance. That inference is what :class:`LossInference` reports, and
the caveat is printed with it every time rather than living in a comment.

The 5 ms lattice
----------------
BLE MIDI does not send events when they happen. The peripheral batches them into
connection events on the negotiated connection interval, so arrival times land
on a grid. On this link the grid is 5.000 ms, and it is exact enough to be
usable as a diagnostic: on the 2026-08-17 piece take, 4819 of 4860 inter-packet
gaps are integer multiples of 5.000000 ms to the nanosecond.

The residue is worth more than the fraction. An excursion off the lattice that
*returns* to it -- gaps of, say, 1 ms then 4 ms -- is one packet delivered late
and the next early, with the phase preserved: jitter, nothing lost. An excursion
that leaves the phase permanently shifted is a different animal. So
:class:`Lattice` groups the outliers into runs and reports, for each, whether
the phase was restored. On the piece take all 21 runs restore it.

Note that the lattice is a property of the *arrival* times, not of the playing.
It is the floor on this link's timing resolution: two notes played 1 ms apart
can arrive in the same connection event and be recorded as simultaneous. Within
a single packet they genuinely are simultaneous as far as this capture can tell,
which is why multi-message packets are counted here too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd

from .pairing import DEFECT_CLASSES

__all__ = ["Lattice", "LossInference", "IntegrityReport", "report",
           "LOSS_CAVEAT", "LATTICE_NS"]

#: The BLE connection interval this link negotiates, in nanoseconds.
LATTICE_NS = 5_000_000

LOSS_CAVEAT = (
    "The capture tool's `dropped` counter cannot see this: it counts only "
    "packets CoreMIDI handed us and refused, and a message lost on the radio "
    "never reaches CoreMIDI. Structural inference is the only signal there is."
)


def _dur(ns: int) -> str:
    """A duration in whatever unit does not turn it into scientific notation."""
    for scale, unit in ((1e6, "ms"), (1e3, "us")):
        if abs(ns) >= scale:
            return f"{ns / scale:g} {unit}"
    return f"{ns} ns"


@dataclass(slots=True)
class Lattice:
    """How well inter-packet gaps sit on the BLE connection-interval grid."""

    step_ns: int = LATTICE_NS
    n_gaps: int = 0
    n_on_lattice: int = 0
    observed_gcd_ns: int = 0
    outliers: list[tuple[int, int, int]] = field(default_factory=list)
    runs: list[tuple[int, int, int, bool]] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        return self.n_on_lattice / self.n_gaps if self.n_gaps else 1.0

    @property
    def runs_phase_restoring(self) -> int:
        return sum(1 for *_, ok in self.runs if ok)

    @property
    def phase_intact(self) -> bool:
        """True when every off-lattice excursion came back to the same phase."""
        return self.runs_phase_restoring == len(self.runs)


def lattice(ns: list[int], *, step_ns: int = LATTICE_NS) -> Lattice:
    """Measure the arrival grid from a take's packet timestamps."""
    out = Lattice(step_ns=step_ns)
    gaps = [b - a for a, b in zip(ns, ns[1:])]
    out.n_gaps = len(gaps)
    if not gaps:
        return out
    out.observed_gcd_ns = gcd(*gaps) if len(gaps) > 1 else gaps[0]
    out.n_on_lattice = sum(1 for g in gaps if g % step_ns == 0)
    out.outliers = [(i, g, g % step_ns) for i, g in enumerate(gaps)
                    if g % step_ns]

    # Group consecutive outliers into runs, and ask whether each run's gaps sum
    # to a whole number of steps -- i.e. whether the grid phase survived it.
    i = 0
    idx = [i for i, _, _ in out.outliers]
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        total = sum(gaps[k] for k in range(idx[i], idx[j] + 1))
        out.runs.append((idx[i], idx[j], total, total % step_ns == 0))
        i = j + 1
    return out


@dataclass(slots=True)
class LossInference:
    """What the structure of the stream implies about messages that never arrived."""

    note_on: int = 0
    note_off: int = 0
    orphan_note_off: int = 0
    restrike_before_note_off: int = 0
    held_at_end: int = 0
    implausible_duration: int = 0
    messages: int = 0
    reported_dropped: int = 0

    @property
    def balance(self) -> int:
        """note-ons minus releases. Zero on a stream with nothing missing."""
        return self.note_on - self.note_off

    @property
    def inferred_lost_note_offs(self) -> int:
        """A key struck while down can only mean its release was lost."""
        return self.restrike_before_note_off

    @property
    def inferred_lost_note_ons(self) -> int:
        """A release for a key that was not down can only mean its onset was lost."""
        return self.orphan_note_off

    @property
    def inferred_lost(self) -> int:
        return self.inferred_lost_note_ons + self.inferred_lost_note_offs

    @property
    def rate(self) -> float:
        return self.inferred_lost / self.messages if self.messages else 0.0

    @property
    def clean(self) -> bool:
        return self.inferred_lost == 0 and self.balance == self.held_at_end


@dataclass(slots=True)
class IntegrityReport:
    """Everything ingest can say about whether a take is sound."""

    name: str = ""
    path: str = ""
    duration_s: float = 0.0
    complete: bool = False
    torn: bool = False
    timing_grade: str = "unknown"
    timing_trusted: bool = False
    timing_note: str = ""
    packets: int = 0
    messages: int = 0
    multi_message_packets: int = 0
    max_messages_per_packet: int = 0
    accounted: bool = True
    census: dict[str, int] = field(default_factory=dict)
    roles: dict[str, int] = field(default_factory=dict)
    closures: dict[str, int] = field(default_factory=dict)
    defects: dict[str, int] = field(default_factory=dict)
    lattice: Lattice = field(default_factory=Lattice)
    loss: LossInference = field(default_factory=LossInference)
    intervals: int = 0
    trusted_intervals: int = 0
    open_notes: list[int] = field(default_factory=list)

    # -- the one-line answer ----------------------------------------------

    @property
    def verdict(self) -> str:
        """"Is this take any good?" -- the whole point of the report."""
        if not self.messages:
            return "EMPTY: nothing ingested"
        bad = []
        if not self.accounted:
            bad.append("messages unaccounted for")
        if self.loss.inferred_lost:
            bad.append(f"{self.loss.inferred_lost} messages inferred lost")
        elif not self.loss.clean:
            bad.append(f"note-on/release balance {self.loss.balance:+d} is not "
                       f"explained by the {self.loss.held_at_end} keys held at "
                       f"the end")
        if self.torn:
            bad.append("torn line in the file")
        # The BLE arrival lattice is only a meaningful model of a take whose
        # timestamps came from the hardware. A poll-loop take failing it says
        # something about the poll loop, which is already known and already said.
        if self.timing_trusted and not self.lattice.phase_intact:
            bad.append("lattice phase slipped")
        soft = []
        if not self.timing_trusted:
            soft.append(f"timing {self.timing_grade}, not usable for timing work")
        if self.defects.get("held_at_end"):
            soft.append(f"{self.defects['held_at_end']} keys down at the end")
        if self.defects.get("implausible_duration"):
            soft.append(f"{self.defects['implausible_duration']} implausibly "
                        f"long intervals")
        if not self.complete:
            soft.append("no clean stop yet (still recording, or cut short)")

        if bad:
            return "SUSPECT: " + "; ".join(bad + soft)
        head = (f"GOOD: {self.loss.note_on} note-ons and {self.loss.note_off} "
                f"releases, all paired, no inferred loss, "
                f"{self.lattice.fraction:.1%} on the "
                f"{self.lattice.step_ns / 1e6:g} ms lattice")
        return head + ("; " + "; ".join(soft) if soft else "")

    def lines(self) -> list[str]:
        """The terminal report. Short on purpose: it is read between takes."""
        L = self.lattice
        out = [
            f"{self.name}  {self.duration_s:.1f} s  "
            f"{self.packets} packets  {self.messages} messages",
            f"  verdict         {self.verdict}",
            f"  timing          {self.timing_grade} "
            f"({'trusted' if self.timing_trusted else 'NOT trusted'}) "
            f"-- {self.timing_note}",
            f"  census          " + ", ".join(
                f"{k} {v}" for k, v in self.census.items()),
            f"  aftertouch      polytouch {self.census.get('polytouch', 0)}, "
            f"channel pressure {self.census.get('aftertouch', 0)} "
            f"(the FP-30X sends neither)",
            f"  packets         {self.multi_message_packets} carry more than one "
            f"message, up to {self.max_messages_per_packet}",
            f"  accounting      {sum(self.roles.values())} of {self.messages} "
            f"messages have a role -- "
            f"{'complete' if self.accounted else 'INCOMPLETE'}",
            f"  intervals       {self.intervals} "
            f"({self.trusted_intervals} closed by a real note-off)",
            f"  lattice         {L.n_on_lattice}/{L.n_gaps} gaps "
            f"({L.fraction:.2%}) are exact multiples of "
            f"{L.step_ns / 1e6:g} ms; observed gcd {_dur(L.observed_gcd_ns)}"
            + ("" if self.timing_trusted else "  (not a meaningful model here)"),
            f"                  {len(L.runs)} off-lattice runs, "
            f"{L.runs_phase_restoring} restore the grid phase"
            + ("" if L.phase_intact or not self.timing_trusted
               else "  <-- PHASE SLIPPED"),
        ]
        if L.outliers[:6]:
            out.append("                  outlier gaps (index, gap ms, residue ms): "
                       + ", ".join(f"({i}, {g / 1e6:g}, {r / 1e6:g})"
                                   for i, g, r in L.outliers[:6])
                       + (" ..." if len(L.outliers) > 6 else ""))
        out.append(
            f"  defects         " + ", ".join(
                f"{c} {self.defects.get(c, 0)}" for c in DEFECT_CLASSES))
        out.append(
            f"  inferred loss   {self.loss.inferred_lost} messages "
            f"({self.loss.rate:.3%}): "
            f"{self.loss.inferred_lost_note_ons} note-ons, "
            f"{self.loss.inferred_lost_note_offs} note-offs; "
            f"on/off balance {self.loss.balance:+d}; "
            f"tool reported dropped {self.loss.reported_dropped}")
        out.append(f"                  {LOSS_CAVEAT}")
        if self.open_notes:
            out.append(f"  still down      {len(self.open_notes)} keys: "
                       f"{self.open_notes}")
        return out

    def text(self) -> str:
        return "\n".join(self.lines())


def report(store) -> IntegrityReport:
    """Compute the full integrity report from an ingested :class:`TakeStore`."""
    cp, meta = store.checkpoint, store.meta
    r = IntegrityReport(
        name=meta["name"], path=meta["path"], duration_s=store.duration,
        complete=bool(cp["complete"]), torn=bool(cp["torn"]),
        timing_grade=meta["timing_grade"],
        timing_trusted=bool(meta["timing_trusted"]),
        timing_note=meta["timing_note"],
        packets=cp["n_packets"], messages=cp["n_messages"],
        accounted=store.accounted, census=store.census(),
        roles=store.role_counts(), closures=store.closure_counts(),
        defects={c: 0 for c in DEFECT_CLASSES} | store.defect_counts(),
        intervals=store.count("interval"),
    )
    r.trusted_intervals = store.db.execute(
        "SELECT COUNT(*) FROM interval WHERE trusted = 1").fetchone()[0]
    row = store.db.execute(
        "SELECT COUNT(*), COALESCE(MAX(n_messages), 0) FROM packet "
        "WHERE n_messages > 1").fetchone()
    r.multi_message_packets = row[0]
    r.max_messages_per_packet = store.db.execute(
        "SELECT COALESCE(MAX(n_messages), 0) FROM packet").fetchone()[0]

    r.lattice = lattice(store.packet_ns())

    on = store.db.execute(
        "SELECT COUNT(*) FROM message WHERE kind = 'note_on' AND d2 > 0"
    ).fetchone()[0]
    off = store.db.execute(
        "SELECT COUNT(*) FROM message WHERE kind = 'note_off' "
        "OR (kind = 'note_on' AND COALESCE(d2, 0) = 0)").fetchone()[0]
    r.loss = LossInference(
        note_on=on, note_off=off,
        orphan_note_off=r.defects.get("orphan_note_off", 0),
        restrike_before_note_off=r.defects.get("restrike_before_note_off", 0),
        held_at_end=r.defects.get("held_at_end", 0),
        implausible_duration=r.defects.get("implausible_duration", 0),
        messages=r.messages,
        reported_dropped=int(store.trailer().get("dropped", 0) or 0),
    )
    state = cp["pairer_state"]
    if state:
        import json
        r.open_notes = sorted(int(k) for k in json.loads(state).get("open", {}))
    return r
