"""Extract every .fp30 take into JSON for the timbre workbench.

Two outputs:
  * docs/takes.json   -- every take in ~/Music/FP-30X Studio/takes/, re-read
                         from disk each time this runs. The page fetches it.
  * events.json       -- the two reference takes, inlined into the page at
                         build time so file:// still works with no server.

Live takes: a file the capture tool is still appending to has no "# end"
trailer and may end mid-line. rawcapture.scan(whole=False) consumes only
complete lines, so a growing file yields whatever has landed so far and never
invents a half-written record. Notes still held when the bytes run out come
back with closure="end_of_take" and are flagged untrusted, not dropped.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/jake/workspace/audio/fp30x-studio")

from fp30x_studio import rawcapture
from fp30x_studio.pipeline import pairing

TAKES_DIR = Path.home() / "Music/FP-30X Studio/takes"
DOCS = Path(__file__).resolve().parent
REFERENCE = ("2026-08-17-piece.fp30", "2026-08-17-open.fp30")


def extract(path: Path) -> dict:
    """Parse one take. Safe on a file that is still being written."""
    s = rawcapture.scan(path, whole=False)

    records = s.records
    if not records:
        return None

    anchor = s.header.get("anchor_mach_ns")
    origin = records[0].ns

    msgs = []
    seq = 0
    for pkt_seq, rec in enumerate(records):
        for m in pairing.framed(pkt_seq, rec.ns, seq, rec.data):
            msgs.append(m)
            seq += 1

    p = pairing.Pairer()
    res = p.feed(msgs)
    tail = p.finish(records[-1].ns)
    res.intervals.extend(tail.intervals)
    res.defects.extend(tail.defects)

    notes = []
    for iv in res.intervals:
        t_on = (iv.ns_on - origin) / 1e6          # ms
        dur = (iv.ns_off - iv.ns_on) / 1e6 if iv.ns_off is not None else 0.0
        notes.append([
            round(t_on, 1),
            iv.note,
            iv.velocity_on,
            round(dur, 1),
            iv.velocity_off if iv.velocity_off is not None else -1,
            0 if iv.closure == "note_off" else 1,
        ])
    notes.sort(key=lambda r: (r[0], r[1]))

    pedal = [[round((m.ns - origin) / 1e6), m.d2]
             for m in msgs
             if m.kind == "control_change" and m.d1 == pairing.SUSTAIN_CC]

    defects = {}
    for d in res.defects:
        defects[d.cls] = defects.get(d.cls, 0) + 1

    st = path.stat()
    complete = bool(s.trailer)
    duration = (records[-1].ns - origin) / 1e9

    return {
        "id": path.stem,
        "name": path.name,
        "notes": notes,
        "pedal": pedal,
        "duration_s": round(duration, 3),
        "packets": len(records),
        "messages": len(msgs),
        "bytes": st.st_size,
        "mtime": int(st.st_mtime),
        "mtime_str": time.strftime("%H:%M:%S", time.localtime(st.st_mtime)),
        "complete": complete,
        "growing": not complete,
        "defects": defects,
        "torn": s.torn,
    }


def main() -> None:
    takes = []
    for path in sorted(TAKES_DIR.glob("*.fp30")):
        try:
            d = extract(path)
        except Exception as exc:  # a live file must never break the whole build
            print(f"  !! {path.name}: {exc}", file=sys.stderr)
            continue
        if not d or not d["notes"]:
            print(f"  -- {path.name}: no paired notes, skipped")
            continue
        takes.append(d)
        flag = "GROWING" if d["growing"] else "complete"
        print(f"  {path.name:32s} {d['duration_s']:8.1f}s  {len(d['notes']):5d} notes  "
              f"{len(d['pedal']):5d} CC64  {flag}  mtime {d['mtime_str']}  "
              f"defects={d['defects'] or '{}'}")

    takes.sort(key=lambda t: -t["mtime"])
    payload = {
        "generated": int(time.time()),
        "generated_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dir": str(TAKES_DIR),
        "takes": takes,
    }
    out = DOCS / "takes.json"
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {out}  ({out.stat().st_size/1024:.0f} KB, {len(takes)} takes)")

    # the two reference takes, for inlining into the page
    inline = [t for t in takes if t["name"] in REFERENCE]
    sp = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("events.json")
    sp.write_text(json.dumps(inline, separators=(",", ":")))
    print(f"wrote {sp}  ({sp.stat().st_size/1024:.0f} KB, inlined: {[t['id'] for t in inline]})")


if __name__ == "__main__":
    main()
