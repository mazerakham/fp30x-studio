"""Silence-split a take, and cut a clip out of it.

The capture layer stays dumb on purpose: ``.fp30`` is one long append-only file
with every packet the piano sent, silences included, because a silence is
evidence too -- it is where the playing stopped. Deciding that a 40 s gap ended
a piece is an interpretation, and interpretations belong here, in a projection
over the raw, where they can be changed without touching what was recorded.

    python -m fp30x_studio.segment list 2026-08-20-a
    python -m fp30x_studio.segment clip 2026-08-20-a --from 12:07 --to end --name cmajor

``list`` splits on onset gaps and characterises each piece; ``clip`` cuts one
out. The cut writes three files that answer three different questions:

``<name>.fp30``   the raw slice, header preserved and absolute timestamps left
                  alone, so it is still addressable against the parent take.
``<name>.mid``    editable notes.
``<name>.wav``    something to actually listen to.

The ``.fp30`` slice is a byte-faithful cut. The ``.mid`` is not: notes still
held at the cut are released and the sustain pedal is lifted, because a MIDI
file that ends mid-note renders as a stuck chord. That asymmetry is the point --
the raw says what happened, the projection says what is playable.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import core

NAMES = "C C# D D# E F F# G G# A A# B".split()
MAJSET = (0, 2, 4, 5, 7, 9, 11)
#: Onset gap treated as a boundary. Long enough to survive a held fermata,
#: short enough to catch hands lifting between pieces.
GAP_S = 4.0


def mmss(s: float) -> str:
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def parse_time(s: str, end: float) -> float:
    if s in ("end", "-"):
        return end
    if s in ("start", "0"):
        return 0.0
    if ":" in s:
        m, sec = s.split(":")
        return int(m) * 60 + float(sec)
    return float(s)


def load(take: str):
    """Notes from the materialised index, as (t_seconds, note, vel, dur)."""
    from .pipeline.cli import resolve
    path = resolve(take)
    db = path.parent / ".index" / (path.name + ".sqlite3")
    if not db.exists():
        raise SystemExit(f"no index for {path.name} — run: "
                         f"python -m fp30x_studio.pipeline ingest {take}")
    rows = sqlite3.connect(db).execute(
        "SELECT ns_on, note, velocity_on, ns_off FROM interval ORDER BY ns_on"
    ).fetchall()
    if not rows:
        raise SystemExit(f"{path.name} has no notes")
    t0 = rows[0][0]
    ev = [((ns - t0) / 1e9, n, v, ((off - ns) / 1e9 if off else 0.05))
          for ns, n, v, off in rows]
    return path, t0, ev


def diatonic(window) -> tuple[str, float]:
    """Best-fitting major key signature and the weight that falls inside it."""
    p = [0.0] * 12
    for _, n, _v, d in window:
        p[n % 12] += max(d, 0.05)
    total = sum(p) or 1.0
    best = max(range(12), key=lambda r: sum(p[(r + i) % 12] for i in MAJSET))
    return NAMES[best], sum(p[(best + i) % 12] for i in MAJSET) / total


def texture(window) -> dict:
    ons = [e[0] for e in window]
    gaps = [b - a for a, b in zip(ons, ons[1:])]
    pit = [e[1] for e in window]
    grams = [tuple(pit[i:i + 4]) for i in range(len(pit) - 3)]
    span = (ons[-1] - ons[0]) or 1.0
    key, inset = diatonic(window)
    return {
        "density": len(window) / span,
        # A stop is hands lifting mid-passage: the signature of
        # practising rather than playing through.
        "stops": sum(1 for g in gaps if g > 1.2),
        "range": max(pit) - min(pit),
        "vel": sum(e[2] for e in window) / len(window),
        "rep": (1 - len(set(grams)) / len(grams)) if grams else 0.0,
        "key": key,
        "inset": inset,
    }


def split(ev, gap: float = GAP_S) -> list[list]:
    segs, cur = [], [ev[0]]
    for prev, e in zip(ev, ev[1:]):
        if e[0] - prev[0] > gap:
            segs.append(cur)
            cur = [e]
        else:
            cur.append(e)
    segs.append(cur)
    return segs


def cmd_list(args) -> int:
    _path, _t0, ev = load(args.take)
    segs = split(ev, args.gap)
    print(f"{len(ev)} notes, {mmss(ev[-1][0])} long, gap>{args.gap}s "
          f"-> {len(segs)} segments\n")
    print(f"{'#':>3} {'start':>6} {'end':>6} {'dur':>6} {'notes':>6} "
          f"{'n/s':>4} {'stops':>5} {'rng':>4} {'vel':>4} {'rep%':>5} "
          f"{'key sig':>7} {'in%':>4}")
    for i, s in enumerate(segs):
        t = texture(s)
        print(f"{i:>3} {mmss(s[0][0]):>6} {mmss(s[-1][0]):>6} "
              f"{s[-1][0] - s[0][0]:6.1f} {len(s):>6} {t['density']:4.1f} "
              f"{t['stops']:>5} {t['range']:>4} {t['vel']:4.0f} "
              f"{t['rep'] * 100:5.0f} {t['key'] + ' maj':>7} "
              f"{t['inset'] * 100:4.0f}")
    return 0


def cmd_clip(args) -> int:
    path, t0, ev = load(args.take)
    end_t = ev[-1][0]
    if args.segment is not None:
        segs = split(ev, args.gap)
        if not 0 <= args.segment < len(segs):
            raise SystemExit(f"segment {args.segment} out of range (0..{len(segs)-1})")
        s = segs[args.segment]
        t_from, t_to = s[0][0], s[-1][0] + max(s[-1][3], 0.5)
    else:
        t_from = parse_time(args.start, end_t)
        t_to = parse_time(args.end, end_t)
    if t_to <= t_from:
        raise SystemExit(f"empty clip: {mmss(t_from)}..{mmss(t_to)}")

    pad = args.pad
    ns_from = t0 + int((t_from - pad) * 1e9)
    ns_to = t0 + int((t_to + pad) * 1e9)

    out_dir = Path(args.out).expanduser() if args.out else path.parent / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or f"{path.stem}-{int(t_from)}s"
    raw_out = out_dir / f"{stem}.fp30"
    mid_out = out_dir / f"{stem}.mid"
    wav_out = out_dir / f"{stem}.wav"

    # --- the raw slice: header verbatim, absolute timestamps untouched ---
    kept = 0
    with open(path) as src, open(raw_out, "w") as dst:
        for line in src:
            if line.startswith("#"):
                dst.write(line)
                continue
            ns = int(line.split(None, 1)[0])
            if ns_from <= ns <= ns_to:
                dst.write(line)
                kept += 1
        dst.write(f"# clipped_from {path.name}\n")
        dst.write(f"# clip_window_ns {ns_from} {ns_to}\n")
        dst.write(f"# clip_window_rel {t_from:.3f} {t_to:.3f}\n")
        dst.write("# end clip\n")

    # --- the playable projection ---
    import mido
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    tpb, tempo = mid.ticks_per_beat, 500000
    held: set[int] = set()
    pedal_down = False
    prev = None
    with open(path) as src:
        for line in src:
            if line.startswith("#"):
                continue
            parts = line.split()
            ns = int(parts[0])
            if not (ns_from <= ns <= ns_to):
                continue
            data = bytes(int(b, 16) for b in parts[1:])
            if len(data) < 2:
                continue
            status, d1 = data[0] & 0xF0, data[1]
            d2 = data[2] if len(data) > 2 else 0
            if prev is None:
                prev = ns
            delta = int(mido.second2tick((ns - prev) / 1e9, tpb, tempo))
            prev = ns
            if status == 0x90 and d2 > 0:
                held.add(d1)
                track.append(mido.Message("note_on", note=d1, velocity=d2, time=delta))
            elif status in (0x80, 0x90):
                held.discard(d1)
                track.append(mido.Message("note_off", note=d1, velocity=d2, time=delta))
            elif status == 0xB0:
                if d1 == 64:
                    pedal_down = d2 >= 64
                track.append(mido.Message("control_change", control=d1,
                                          value=d2, time=delta))
    # The cut is arbitrary; the sound should not be. Release what is still down.
    for n in sorted(held):
        track.append(mido.Message("note_off", note=n, velocity=64, time=0))
    if pedal_down:
        track.append(mido.Message("control_change", control=64, value=0, time=0))
    mid.save(mid_out)

    core.render(mid_out, wav_out, core.soundfont_path())

    dur = t_to - t_from
    print(f"{stem}  {mmss(t_from)}–{mmss(t_to)}  ({dur:.1f}s, {kept} packets, "
          f"{len(mid.tracks[0])} messages)")
    for p in (raw_out, mid_out, wav_out):
        print(f"  {p}")
    if not args.no_play:
        subprocess.run(["afplay", str(wav_out)])
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m fp30x_studio.segment",
                                 description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="split a take on silences and characterise each piece")
    sp.add_argument("take")
    sp.add_argument("--gap", type=float, default=GAP_S)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("clip", help="cut a clip out of a take")
    sp.add_argument("take")
    sp.add_argument("--from", dest="start", default="start", help="MM:SS, seconds, or 'start'")
    sp.add_argument("--to", dest="end", default="end", help="MM:SS, seconds, or 'end'")
    sp.add_argument("--segment", type=int, help="clip segment N from 'list' instead")
    sp.add_argument("--gap", type=float, default=GAP_S)
    sp.add_argument("--name", help="output stem (default: <take>-<start>s)")
    sp.add_argument("--out", help="output directory (default: <takes>/clips)")
    sp.add_argument("--pad", type=float, default=0.5, help="seconds of padding each side")
    sp.add_argument("--no-play", action="store_true")
    sp.set_defaults(func=cmd_clip)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
