"""Reader for the ``.fp30x`` files written by the native CoreMIDI capture tool.

Why there is a second capture format
------------------------------------
:class:`fp30x_studio.core.Capture` polls the MIDI port in Python and stamps each
message with :func:`time.time` at the moment the poll loop noticed it::

    while not stopped:
        for msg in inp.iter_pending(): record(time.time())
        time.sleep(0.002)

That imposes a quantisation of about 2 ms on every take, and it is an artifact
of our own loop rather than a property of the piano, of MIDI, or of Bluetooth.

``native/fp30x_capture.c`` avoids it by not polling. CoreMIDI is callback-driven
and every incoming ``MIDIPacket`` already carries a ``MIDITimeStamp``, which
Apple's SDK headers define as "A host clock time representing the time of an
event, as returned by ``mach_absolute_time()``" (``MIDIServices.h``, the
``MIDITimeStamp`` typedef) applied to "the first MIDI byte in the packet"
(the ``MIDIPacket.timeStamp`` field). The stamp is applied near the driver,
before any of our code runs, so it survives whatever the userland scheduler
does to us afterwards.

The format
----------
Line-oriented UTF-8 text, append-only, so a crash or a laptop sleep truncates
at most the final line. Comment lines carry the header; every other line is one
captured packet::

    # fp30x-capture v1
    # columns abs_ns hex_bytes
    # mach_timebase_numer 125
    # mach_timebase_denom 3
    # anchor_mach_ns 50666761187333
    # anchor_unix_ns 1786981500562062000
    # started_utc 2026-08-17T15:45:00Z
    # source FP-30X Bluetooth
    # sources_connected 1
    50679390933166 90 3C 01
    50679391832208 80 3C 40
    # end packets 40 dropped 0 truncated 0 ts_zero 0 stopped_utc ...

The first field is absolute nanoseconds on the mach clock. ``numer``/``denom``
are recorded so that conversion back to raw ticks is auditable, and the
``anchor_mach_ns``/``anchor_unix_ns`` pair fixes one instant on both the mach
and the wall clock so a take can be placed in civil time.

Reading the trailer honestly
----------------------------
``# end`` is written only on a clean stop. :attr:`RawCapture.complete` is False
without it, which is the file telling you it was cut short rather than you
assuming it was not.

``ts_zero`` counts packets the *source* did not stamp — CoreMIDI documents a
zero timestamp as meaning "now", so those were stamped by the capture tool on
arrival and are no better than what the Python loop produces. If ``ts_zero``
equals ``packets``, this file bought nothing over the old path, and
:attr:`RawCapture.hardware_timestamped` says so.

One interface with the old path
-------------------------------
:attr:`RawCapture.messages` has exactly the shape
:class:`fp30x_studio.core.Capture` exposes — ``list[(seconds, mido.Message)]``
— so :meth:`fp30x_studio.performance.Performance.from_capture` consumes a
``RawCapture`` unchanged and no analysis code is duplicated or forked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

__all__ = [
    "RawCapture",
    "RawRecord",
    "read",
    "split_messages",
    "message_length",
]

#: Number of data bytes following each channel-voice status byte.
_CHANNEL_VOICE_DATA = {
    0x80: 2,  # note off
    0x90: 2,  # note on
    0xA0: 2,  # polyphonic key pressure
    0xB0: 2,  # control change
    0xC0: 1,  # program change
    0xD0: 1,  # channel pressure
    0xE0: 2,  # pitch bend
}

#: Number of data bytes following each System Common status byte.
_SYSTEM_COMMON_DATA = {
    0xF1: 1,  # MTC quarter frame
    0xF2: 2,  # song position pointer
    0xF3: 1,  # song select
    0xF6: 0,  # tune request
}


def message_length(status: int) -> int | None:
    """Total byte length of the message introduced by ``status``.

    Returns ``None`` for 0xF0 (system exclusive), which is delimited by 0xF7
    rather than by a fixed length. Real-time status bytes (0xF8-0xFF) are
    single-byte messages and return 1.
    """
    if status < 0x80:
        raise ValueError(f"{status:#04x} is a data byte, not a status byte")
    if status >= 0xF8:
        return 1
    if status == 0xF0:
        return None
    if status >= 0xF1:
        n = _SYSTEM_COMMON_DATA.get(status)
        return None if n is None else n + 1
    return _CHANNEL_VOICE_DATA[status & 0xF0] + 1


def split_messages(data: bytes) -> list[bytes]:
    """Split one packet's bytes into individual complete MIDI messages.

    A single ``MIDIPacket`` may carry several messages -- CoreMIDI documents the
    timestamp as applying to the first byte, and that "The MIDI messages in the
    packet must always be complete, except for system-exclusive", and that
    "Running status is not allowed". So every message here begins with its own
    status byte and this split needs no running-status state.

    Anything unparseable is returned as a single trailing chunk rather than
    silently dropped, so nothing captured is ever discarded by the reader.
    """
    out: list[bytes] = []
    i = 0
    n = len(data)
    while i < n:
        status = data[i]
        if status < 0x80:
            # Not legal here, but keep the bytes rather than lose them.
            out.append(data[i:])
            break
        if status == 0xF0:
            end = data.find(0xF7, i)
            if end == -1:
                out.append(data[i:])
                break
            out.append(data[i:end + 1])
            i = end + 1
            continue
        length = message_length(status)
        if length is None or i + length > n:
            out.append(data[i:])
            break
        out.append(data[i:i + length])
        i += length
    return out


@dataclass(slots=True)
class RawRecord:
    """One captured packet: absolute mach nanoseconds plus its raw bytes."""

    ns: int
    data: bytes

    @property
    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)


@dataclass(slots=True)
class RawCapture:
    """A parsed ``.fp30x`` capture.

    ``messages`` gives the same ``(seconds, mido.Message)`` shape that
    :class:`fp30x_studio.core.Capture` produces, which is the single interface
    the analysis layer consumes.
    """

    path: Path
    header: dict[str, str] = field(default_factory=dict)
    trailer: dict[str, str] = field(default_factory=dict)
    records: list[RawRecord] = field(default_factory=list)
    origin_ns: int = 0

    # -- header-derived facts ---------------------------------------------

    @property
    def version(self) -> int:
        m = re.search(r"v(\d+)", self.header.get("fp30x-capture", ""))
        return int(m.group(1)) if m else 0

    @property
    def timebase(self) -> tuple[int, int]:
        """``(numer, denom)`` of the mach timebase this file was written under."""
        return (int(self.header.get("mach_timebase_numer", 1)),
                int(self.header.get("mach_timebase_denom", 1)))

    @property
    def source(self) -> str:
        return self.header.get("source", "")

    @property
    def complete(self) -> bool:
        """True only if the tool wrote its ``# end`` trailer, i.e. stopped cleanly."""
        return bool(self.trailer)

    @property
    def n_dropped(self) -> int:
        """Packets the ring buffer could not accept. Should be 0."""
        return int(self.trailer.get("dropped", 0))

    @property
    def n_ts_zero(self) -> int:
        """Packets the source did not stamp, so the tool stamped them on arrival."""
        return int(self.trailer.get("ts_zero", 0))

    @property
    def hardware_timestamped(self) -> bool:
        """True when at least one packet carried a timestamp from below us.

        False means every event was stamped on arrival by the capture tool, so
        this file has no timing advantage over the Python poll loop and should
        not be presented as if it did.
        """
        return bool(self.records) and self.n_ts_zero < len(self.records)

    def unix_ns(self, ns: int) -> int:
        """Map an absolute mach nanosecond value onto the wall clock."""
        a_mach = int(self.header.get("anchor_mach_ns", 0))
        a_unix = int(self.header.get("anchor_unix_ns", 0))
        return a_unix + (ns - a_mach)

    # -- the shared interface ---------------------------------------------

    def seconds(self, ns: int) -> float:
        """Absolute mach nanoseconds -> seconds relative to this take's origin."""
        return (ns - self.origin_ns) / 1e9

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[RawRecord]:
        return iter(self.records)

    @property
    def duration(self) -> float:
        if not self.records:
            return 0.0
        return self.seconds(self.records[-1].ns)

    @property
    def messages(self) -> list[tuple[float, object]]:
        """``[(seconds, mido.Message)]`` -- the shape the analysis layer eats.

        A packet holding several messages contributes several entries, all at
        that packet's timestamp, since CoreMIDI stamps the first byte of the
        packet and the messages inside it were simultaneous on the wire.

        Bytes mido cannot decode are skipped here (they are still available
        losslessly through :attr:`records`), so a stray real-time byte cannot
        break a take.
        """
        import mido

        out: list[tuple[float, object]] = []
        for rec in self.records:
            t = self.seconds(rec.ns)
            for raw in split_messages(rec.data):
                try:
                    out.append((t, mido.Message.from_bytes(list(raw))))
                except Exception:  # noqa: BLE001 - undecodable bytes are not fatal
                    continue
        return out

    def summary(self) -> str:
        bits = [
            f"{self.path.name}",
            f"  source          {self.source or '(unrecorded)'}",
            f"  packets         {len(self.records)}",
            f"  duration        {self.duration:.3f} s",
            f"  timebase        {self.timebase[0]}/{self.timebase[1]} "
            f"({self.timebase[0] / self.timebase[1]:.4f} ns per tick)",
            f"  clean stop      {self.complete}",
            f"  dropped         {self.n_dropped}",
            f"  source-stamped  {len(self.records) - self.n_ts_zero} of "
            f"{len(self.records)}",
        ]
        return "\n".join(bits)


