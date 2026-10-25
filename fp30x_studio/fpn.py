"""FPN: an editable note sketch, and the commands around it.

A .fpn file is JSON. Its `notes` and `pedal` are what the keyboard sent and are
never rewritten; every change is an entry appended to `edits`, applied in order
on load. So nothing played is ever lost, any edit can be undone, and the file
itself is the record of how a sketch got from the take to what it is now.

    {"format": "fpn", "version": 1, "title": "...",
     "source": {"file": "...", "made": "2026-10-25T23:30:00Z"},
     "notes": [[id, onset_s, dur_s, midi, velocity], ...],
     "pedal": [[t_s, cc64_value], ...],
     "edits": [{"op": "delete", "ids": [3, 4]},
               {"op": "speed", "a": 105.0, "b": 165.0, "k": 3.0}]}

`speed` plays [a, b) k times faster and pulls everything after it earlier; its
a and b are in the timeline as it stands after the edits before it. The player
(player/fpn-player.html) and this module implement the same two ops and must
agree; tests/test_fpn.py pins this side.

    python -m fp30x_studio.fpn new <clip-slug | file.mid | file.fp30> [--title T]
    python -m fp30x_studio.fpn render <file.fpn>        # grand piano + hall, opens it
    python -m fp30x_studio.fpn open <file.fpn>          # the player, in Chrome
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

STUDIO = Path.home() / "Music" / "FP-30X Studio"
CLIPS = STUDIO / "takes" / "clips"
SKETCHES = STUDIO / "sketches"
SOUNDFONT = STUDIO / "MuseScore_General.sf3"
PLAYER = Path(__file__).resolve().parent.parent / "player"
SERVED = Path.home() / ".claude" / "served"


# ---------------------------------------------------------------- reading

def _pair(events):
    """(t, kind, a, b) events -> notes [[id,t,d,p,v]] and pedal [[t,v]]."""
    held, notes, pedal = {}, [], []
    for t, kind, a, b in sorted(events, key=lambda e: e[0]):
        if kind == "on" and b > 0:
            if a in held:                       # re-strike before release
                t0, v = held.pop(a)
                notes.append([t0, t - t0, a, v])
            held[a] = (t, b)
        elif kind in ("on", "off") and a in held:
            t0, v = held.pop(a)
            notes.append([t0, t - t0, a, v])
        elif kind == "cc" and a == 64:
            pedal.append([round(t, 4), b])
    end = max([n[0] + n[1] for n in notes] + [0.0])
    for p, (t0, v) in held.items():
        notes.append([t0, max(end - t0, 0.05), p, v])
    if not notes:
        raise SystemExit("no notes in that file")
    t0 = min(min(n[0] for n in notes), min([p[0] for p in pedal] + [1e18]))
    notes.sort(key=lambda n: (n[0], n[2]))
    out = [[i, round(t - t0, 4), round(max(d, 0.01), 4), p, v]
           for i, (t, d, p, v) in enumerate(notes)]
    return out, [[round(t - t0, 4), v] for t, v in pedal]


def read_mid(path: Path):
    import mido
    t, ev = 0.0, []
    for m in mido.MidiFile(path):           # yields delta seconds, tempo applied
        t += m.time
        if m.type == "note_on":
            ev.append((t, "on", m.note, m.velocity))
        elif m.type == "note_off":
            ev.append((t, "off", m.note, 0))
        elif m.type == "control_change":
            ev.append((t, "cc", m.control, m.value))
    return _pair(ev)


def read_fp30(path: Path):
    """The capture's text format: `abs_ns HEX HEX ...`, possibly several
    messages per line, running status allowed."""
    ev = []
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        f = line.split()
        t = int(f[0]) / 1e9
        b = [int(x, 16) for x in f[1:]]
        i, status = 0, None
        while i < len(b):
            if b[i] & 0x80:
                status = b[i]; i += 1
            if status is None or i >= len(b):
                break
            hi = status & 0xF0
            n = 1 if hi in (0xC0, 0xD0) else 2
            d = b[i:i + n]; i += n
            if len(d) < n:
                break
            if hi == 0x90:
                ev.append((t, "on", d[0], d[1]))
            elif hi == 0x80:
                ev.append((t, "off", d[0], 0))
            elif hi == 0xB0:
                ev.append((t, "cc", d[0], d[1]))
    return _pair(ev)


def resolve_source(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.exists():
        return p
    for ext in (".mid", ".fp30"):
        c = CLIPS / f"{arg}{ext}"
        if c.exists():
            return c
    raise SystemExit(f"not a file or a clip slug: {arg}")


def clip_title(slug: str) -> str | None:
    try:
        lib = json.loads((CLIPS / "library.json").read_text())
    except (OSError, ValueError):
        return None
    for c in lib if isinstance(lib, list) else lib.get("clips", []):
        if c.get("slug") == slug:
            return c.get("title")
    return None


# ---------------------------------------------------------------- edits

def warp(t: float, a: float, b: float, k: float) -> float:
    if t < a:
        return t
    if t < b:
        return a + (t - a) / k
    return a + (b - a) / k + (t - b)


def apply(doc: dict):
    """The sketch as it stands: (notes, pedal) after every edit, in order."""
    notes = [list(n) for n in doc["notes"]]
    pedal = [list(p) for p in doc.get("pedal", [])]
    for e in doc.get("edits", []):
        if e["op"] == "delete":
            gone = set(e["ids"])
            notes = [n for n in notes if n[0] not in gone]
        elif e["op"] == "speed":
            a, b, k = e["a"], e["b"], e["k"]
            for n in notes:
                on, off = warp(n[1], a, b, k), warp(n[1] + n[2], a, b, k)
                n[1], n[2] = on, max(off - on, 0.01)
            for p in pedal:
                p[0] = warp(p[0], a, b, k)
        else:
            raise ValueError(f"unknown edit op {e['op']!r}")
    return notes, pedal


def to_midi(notes, pedal, out: Path):
    import mido
    ev = []
    for _, t, d, p, v in notes:
        ev.append((t, 1, mido.Message("note_on", note=p, velocity=v)))
        ev.append((t + d, 0, mido.Message("note_off", note=p, velocity=0)))
    for t, v in pedal:
        ev.append((t, 2, mido.Message("control_change", control=64, value=v)))
    ev.sort(key=lambda e: (e[0], e[1]))
    mf = mido.MidiFile(ticks_per_beat=960)
    tr = mido.MidiTrack(); mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000))   # 960 ticks = 1 s
    last = 0
    for t, _, m in ev:
        tick = max(round(t * 960), last)
        tr.append(m.copy(time=tick - last)); last = tick
    mf.save(out)


# ---------------------------------------------------------------- commands

def publish_player() -> str:
    """Link the player directory into the one static server's root."""
    link = SERVED / "fpn"
    if not link.is_symlink() or link.resolve() != PLAYER:
        link.unlink(missing_ok=True)
        link.symlink_to(PLAYER)
    subprocess.run([str(Path.home() / ".claude/bin/show"), "--url",
                    str(PLAYER / "fpn-player.html")], check=True,
                   stdout=subprocess.DEVNULL)
    return "http://127.0.0.1:8777/fpn/fpn-player.html"


