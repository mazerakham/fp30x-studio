r"""Render the scene with its own sound. ``python -m fp30x_studio.viz.render``

Three stages, and the only interesting thing about them is that the audio and
the video are generated from the *same* take rows rather than being aligned
after the fact.

1. The window's note-ons, note-offs and CC64 are written out as a MIDI file and
   played through the same soundfont the rest of the studio uses. The visual
   clock and this file are both functions of ``store.seconds(ns_on)``, so
   nothing has to be nudged into sync -- there is no offset to get wrong.
2. Manim is driven through its Python API, not its command line. Loading
   ``scene.py`` as a file makes its package-relative imports fail, and copying
   the scene out to a standalone script to work around that is how two copies of
   a scene start to drift apart.
3. ffmpeg lays the audio under the video with ``+faststart``. A render without
   it plays fine locally and then stalls for everyone it is sent to, so the flag
   is not optional here and the final file is probed to prove it is set.

The silence at the head is real silence: the audio is delayed by exactly
:data:`~.scene.MUSIC_START` seconds, so the title sequence plays against
nothing, and both ends carry a fade.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import perf
from . import scene as _scene
from .scene import MUSIC_START, TAKE, TAKE_T0, TAKE_T1, Nocturne

TICKS_PER_BEAT = 480
TEMPO_US = 500_000                       # 960 ticks per second
FADE_IN_S = 0.30
FADE_OUT_S = 0.85
PEAK_DBFS = -1.5                         # headroom the master keeps
DEFAULT_OUT = Path.home() / "Music/FP-30X Studio/renders"

QUALITIES = {"l": "low_quality", "m": "medium_quality", "h": "high_quality",
             "p": "production_quality", "k": "fourk_quality"}


def _sec_to_tick(s: float) -> int:
    return int(round(s * TICKS_PER_BEAT * 1e6 / TEMPO_US))


def write_midi(take: Path, t0: float, t1: float, out: Path) -> Path:
    """The window as a MIDI file, pedal included.

    The damper's *state on entry* is written at tick 0. Without it the window
    opens with the dampers on the strings whatever he was actually doing, and
    the first second sounds dry in a way the take is not.
    """
    import mido

    notes, pedal = perf.load_window(take, t0, t1)
    ev: list[tuple[int, int, mido.Message]] = []
    for t, v in pedal:
        ev.append((_sec_to_tick(t - t0), 0, mido.Message("control_change", control=64, value=v)))
    for n in notes:
        ev.append((_sec_to_tick(n.t - t0), 1,
                   mido.Message("note_on", note=n.pitch, velocity=n.velocity)))
        ev.append((_sec_to_tick(n.t_off - t0), 2,
                   mido.Message("note_off", note=n.pitch, velocity=0)))
    # Order key: pedal before note-on before note-off at the same tick, so a
    # note re-struck at the instant of its own release is not killed by it.
    ev.sort(key=lambda r: (r[0], r[1]))

    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=TEMPO_US, time=0))
    track.append(mido.Message("program_change", program=0, time=0))
    prev = 0
    for tick, _rank, msg in ev:
        msg.time = tick - prev
        prev = tick
        track.append(msg)
    mid.save(out)
    return out


def _peak_dbfs(path: Path) -> float:
    p = subprocess.run(["ffmpeg", "-i", str(path), "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True)
    for line in p.stderr.splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].strip().split()[0])
    return 0.0


def render_audio(take: Path, t0: float, t1: float, work: Path,
                 soundfont: Path) -> Path:
    """MIDI -> soundfont -> a ten-second track with the head silent."""
    mid = write_midi(take, t0, t1, work / "window.mid")
    raw = work / "raw.wav"
    subprocess.run(["fluidsynth", "-ni", "-F", str(raw), "-r", "48000",
                    "-g", "1.0", str(soundfont), str(mid)], check=True,
                   capture_output=True)
    gain = PEAK_DBFS - _peak_dbfs(raw)
    out = work / "audio.wav"
    delay = int(MUSIC_START * 1000)
    # Read through the module, never `from .scene import RUN_TIME`: configure()
    # recomputes it from the window, and a name bound at import time would keep
    # the old value and silently truncate or pad the audio.
    run_time = _scene.RUN_TIME
    fade_out_at = run_time - FADE_OUT_S
    subprocess.run([
        "ffmpeg", "-y", "-i", str(raw), "-af",
        f"adelay={delay}|{delay},volume={gain:.2f}dB,"
        f"afade=t=in:st={MUSIC_START}:d={FADE_IN_S},"
        f"afade=t=out:st={fade_out_at}:d={FADE_OUT_S},apad",
        "-t", str(run_time), "-ar", "48000", "-ac", "2", str(out),
    ], check=True, capture_output=True)
    return out


def render_video(work: Path, quality: str) -> Path:
    from manim import config, tempconfig

    with tempconfig({
        "quality": QUALITIES[quality],
        "media_dir": str(work / "media"),
        "output_file": "nocturne_silent",
        "disable_caching": True,
        "flush_cache": True,
        "background_color": "#000000",
    }):
        Nocturne().render()
        return Path(config.output_file if Path(str(config.output_file)).is_absolute()
                    else str(config.get_dir("video_dir") / "nocturne_silent.mp4"))


def probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                        "-of", "json", str(path)], capture_output=True, text=True,
                       check=True)
    return json.loads(p.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--take", type=Path, default=TAKE)
    ap.add_argument("--window", nargs=2, type=float, default=[TAKE_T0, TAKE_T1],
                    metavar=("T0", "T1"))
    ap.add_argument("--quality", choices=sorted(QUALITIES), default="h")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--key", default=None,
                    help="key signature to engrave and spell against, e.g. B or Ab")
    args = ap.parse_args(argv)

    from .. import core
    soundfont = core.soundfont_path()
    if not soundfont.exists():
        raise SystemExit(f"soundfont missing: {soundfont}")

    cfg = _scene.configure(args.take, args.window[0], args.window[1],
                           title=args.title, subtitle=args.subtitle,
                           key=args.key)
    print(f"  take   {cfg['take']}\n  window {cfg['t0']:.2f}-{cfg['t1']:.2f} s"
          f"  ->  {cfg['run_time']:.2f} s of video\n  key    {cfg['key']}")

    work = Path(tempfile.mkdtemp(prefix="fpx-viz-"))
    audio = render_audio(args.take, args.window[0], args.window[1], work, soundfont)
    silent = render_video(work, args.quality)

    out = args.out
    if out is None:
        DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        stem = Path(args.take).stem
        out = DEFAULT_OUT / (f"{stem}-{args.window[0]:.0f}-{args.window[1]:.0f}s.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(silent), "-i", str(audio),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(out),
    ], check=True, capture_output=True)
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)

    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    print(f"{out}\n  {v['width']}x{v['height']} {v['r_frame_rate']} fps "
          f"{v['codec_name']} / {a['codec_name']} {a['sample_rate']} Hz\n"
          f"  duration {float(info['format']['duration']):.3f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
