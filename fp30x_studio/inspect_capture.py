"""Byte-level inspection of a MIDI capture: what did the instrument actually send?

This exists because "the piano supports X" is a claim about a spec sheet and
"the capture contains X" is a claim about bytes, and only the second one can be
checked.  Everything here parses the Standard MIDI File by hand -- including
running status -- rather than trusting a library's decode, so that an absent
message type is established rather than assumed.

The headline question it answers: **is there any continuous per-key data?**  A
digital piano action that senses key velocity from the interval between two
make-contacts emits exactly two messages per note and nothing in between, so a
partial release mid-note is either invisible or a retrigger.  An instrument
with channel pressure (0xDn) or polyphonic key pressure (0xAn) emits a stream.
:func:`pressure_report` distinguishes the two from the data.

    python -m fp30x_studio.inspect_capture ~/Music/FP-30X\\ Studio/takes/*.mid

Also reads the JSON produced by the live wiggle-test protocol (a list of
``[seconds, [status, data1, ...]]``), so a capture taken to settle the question
can be run through the same census as the archive.
"""

from __future__ import annotations

import json
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import core

__all__ = [
    "STATUS", "CC_NAMES", "Event", "Capture",
    "parse_smf", "parse_json", "parse_raw", "load", "pressure_report",
]

#: Channel-voice status nibbles, with the number of data bytes each carries.
STATUS: dict[int, tuple[str, str, int]] = {
    0x80: ("note_off", "Note Off", 2),
    0x90: ("note_on", "Note On", 2),
    0xA0: ("polytouch", "Polyphonic Key Pressure", 2),
    0xB0: ("control_change", "Control Change", 2),
    0xC0: ("program_change", "Program Change", 1),
    0xD0: ("aftertouch", "Channel Pressure", 1),
    0xE0: ("pitchwheel", "Pitch Bend", 2),
}

#: The two message types that carry continuous per-key or per-channel force.
PRESSURE_TYPES = ("aftertouch", "polytouch")

CC_NAMES = {
    0: "Bank Select MSB", 1: "Modulation", 6: "Data Entry MSB", 7: "Channel Volume",
    10: "Pan", 11: "Expression", 64: "Sustain / Damper", 66: "Sostenuto",
    67: "Soft (una corda)", 91: "Reverb", 93: "Chorus",
    120: "All Sound Off", 121: "Reset All Controllers", 123: "All Notes Off",
}


@dataclass(slots=True)
class Event:
    """One channel-voice message with its absolute time in seconds."""

    sec: float
    kind: str
    status: int
    channel: int
    data: tuple[int, ...]
    tick: int = 0
    delta: int = 0
    running_status: bool = False

    @property
    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in (self.status, *self.data))

    @property
    def is_note_on(self) -> bool:
        """A note-on with velocity 0 is a release, per the running-status convention."""
        return self.kind == "note_on" and self.data[1] > 0

    @property
    def is_note_off(self) -> bool:
        return self.kind == "note_off" or (self.kind == "note_on" and self.data[1] == 0)


@dataclass(slots=True)
class Capture:
    """A parsed capture: the events plus everything derived from them."""

    path: Path
    events: list[Event]
    division: int = 480
    source: str = "smf"

    @property
    def duration(self) -> float:
        return max((e.sec for e in self.events), default=0.0)

    def census(self) -> Counter:
        """Count of every channel-voice message type present."""
        return Counter(e.kind for e in self.events)

    def notes(self) -> list[dict]:
        """Note-ons paired to their note-offs, as closed intervals."""
        open_: dict[int, Event] = {}
        out = []
        for e in self.events:
            if e.is_note_on:
                open_[e.data[0]] = e
            elif e.is_note_off and e.data[0] in open_:
                on = open_.pop(e.data[0])
                out.append({"note": on.data[0], "name": core.note_name(on.data[0]),
                            "t_on": on.sec, "t_off": e.sec,
                            "vel_on": on.data[1], "vel_off": e.data[1],
                            "dur": e.sec - on.sec})
        return sorted(out, key=lambda n: n["t_on"])

    def controls(self) -> dict[int, list[tuple[float, int]]]:
        """CC number -> [(seconds, value)]."""
        out = defaultdict(list)
        for e in self.events:
            if e.kind == "control_change":
                out[e.data[0]].append((e.sec, e.data[1]))
        return dict(out)

    def during(self, t0: float, t1: float, note: int | None = None) -> list[Event]:
        """Every event strictly inside ``(t0, t1)``, optionally about one key only.

        Channel pressure carries no key number, so it is always included when it
        falls in the window -- it applies to whatever is sounding.
        """
        out = [e for e in self.events if t0 < e.sec < t1]
        if note is None:
            return out
        return [e for e in out
                if e.kind == "aftertouch"
                or (e.data and e.data[0] == note)]


