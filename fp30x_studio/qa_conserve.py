"""Conservation checks: does a stage still contain what went into it?

:mod:`fp30x_studio.qa` inspects a finished file on its own terms -- sample
rate, silence, clipping, loudness. That catches a broken artifact. It cannot
catch a **faithful-looking artifact of the wrong thing**, and twice in one day
that was the actual failure:

  96 kHz          ffmpeg's loudnorm resamples to 192 kHz internally and does
                  not put it back. Every render came out at 96 kHz. Correct
                  audio, right duration, no clipping, passed everything.
  dropped pedal   the two-channel writer in :mod:`fp30x_studio.hands` emitted
                  notes and program changes and no CC64 at all. All 1,512
                  pedal events gone. The render was clean, correctly loud,
                  correctly long -- and detached where the playing was legato,
                  because note-offs are key releases and nothing was holding
                  the strings.

Neither is detectable by looking at the output alone. Both are obvious the
instant you compare the output to the input and ask what went missing.

So: every transform declares what it must preserve, and this module asserts it.
A stage may legitimately change some quantities (a reverb lengthens a file, a
transposition moves pitches). It may not silently drop a whole class of event.
"""

from __future__ import annotations

import argparse, json, sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Check:
    name: str
    passed: bool
    value: str
    detail: str = ""
    fatal: bool = True


@dataclass
class Report:
    label: str
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks if c.fatal)


def midi_census(path: str | Path) -> dict:
    """What a MIDI file contains, in the categories a render can lose."""
    import mido
    m = mido.MidiFile(str(Path(path).expanduser()))
    notes = pedal = other_cc = prog = 0
    pitches: list[int] = []
    pedal_vals: set[int] = set()
    for tr in m.tracks:
        for msg in tr:
            if msg.type == "note_on" and msg.velocity > 0:
                notes += 1
                pitches.append(msg.note)
            elif msg.type == "control_change":
                if msg.control == 64:
                    pedal += 1
                    pedal_vals.add(msg.value)
                else:
                    other_cc += 1
            elif msg.type == "program_change":
                prog += 1
    return {"notes": notes, "pedal_events": pedal, "pedal_values": len(pedal_vals),
            "other_cc": other_cc, "programs": prog,
            "seconds": round(m.length, 2),
            "pitch_min": min(pitches) if pitches else None,
            "pitch_max": max(pitches) if pitches else None}


