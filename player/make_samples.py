"""Render the player's piano samples from MuseScore_General.sf3 (MIT-licensed),
the same grand piano `fpn render` uses, so the player and the render agree.

One sample every minor third from A0 to C8, at two velocities. The player
pitch-shifts to the nearest sample (at most 1.5 semitones) and picks the layer
by velocity. Output is player/samples/, gitignored; rerun to rebuild.

    .venv/bin/python player/make_samples.py
"""
import subprocess, tempfile
from pathlib import Path
import mido

HERE = Path(__file__).resolve().parent
OUT = HERE / "samples"
SF = Path.home() / "Music/FP-30X Studio/MuseScore_General.sf3"
LAYERS = {"p": 45, "f": 100}

OUT.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    for p in range(21, 109, 3):
        for name, vel in LAYERS.items():
            mf = mido.MidiFile(ticks_per_beat=960); tr = mido.MidiTrack(); mf.tracks.append(tr)
            tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000))
            tr.append(mido.Message("note_on", note=p, velocity=vel, time=0))
            tr.append(mido.Message("note_off", note=p, velocity=0, time=960 * 7))
            mf.save(td / "n.mid")
            subprocess.run(["fluidsynth", "-ni", "-g", "0.6", "-r", "44100", "-F",
                            str(td / "n.wav"), str(SF), str(td / "n.mid")],
                           check=True, capture_output=True)
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i",
                            str(td / "n.wav"), "-t", "7", "-af", "afade=t=out:st=6:d=1",
                            "-b:a", "128k", str(OUT / f"{p}{name}.mp3")], check=True)
print(f"{len(list(OUT.glob('*.mp3')))} samples in {OUT}")