def open_in_player(fpn: Path):
    """Sketches are served from their own directory, linked beside the player."""
    link = SERVED / "fpn-sketches"
    if not link.is_symlink() or link.resolve() != fpn.parent.resolve():
        link.unlink(missing_ok=True)
        link.symlink_to(fpn.parent.resolve())
    url = publish_player() + "?f=/fpn-sketches/" + fpn.name
    subprocess.run(["open", "-a", "Google Chrome", url], check=False)
    return url


def cmd_new(args) -> int:
    src = resolve_source(args.source)
    notes, pedal = read_fp30(src) if src.suffix == ".fp30" else read_mid(src)
    title = args.title or clip_title(src.stem) or src.stem
    SKETCHES.mkdir(parents=True, exist_ok=True)
    out = SKETCHES / f"{src.stem}.fpn"
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists; it may hold your edits. --force to replace it.")
    doc = {"format": "fpn", "version": 1, "title": title,
           "source": {"file": str(src), "made": dt.datetime.now(dt.timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%SZ")},
           "notes": notes, "pedal": pedal, "edits": []}
    out.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"{out}  ({len(notes)} notes, {len(pedal)} pedal events)")
    if not args.no_open:
        open_in_player(out)
    return 0


def cmd_open(args) -> int:
    print(open_in_player(Path(args.fpn).expanduser().resolve()))
    return 0


def cmd_render(args) -> int:
    fpn = Path(args.fpn).expanduser().resolve()
    doc = json.loads(fpn.read_text())
    notes, pedal = apply(doc)
    n_edits = len(doc.get("edits", []))
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        mid = fpn.with_suffix(f".e{n_edits}.mid")
        to_midi(notes, pedal, mid)
        fs = lambda g, o: subprocess.run(
            ["fluidsynth", "-ni", "-g", str(g), "-r", "44100", "-F", str(o),
             str(SOUNDFONT), str(mid)], check=True, capture_output=True)
        fs(0.1, td / "probe.wav")
        peak = _peak_db(td / "probe.wav")
        gain = round(min(5.0, 0.1 * 10 ** ((-3 - peak) / 20)), 3)
        fs(gain, td / "dry.wav")
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i",
                        str(td / "dry.wav"), "-af", "alimiter=limit=0.89",
                        "-c:a", "pcm_s16le", str(td / "dry16.wav")], check=True)
        subprocess.run([sys.executable, "-m", "fp30x_studio.reverb",
                        str(td / "dry16.wav"), str(td / "hall.wav"),
                        "--wet", "0.28", "--rt60", "2.1"], check=True,
                       capture_output=True, cwd=Path(__file__).resolve().parent.parent)
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(td / "hall.wav")],
            check=True, capture_output=True, text=True).stdout)
        out = fpn.with_name(f"{fpn.stem}.e{n_edits}-grand-hall-FULL-"
                            f"{int(dur // 60)}m{int(dur % 60):02d}s.m4a")
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i",
                        str(td / "hall.wav"), "-af", "loudnorm=I=-18:TP=-1.5",
                        "-c:a", "aac", "-b:a", "192k", str(out)], check=True)
    print(out)
    if not args.no_open:
        subprocess.run(["open", "-a", "Google Chrome", str(out)], check=False)
    return 0


def _peak_db(wav: Path) -> float:
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", str(wav), "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].split()[0])
    raise RuntimeError("no peak reading")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fp30x_studio.fpn",
                                 description=__doc__.split("\n\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    n = sp.add_parser("new", help="make a sketch from a clip, .mid or .fp30")
    n.add_argument("source"); n.add_argument("--title")
    n.add_argument("--force", action="store_true"); n.add_argument("--no-open", action="store_true")
    n.set_defaults(fn=cmd_new)
    o = sp.add_parser("open", help="open a sketch in the player")
    o.add_argument("fpn"); o.set_defaults(fn=cmd_open)
    r = sp.add_parser("render", help="render the edited sketch: grand piano, hall")
    r.add_argument("fpn"); r.add_argument("--no-open", action="store_true")
    r.set_defaults(fn=cmd_render)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