def fp30_census(path: str | Path) -> dict:
    """The same categories, read out of a raw capture."""
    notes = pedal = 0
    pedal_vals: set[int] = set()
    pitches: list[int] = []
    t_first = t_last = None
    with open(Path(path).expanduser(), encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            f = line.split()
            if len(f) < 4:
                continue
            try:
                ns = int(f[0])
            except ValueError:
                continue
            i = 1
            while i + 2 < len(f):
                try:
                    st = int(f[i], 16); d1 = int(f[i + 1], 16); d2 = int(f[i + 2], 16)
                except ValueError:
                    break
                if st & 0xF0 == 0x90 and d2 > 0:
                    notes += 1
                    pitches.append(d1)
                    if t_first is None:
                        t_first = ns
                    t_last = ns
                elif st & 0xF0 == 0xB0 and d1 == 64:
                    pedal += 1
                    pedal_vals.add(d2)
                i += 3
    return {"notes": notes, "pedal_events": pedal, "pedal_values": len(pedal_vals),
            "other_cc": 0, "programs": 0,
            "seconds": round((t_last - t_first) / 1e9, 2) if t_first else 0.0,
            "pitch_min": min(pitches) if pitches else None,
            "pitch_max": max(pitches) if pitches else None}


def census(path: str | Path) -> dict:
    p = Path(path).expanduser()
    return fp30_census(p) if p.suffix == ".fp30" else midi_census(p)


def compare(src: str | Path, dst: str | Path, *,
            note_tol: float = 0.01, seconds_tol: float = 0.05,
            allow_transpose: bool = False) -> Report:
    """Assert that ``dst`` still carries what ``src`` put in.

    Tolerances are fractional. ``note_tol`` allows the handful of notes a clip
    boundary can clip; it does not allow losing a hand.
    """
    a, b = census(src), census(dst)
    rep = Report(label=f"{Path(src).name} -> {Path(dst).name}")

    dn = abs(b["notes"] - a["notes"]) / max(a["notes"], 1)
    rep.checks.append(Check(
        "notes", dn <= note_tol, f"{a['notes']} -> {b['notes']}",
        f"lost {a['notes'] - b['notes']} notes ({dn:.1%})"))

    # The one that would have caught the pedal bug, instantly and loudly.
    if a["pedal_events"] > 0:
        rep.checks.append(Check(
            "pedal_present", b["pedal_events"] > 0,
            f"{a['pedal_events']} -> {b['pedal_events']}",
            "the source is pedalled and the output has NO CC64 at all. Note-offs "
            "are key releases, so every note will damp on finger-lift and "
            "anything played legato will render detached."))
        # A two-channel writer duplicates the pedal deliberately: controllers
        # are per-channel, so each hand needs its own copy. Normalise by the
        # program-change count so that is not read as a 200% surplus.
        chans = max(b["programs"], 1)
        kept = b["pedal_events"] / chans / a["pedal_events"] if a["pedal_events"] else 1
        rep.checks.append(Check(
            "pedal_retained", kept >= 0.9,
            f"{kept:.0%} of pedal events kept per channel",
            "most of the pedal stream was dropped", fatal=False))
        rep.checks.append(Check(
            "pedal_resolution", b["pedal_values"] >= min(a["pedal_values"], 3),
            f"{a['pedal_values']} -> {b['pedal_values']} distinct values",
            "half-pedalling was flattened to on/off", fatal=False))

    ds = abs(b["seconds"] - a["seconds"]) / max(a["seconds"], 1e-9)
    rep.checks.append(Check(
        "duration", ds <= seconds_tol,
        f"{a['seconds']}s -> {b['seconds']}s", f"differs by {ds:.1%}"))

    if not allow_transpose and a["pitch_min"] is not None and b["pitch_min"] is not None:
        same = (a["pitch_min"] == b["pitch_min"] and a["pitch_max"] == b["pitch_max"])
        # A warning, not a failure: hands.py filters to MIN_PITCH..MAX_PITCH by
        # design, so a few notes at the extremes legitimately do not survive.
        # Losing a whole register is what this is watching for.
        rep.checks.append(Check(
            "pitch_range", same,
            f"{a['pitch_min']}-{a['pitch_max']} -> {b['pitch_min']}-{b['pitch_max']}",
            "the pitch range moved; check a hand was not dropped or transposed",
            fatal=False))

    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m fp30x_studio.qa_conserve",
        description="Assert a stage still contains what went into it.")
    ap.add_argument("src", help=".fp30 or .mid that went in")
    ap.add_argument("dst", nargs="?", help=".mid that came out; omit to just census src")
    ap.add_argument("--allow-transpose", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if not a.dst:
        c = census(a.src)
        print(json.dumps(c, indent=2) if a.json else
              "  ".join(f"{k}={v}" for k, v in c.items()))
        return 0

    rep = compare(a.src, a.dst, allow_transpose=a.allow_transpose)
    if a.json:
        print(json.dumps({"ok": rep.ok, "label": rep.label,
                          "checks": [vars(c) for c in rep.checks]}, indent=2))
    else:
        print(rep.label)
        for c in rep.checks:
            mark = "PASS" if c.passed else ("WARN" if not c.fatal else "FAIL")
            print(f"  [{mark}] {c.name:18s} {c.value}"
                  + (f"   — {c.detail}" if not c.passed else ""))
        print("  OK" if rep.ok else "  FAILED")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