_COMMENT = re.compile(r"^#\s*(\S+)\s*(.*)$")


def read(path: str | Path, *, origin: str = "first") -> RawCapture:
    """Parse a ``.fp30x`` file.

    ``origin="first"`` puts t=0 at the first captured packet, which is what the
    analysis layer expects. ``origin="header"`` puts t=0 at the instant the
    capture tool started, so leading silence is preserved.

    A file with no ``# end`` trailer parses fine; :attr:`RawCapture.complete`
    reports the truncation rather than the parse failing.
    """
    path = Path(path)
    cap = RawCapture(path=path)

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                m = _COMMENT.match(line)
                if not m:
                    continue
                key, rest = m.group(1), m.group(2).strip()
                if key == "end":
                    parts = rest.split()
                    cap.trailer = {parts[i]: parts[i + 1]
                                   for i in range(0, len(parts) - 1, 2)}
                else:
                    cap.header[key] = rest
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                ns = int(parts[0])
                data = bytes(int(b, 16) for b in parts[1:])
            except ValueError:
                # A torn final line from a crash or a sleep. Stop, don't guess.
                break
            cap.records.append(RawRecord(ns=ns, data=data))

    if origin == "header":
        cap.origin_ns = int(cap.header.get("anchor_mach_ns", 0))
    elif cap.records:
        cap.origin_ns = cap.records[0].ns
    return cap
