"""The materialised index for one take: incremental, checkpointed, on SQLite.

The problem this solves
-----------------------
Every question asked of a take today was answered by re-reading the whole file
and re-deriving the pairing from scratch, once per question. That is quadratic
in the number of questions, it is slow on a long take, and -- worse -- it means
each answer carries its own private copy of the pairing rules, so two answers
can disagree about what a note even is. There was such a disagreement today and
it produced a phantom 37.97 s note (see ``PROVENANCE.md``).

So the pairing is computed **once**, written down, and everything reads it.

Why it can be incremental
-------------------------
The ``.fp30`` format is append-only text and the capture tool writes strictly
monotone timestamps and ``fsync``s every 0.25 s. Those three facts together mean
that a byte offset is a complete resume point: nothing before it can ever
change, and the file's own durability guarantee is what makes the offset safe
to trust. Ingest therefore stores ``byte_offset`` plus the pairing state -- the
set of keys currently down, a few hundred bytes -- and picks up from there.

The same call path serves a finished take and one still being recorded. The only
difference is :meth:`TakeStore.finish`, which closes the keys still down; that
runs only when the file's ``# end`` trailer proves the stream really ended.
Closing them earlier would invent a release that was never played.

The append-only assumption is checked, not assumed: the size and a digest of the
file's head are stored with the offset, and a file that shrank or was rewritten
forces a full re-ingest rather than a corrupt resume.

Where the index lives
---------------------
Beside the take, in a hidden ``.index`` directory. The takes directory holds
primary evidence and stays readable; the index is a derived cache and may be
deleted at any time, which is why nothing but this module ever writes to it.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from .. import rawcapture
from .pairing import Defect, Interval, Msg, Pairer, framed

__all__ = ["TakeStore", "IngestResult", "index_path_for"]

SCHEMA_VERSION = 1

#: Bytes of the file head hashed to detect a rewrite rather than an append.
_HEAD_BYTES = 4096

SCHEMA = """
CREATE TABLE IF NOT EXISTS take (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    path                TEXT NOT NULL,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL,
    source              TEXT,
    origin_ns           INTEGER NOT NULL DEFAULT 0,
    anchor_mach_ns      INTEGER,
    anchor_unix_ns      INTEGER,
    timebase_numer      INTEGER,
    timebase_denom      INTEGER,
    started_utc         TEXT,
    stopped_utc         TEXT,
    timing_grade        TEXT NOT NULL DEFAULT 'unknown',
    timing_trusted      INTEGER NOT NULL DEFAULT 0,
    timing_note         TEXT NOT NULL DEFAULT '',
    schema_version      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoint (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    byte_offset         INTEGER NOT NULL DEFAULT 0,
    file_size           INTEGER NOT NULL DEFAULT 0,
    head_sha256         TEXT NOT NULL DEFAULT '',
    n_packets           INTEGER NOT NULL DEFAULT 0,
    n_messages          INTEGER NOT NULL DEFAULT 0,
    last_ns             INTEGER,
    complete            INTEGER NOT NULL DEFAULT 0,
    finished            INTEGER NOT NULL DEFAULT 0,
    torn                INTEGER NOT NULL DEFAULT 0,
    pairer_state        TEXT NOT NULL DEFAULT '',
    updated_utc         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS header (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trailer (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS packet (
    seq                 INTEGER PRIMARY KEY,
    ns                  INTEGER NOT NULL,
    n_bytes             INTEGER NOT NULL,
    n_messages          INTEGER NOT NULL,
    hex                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS packet_ns ON packet(ns);

CREATE TABLE IF NOT EXISTS message (
    seq                 INTEGER PRIMARY KEY,
    packet_seq          INTEGER NOT NULL,
    ns                  INTEGER NOT NULL,
    kind                TEXT NOT NULL,
    channel             INTEGER,
    d1                  INTEGER,
    d2                  INTEGER,
    hex                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS message_ns ON message(ns);
CREATE INDEX IF NOT EXISTS message_kind ON message(kind, ns);

CREATE TABLE IF NOT EXISTS interval (
    id                  INTEGER PRIMARY KEY,
    key                 INTEGER NOT NULL,
    note                INTEGER NOT NULL,
    on_seq              INTEGER NOT NULL,
    off_seq             INTEGER,
    ns_on               INTEGER NOT NULL,
    ns_off              INTEGER NOT NULL,
    velocity_on         INTEGER NOT NULL,
    velocity_off        INTEGER,
    closure             TEXT NOT NULL,
    trusted             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS interval_note ON interval(note, ns_on);
CREATE INDEX IF NOT EXISTS interval_on ON interval(ns_on);

-- Exactly one row per message. The count of this table against the count of
-- `message` is the guarantee that nothing was silently dropped.
CREATE TABLE IF NOT EXISTS accounting (
    msg_seq             INTEGER PRIMARY KEY,
    role                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS accounting_role ON accounting(role);

CREATE TABLE IF NOT EXISTS defect (
    id                  INTEGER PRIMARY KEY,
    cls                 TEXT NOT NULL,
    ns                  INTEGER NOT NULL,
    msg_seq             INTEGER,
    note                INTEGER,
    detail              TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS defect_cls ON defect(cls);
"""


def index_path_for(take: str | Path) -> Path:
    """Where the index for ``take`` lives. Created lazily, never by hand."""
    take = Path(take).expanduser()
    return take.parent / ".index" / (take.name + ".sqlite3")


@dataclass(slots=True)
class IngestResult:
    """What one ingest pass did, in terms an operator can check."""

    take: str
    from_offset: int
    to_offset: int
    new_packets: int = 0
    new_messages: int = 0
    new_intervals: int = 0
    new_defects: int = 0
    full_reingest: bool = False
    reingest_reason: str = ""
    complete: bool = False
    finished: bool = False
    torn: bool = False
    open_notes: list[int] = field(default_factory=list)

    @property
    def new_bytes(self) -> int:
        return self.to_offset - self.from_offset

    def line(self) -> str:
        bits = [f"{self.take}: +{self.new_bytes} B "
                f"({self.from_offset} -> {self.to_offset})",
                f"+{self.new_packets} packets", f"+{self.new_messages} messages",
                f"+{self.new_intervals} intervals"]
        if self.new_defects:
            bits.append(f"+{self.new_defects} defects")
        if self.full_reingest:
            bits.append(f"FULL RE-INGEST ({self.reingest_reason})")
        if self.torn:
            bits.append("torn line: stopped there")
        if self.open_notes:
            bits.append(f"{len(self.open_notes)} keys still down")
        bits.append("complete" if self.complete else "still open")
        return ", ".join(bits)


class TakeStore:
    """The index for one take. Open it, ingest into it, then query it."""

    def __init__(self, take: str | Path, *, index: str | Path | None = None):
        self.take = Path(take).expanduser()
        self.index = Path(index).expanduser() if index else index_path_for(self.take)
        self.index.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.index)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._ensure_take_row()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "TakeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _ensure_take_row(self) -> None:
        row = self.db.execute("SELECT id FROM take WHERE id = 1").fetchone()
        if row is None:
            kind = "fp30" if self.take.suffix.lower() in (".fp30", ".fp30x") else "midi"
            self.db.execute(
                "INSERT INTO take (id, path, name, kind, schema_version) "
                "VALUES (1, ?, ?, ?, ?)",
                (str(self.take), self.take.stem, kind, SCHEMA_VERSION))
        self.db.execute(
            "INSERT OR IGNORE INTO checkpoint (id) VALUES (1)")
        self.db.commit()

    # -- checkpoint --------------------------------------------------------

    @property
    def checkpoint(self) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM checkpoint WHERE id = 1").fetchone()

    @property
    def meta(self) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM take WHERE id = 1").fetchone()

    def header(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.execute("SELECT * FROM header")}

    def trailer(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.execute("SELECT * FROM trailer")}

    def _head_digest(self) -> str:
        with self.take.open("rb") as fh:
            return hashlib.sha256(fh.read(_HEAD_BYTES)).hexdigest()

    def _reset(self) -> None:
        for table in ("packet", "message", "interval", "accounting", "defect",
                      "header", "trailer"):
            self.db.execute(f"DELETE FROM {table}")
        self.db.execute(
            "UPDATE checkpoint SET byte_offset = 0, file_size = 0, "
            "head_sha256 = '', n_packets = 0, n_messages = 0, last_ns = NULL, "
            "complete = 0, finished = 0, torn = 0, pairer_state = '' WHERE id = 1")
        self.db.commit()

    # -- ingest ------------------------------------------------------------

    def ingest(self, *, reset: bool = False) -> IngestResult:
        """Bring the index up to date with the file. Cheap when nothing changed.

        Resumes from the stored byte offset unless the file contradicts it, in
        which case it re-ingests from zero and says why.
        """
        if self.meta["kind"] == "midi":
            return self._ingest_midi(reset=reset)

        if not self.take.exists():
            raise FileNotFoundError(self.take)

        cp = self.checkpoint
        size = self.take.stat().st_size
        reason = ""
        if reset:
            reason = "asked for"
        elif cp["byte_offset"] and size < cp["byte_offset"]:
            reason = f"file shrank to {size} B, below the {cp['byte_offset']} B mark"
        elif cp["head_sha256"] and self._head_digest() != cp["head_sha256"]:
            reason = "file head changed, so this is not the same file appended to"
        if reason:
            self._reset()
            cp = self.checkpoint

        start = cp["byte_offset"]
        res = IngestResult(take=self.take.name, from_offset=start,
                           to_offset=start, full_reingest=bool(reason),
                           reingest_reason=reason)

        s = rawcapture.scan(self.take, start=start)
        pairer = Pairer.from_json(cp["pairer_state"] or None)

        seq = cp["n_messages"]
        pseq = cp["n_packets"]
        origin_ns = self.meta["origin_ns"]

        packets: list[tuple] = []
        msgs: list[Msg] = []
        for rec in s.records:
            batch = list(framed(pseq, rec.ns, seq, rec.data))
            packets.append((pseq, rec.ns, len(rec.data), len(batch), rec.hex))
            msgs.extend(batch)
            seq += len(batch)
            pseq += 1

        if pseq and not origin_ns:
            origin_ns = s.records[0].ns if s.records else 0

        pr = pairer.feed(msgs)

        with self.db:
            for k, v in s.header.items():
                self.db.execute(
                    "INSERT OR REPLACE INTO header VALUES (?, ?)", (k, v))
            for k, v in s.trailer.items():
                self.db.execute(
                    "INSERT OR REPLACE INTO trailer VALUES (?, ?)", (k, v))
            self.db.executemany(
                "INSERT OR REPLACE INTO packet VALUES (?, ?, ?, ?, ?)", packets)
            self.db.executemany(
                "INSERT OR REPLACE INTO message VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(m.seq, m.packet_seq, m.ns, m.kind, m.channel, m.d1, m.d2, m.hex)
                 for m in msgs])
            self._write_pairing(pr.intervals, pr.defects, pr.roles)

            complete = bool(s.trailer) or bool(self.trailer())
            self.db.execute(
                "UPDATE checkpoint SET byte_offset = ?, file_size = ?, "
                "head_sha256 = ?, n_packets = ?, n_messages = ?, last_ns = ?, "
                "complete = ?, torn = ?, pairer_state = ?, updated_utc = ? "
                "WHERE id = 1",
                (s.next_offset, size, self._head_digest(), pseq, seq,
                 pairer.last_ns, int(complete), int(s.torn), pairer.to_json(),
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            self.db.execute("UPDATE take SET origin_ns = ? WHERE id = 1",
                            (origin_ns,))

        res.to_offset = s.next_offset
        res.new_packets = len(packets)
        res.new_messages = len(msgs)
        res.new_intervals = len(pr.intervals)
        res.new_defects = len(pr.defects)
        res.torn = s.torn
        res.complete = bool(self.trailer())

        self._describe_timing()
        if res.complete and not self.checkpoint["finished"]:
            res.new_intervals += self._finish(pairer)
            res.finished = True
        res.open_notes = pairer.open_notes
        return res

    def _write_pairing(self, intervals: Iterable[Interval],
                       defects: Iterable[Defect],
                       roles: Iterable[tuple[int, str]]) -> None:
        nid = (self.db.execute("SELECT COALESCE(MAX(id), 0) FROM interval")
               .fetchone()[0])
        rows = []
        for iv in intervals:
            nid += 1
            rows.append((nid, iv.key, iv.note, iv.on_seq, iv.off_seq, iv.ns_on,
                         iv.ns_off, iv.velocity_on, iv.velocity_off,
                         iv.closure, int(iv.trusted)))
        self.db.executemany(
            "INSERT INTO interval VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        self.db.executemany(
            "INSERT OR REPLACE INTO accounting VALUES (?, ?)", list(roles))
        did = (self.db.execute("SELECT COALESCE(MAX(id), 0) FROM defect")
               .fetchone()[0])
        drows = []
        for d in defects:
            did += 1
            drows.append((did, d.cls, d.ns, d.msg_seq, d.note, d.detail))
        self.db.executemany(
            "INSERT INTO defect VALUES (?, ?, ?, ?, ?, ?)", drows)

    def _finish(self, pairer: Pairer) -> int:
        """Close the keys still down, now that the file proves the stream ended."""
        end_ns = self.checkpoint["last_ns"] or 0
        pr = pairer.finish(end_ns)
        with self.db:
            self._write_pairing(pr.intervals, pr.defects, ())
            self.db.execute(
                "UPDATE checkpoint SET finished = 1, pairer_state = ? WHERE id = 1",
                (pairer.to_json(),))
        return len(pr.intervals)

    def _describe_timing(self) -> None:
        """Record how much the timestamps in this take can be trusted, and why."""
        h, t = self.header(), self.trailer()
        packets = self.checkpoint["n_packets"]
        ts_zero = int(t.get("ts_zero", 0))
        if self.meta["kind"] != "fp30":
            return
        if packets and ts_zero >= packets:
            grade, trusted = "arrival-stamped", 0
            note = ("every packet reached us unstamped, so the capture tool "
                    "stamped them on arrival; no better than the poll loop")
        elif packets:
            grade, trusted = "hardware", 1
            note = ("CoreMIDI packet timestamps applied near the driver, "
                    f"{packets - ts_zero} of {packets} stamped by the source")
        else:
            grade, trusted, note = "unknown", 0, "no packets ingested yet"
        with self.db:
            self.db.execute(
                "UPDATE take SET source = ?, timing_grade = ?, timing_trusted = ?, "
                "timing_note = ?, anchor_mach_ns = ?, anchor_unix_ns = ?, "
                "timebase_numer = ?, timebase_denom = ?, started_utc = ?, "
                "stopped_utc = ? WHERE id = 1",
                (h.get("source", ""), grade, trusted, note,
                 int(h.get("anchor_mach_ns", 0) or 0),
                 int(h.get("anchor_unix_ns", 0) or 0),
                 int(h.get("mach_timebase_numer", 0) or 0),
                 int(h.get("mach_timebase_denom", 0) or 0),
                 h.get("started_utc", ""), t.get("stopped_utc", "")))

    # -- the old poll-loop takes -------------------------------------------

    def _ingest_midi(self, *, reset: bool = False) -> IngestResult:
        """Ingest a take from the Python poll loop, marked untrustworthy.

        These are the pre-2026-08-17 ``.mid`` files. Their message *types* are
        sound evidence and their timing is not: the ``time.sleep(0.002)`` loop
        that recorded them imposed a quantisation floor of about 2.08 ms and
        recorded 20% of gaps as exactly zero. They are ingested so they can be
        queried, and marked so that no timing query silently mixes them in with
        hardware-stamped takes.

        There is no incremental path here: a standard MIDI file is not
        append-only and its header carries a length, so it is re-read whole.
        """
        import mido

        self._reset()
        res = IngestResult(take=self.take.name, from_offset=0, to_offset=0,
                           full_reingest=True,
                           reingest_reason="standard MIDI file, read whole")
        mid = mido.MidiFile(str(self.take))
        t = 0.0
        pairer = Pairer()
        packets: list[tuple] = []
        msgs: list[Msg] = []
        seq = 0
        for msg in mid:
            t += msg.time
            if msg.is_meta:
                continue
            ns = int(round(t * 1e9))
            raw = bytes(msg.bytes())
            batch = list(framed(len(packets), ns, seq, raw))
            packets.append((len(packets), ns, len(raw), len(batch),
                            " ".join(f"{b:02X}" for b in raw)))
            msgs.extend(batch)
            seq += len(batch)
        pr = pairer.feed(msgs)

        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO packet VALUES (?, ?, ?, ?, ?)", packets)
            self.db.executemany(
                "INSERT OR REPLACE INTO message VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(m.seq, m.packet_seq, m.ns, m.kind, m.channel, m.d1, m.d2, m.hex)
                 for m in msgs])
            self._write_pairing(pr.intervals, pr.defects, pr.roles)
            self.db.execute(
                "UPDATE checkpoint SET byte_offset = ?, file_size = ?, "
                "n_packets = ?, n_messages = ?, last_ns = ?, complete = 1, "
                "torn = 0, pairer_state = ?, updated_utc = ? WHERE id = 1",
                (self.take.stat().st_size, self.take.stat().st_size,
                 len(packets), seq, pairer.last_ns, pairer.to_json(),
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            self.db.execute(
                "UPDATE take SET origin_ns = 0, timing_grade = 'poll-loop', "
                "timing_trusted = 0, timing_note = ? WHERE id = 1",
                ("recorded by the Python poll loop: host arrival times with a "
                 "~2.08 ms quantisation floor. Message types are evidence; "
                 "timing is not.",))
        res.new_packets = len(packets)
        res.new_messages = len(msgs)
        res.new_intervals = len(pr.intervals)
        res.new_defects = len(pr.defects)
        res.complete = True
        res.to_offset = self.take.stat().st_size
        res.new_intervals += self._finish(pairer)
        res.finished = True
        return res

    # -- reads -------------------------------------------------------------

    @property
    def origin_ns(self) -> int:
        return self.meta["origin_ns"]

    def seconds(self, ns: int) -> float:
        return (ns - self.origin_ns) / 1e9

    @property
    def duration(self) -> float:
        last = self.checkpoint["last_ns"]
        return 0.0 if last is None else self.seconds(last)

    @property
    def timing_trusted(self) -> bool:
        return bool(self.meta["timing_trusted"])

    def count(self, table: str) -> int:
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def census(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT kind, COUNT(*) FROM message GROUP BY kind ORDER BY 2 DESC")}

    def role_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT role, COUNT(*) FROM accounting GROUP BY role ORDER BY 2 DESC")}

    def defect_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT cls, COUNT(*) FROM defect GROUP BY cls ORDER BY 2 DESC")}

    def closure_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute(
            "SELECT closure, COUNT(*) FROM interval GROUP BY closure ORDER BY 2 DESC")}

    def packet_ns(self) -> list[int]:
        return [r[0] for r in self.db.execute(
            "SELECT ns FROM packet ORDER BY seq")]

    def note_onsets(self) -> list[int]:
        return [r[0] for r in self.db.execute(
            "SELECT ns_on FROM interval ORDER BY ns_on")]

    def intervals(self, *, trusted_only: bool = False, note: int | None = None
                  ) -> Iterator[sqlite3.Row]:
        sql = "SELECT * FROM interval"
        where, args = [], []
        if trusted_only:
            where.append("trusted = 1")
        if note is not None:
            where.append("note = ?")
            args.append(note)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.execute(sql + " ORDER BY ns_on, note", args)

    def messages(self, *, kinds: Iterable[str] | None = None) -> Iterator[sqlite3.Row]:
        sql = "SELECT * FROM message"
        args: list = []
        if kinds:
            kinds = list(kinds)
            sql += " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
            args = kinds
        return self.db.execute(sql + " ORDER BY seq", args)

    def defects(self, cls: str | None = None) -> Iterator[sqlite3.Row]:
        if cls:
            return self.db.execute(
                "SELECT * FROM defect WHERE cls = ? ORDER BY ns", (cls,))
        return self.db.execute("SELECT * FROM defect ORDER BY ns")

    @property
    def accounted(self) -> bool:
        """Every message has exactly one role. The no-silent-drop guarantee."""
        return self.count("accounting") == self.count("message")

    # -- handing back to the analysis layer --------------------------------

    def to_performance(self, *, trusted_only: bool = False, strict: bool = False):
        """Build a :class:`fp30x_studio.performance.Performance` from the index.

        This is a *materialisation* of the pairing already computed here, not a
        second implementation of it: the intervals come straight out of the
        table. ``tests/test_pipeline.py`` asserts that the result is identical
        to ``Performance.from_capture()`` on the real takes, which is what keeps
        the two from drifting apart.

        ``trusted_only=True`` keeps only intervals a real note-off closed. Use
        it when a measurement would be corrupted by an inferred endpoint --
        release velocity, say -- and accept that it is a biased subsample.
        """
        from ..performance import N_KEYS, KeyTrack, ParseReport, Performance, Strike

        by_key: dict[int, list[Strike]] = {}
        dropped = 0
        for r in self.intervals(trusted_only=trusted_only):
            k = r["key"]
            if not 0 <= k < N_KEYS or not 1 <= r["velocity_on"] <= 127:
                dropped += 1
                continue
            by_key.setdefault(k, []).append(Strike(
                t_on=self.seconds(r["ns_on"]), t_off=self.seconds(r["ns_off"]),
                key=k, velocity=r["velocity_on"]))
        tracks = [KeyTrack(k, by_key.get(k, ()), check=True, strict=strict)
                  for k in range(N_KEYS)]

        pedal: list[tuple[float, float]] = []
        down: float | None = None
        n_pedal = 0
        for m in self.messages(kinds=["control_change"]):
            if m["d1"] != 64:
                continue
            n_pedal += 1
            t = self.seconds(m["ns"])
            if (m["d2"] or 0) >= 64 and down is None:
                down = t
            elif (m["d2"] or 0) < 64 and down is not None:
                pedal.append((down, t))
                down = None
        report = ParseReport()
        d = self.defect_counts()
        report.n_messages = self.count("message")
        report.n_strikes = self.count("interval")
        report.pedal_events = n_pedal
        report.orphan_note_off = d.get("orphan_note_off", 0)
        report.orphan_note_on = d.get("held_at_end", 0)
        report.retrigger_truncated = d.get("restrike_before_note_off", 0)
        report.out_of_range = dropped
        if down is not None:
            report.pedal_unclosed = 1
            pedal.append((down, max(down, self.duration)))
        return Performance(tracks, pedal, t_end=self.duration, report=report)