def _vlq(buf: bytes, i: int) -> tuple[int, int]:
    v = 0
    while True:
        b = buf[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def parse_smf(path: str | Path) -> Capture:
    """Parse a Standard MIDI File to absolute-time events, by hand.

    Handles running status (a data byte arriving with no status byte reuses the
    previous status) and the tempo map, so the seconds are real seconds.
    """
    path = Path(path)
    raw = path.read_bytes()
    if raw[:4] != b"MThd":
        raise ValueError(f"{path} is not a Standard MIDI File")
    hlen = struct.unpack(">I", raw[4:8])[0]
    _fmt, ntrk, division = struct.unpack(">HHH", raw[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported")

    raw_events: list[tuple[int, int, int, tuple[int, ...], bool]] = []
    tempos: list[tuple[int, int]] = [(0, 500_000)]
    i = 8 + hlen
    for _ in range(ntrk):
        if raw[i:i + 4] != b"MTrk":
            raise ValueError(f"expected MTrk at byte {i}")
        end = i + 8 + struct.unpack(">I", raw[i + 4:i + 8])[0]
        j, tick, running = i + 8, 0, None
        while j < end:
            delta, j = _vlq(raw, j)
            tick += delta
            b = raw[j]
            if b == 0xFF:
                mtype = raw[j + 1]
                ln, k = _vlq(raw, j + 2)
                if mtype == 0x51:
                    tempos.append((tick, int.from_bytes(raw[k:k + 3], "big")))
                j, running = k + ln, None
                continue
            if b in (0xF0, 0xF7):
                ln, k = _vlq(raw, j + 1)
                raw_events.append((tick, delta, b, tuple(raw[k:k + ln]), False))
                j, running = k + ln, None
                continue
            used_rs = not (b & 0x80)
            if not used_rs:
                running = b
                j += 1
            elif running is None:
                raise ValueError(f"data byte {b:#04x} with no running status")
            nbytes = STATUS[running & 0xF0][2]
            raw_events.append((tick, delta, running, tuple(raw[j:j + nbytes]), used_rs))
            j += nbytes
        i = end

    tempos = sorted(set(tempos))

    def secs(tick: int) -> float:
        s, pt, pu = 0.0, 0, tempos[0][1]
        for t, us in tempos:
            if t >= tick:
                break
            s += (t - pt) * pu / 1e6 / division
            pt, pu = t, us
        return s + (tick - pt) * pu / 1e6 / division

    events = []
    for tick, delta, status, data, rs in sorted(raw_events, key=lambda e: e[0]):
        if status in (0xF0, 0xF7):
            continue
        events.append(Event(sec=secs(tick), kind=STATUS[status & 0xF0][0],
                            status=status, channel=status & 0x0F, data=data,
                            tick=tick, delta=delta, running_status=rs))
    return Capture(path=path, events=events, division=division, source="smf")


def parse_json(path: str | Path) -> Capture:
    """Parse the live-capture JSON written by the wiggle-test protocol."""
    path = Path(path)
    rows = json.loads(path.read_text())
    events = []
    for sec, by in rows:
        status = by[0]
        if status & 0xF0 not in STATUS:
            continue
        events.append(Event(sec=float(sec), kind=STATUS[status & 0xF0][0],
                            status=status, channel=status & 0x0F,
                            data=tuple(by[1:])))
    return Capture(path=path, events=events, source="json")


def parse_raw(path: str | Path) -> Capture:
    """Parse a ``.fp30x`` file from the native CoreMIDI capture tool.

    Same census, same byte-level scrutiny, on timestamps CoreMIDI applied near
    the driver instead of ones a Python poll loop invented. One packet may hold
    several messages, so the split is delegated to
    :func:`fp30x_studio.rawcapture.split_messages` rather than re-implemented.
    """
    from . import rawcapture

    cap = rawcapture.read(path)
    events = []
    for rec in cap.records:
        sec = cap.seconds(rec.ns)
        for raw in rawcapture.split_messages(rec.data):
            status = raw[0]
            if status & 0xF0 not in STATUS:
                continue
            events.append(Event(sec=sec, kind=STATUS[status & 0xF0][0],
                                status=status, channel=status & 0x0F,
                                data=tuple(raw[1:])))
    return Capture(path=Path(path), events=events, source="fp30x")


def load(path: str | Path) -> Capture:
    """Parse a ``.mid``, the test protocol's ``.json``, or a native ``.fp30x``."""
    s = str(path)
    if s.endswith(".json"):
        return parse_json(path)
    if s.endswith(".fp30x"):
        return parse_raw(path)
    return parse_smf(path)


def pressure_report(caps: list[Capture]) -> dict:
    """The whole question, reduced to numbers.

    Returns the per-type census, the number of paired notes, and how many of
    them had any continuous-pressure message during their sounding life.  If
    ``notes_with_pressure`` is 0 for every note in a decent-sized corpus, the
    action is discrete-event: it samples at the strike and stops looking, and a
    partial release is either invisible or a retrigger.
    """
    census: Counter = Counter()
    n_notes = with_pressure = 0
    longest = None
    for c in caps:
        census.update(c.census())
        for n in c.notes():
            n_notes += 1
            during = c.during(n["t_on"], n["t_off"], note=n["note"])
            if any(e.kind in PRESSURE_TYPES for e in during):
                with_pressure += 1
            if longest is None or n["dur"] > longest["dur"]:
                longest = {**n, "file": c.path.name,
                           "n_during_any": len(c.during(n["t_on"], n["t_off"])),
                           "n_during_key": len(during)}
    return {
        "census": dict(census),
        "n_captures": len(caps),
        "seconds": sum(c.duration for c in caps),
        "n_notes": n_notes,
        "notes_with_pressure": with_pressure,
        "pressure_messages": sum(census.get(k, 0) for k in PRESSURE_TYPES),
        "longest_note": longest,
        "verdict": ("discrete-event: no continuous per-key data"
                    if not sum(census.get(k, 0) for k in PRESSURE_TYPES)
                    else "continuous pressure data IS present"),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    paths = [a for a in args if not a.startswith("-")]
    if not paths:
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python -m fp30x_studio.inspect_capture FILE.mid [FILE...] "
              "[--json]")
        return 2

    caps = [load(p) for p in paths]
    rep = pressure_report(caps)
    if as_json:
        print(json.dumps(rep, indent=1, default=str))
        return 0

    print(f"{rep['n_captures']} captures, {rep['seconds']:.1f} s, "
          f"{rep['n_notes']} notes paired\n")
    print("message-type census")
    for hi, (key, label, _) in sorted(STATUS.items()):
        n = rep["census"].get(key, 0)
        mark = "" if n else "   <-- never once"
        print(f"  0x{hi:02X}n  {label:<26} {n:>6}{mark}")

    print(f"\ncontinuous-pressure messages: {rep['pressure_messages']}")
    print(f"notes with any pressure data during their life: "
          f"{rep['notes_with_pressure']} of {rep['n_notes']}")
    print(f"VERDICT: {rep['verdict']}")

    ln = rep["longest_note"]
    if ln:
        print(f"\nlongest note: {ln['name']} held {ln['dur']:.3f} s in {ln['file']}")
        print(f"  messages about that key while it sounded: {ln['n_during_key']}")
        print(f"  messages of any kind in that window:      {ln['n_during_any']}")

    for c in caps:
        ctrl = c.controls()
        for num, vals in sorted(ctrl.items()):
            vs = [v for _, v in vals]
            binary = set(vs) <= {0, 127}
            print(f"\n{c.path.name}  CC{num} ({CC_NAMES.get(num, '?')}): "
                  f"{len(vs)} msgs, {len(set(vs))} distinct, {min(vs)}..{max(vs)}"
                  f"  {'binary switch' if binary else 'CONTINUOUS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
