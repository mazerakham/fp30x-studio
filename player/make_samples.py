"""Render the players' samples with fluidsynth, so a page and a render agree.

Piano (default): MuseScore_General.sf3 (MIT-licensed), the grand piano
`fpn render` uses. One sample every minor third from A0 to C8, at two
velocities (p 45, f 100), 7 s each. Output player/samples/.

Rhodes (--rhodes): jRhodes3c-stereo.sf2, the soundfont every Rhodes render here
uses (fp30x_studio.slicekit). Same pitches, three velocities (p 40, m 80,
f 115), since the Rhodes bark changes with how hard the key is hit. All of them
are rendered at ONE gain, found the way slicekit probes: render at -g 0.1, then
render again at the gain that puts the loudest sample at PROBE_TARGET. One gain keeps the layers'
loudness relative to each other. Output player/samples/rhodes/. Compose's audio
export plays these.

A page pitch-shifts to the nearest sample (at most 1.5 semitones) and picks the
layer by velocity. player/samples/ is gitignored; rerun to rebuild.

    .venv/bin/python player/make_samples.py
    .venv/bin/python player/make_samples.py --rhodes
"""
import subprocess, sys, tempfile, wave
from pathlib import Path
import mido
import numpy as np

HERE = Path(__file__).resolve().parent
STUDIO = Path.home() / "Music/FP-30X Studio"
PIANO = dict(sf=STUDIO / "MuseScore_General.sf3", out=HERE / "samples",
             layers={"p": 45, "f": 100}, secs=7, gain=0.6)
RHODES = dict(sf=STUDIO / "soundfonts/jRhodes3/jRhodes3c-stereo.sf2", out=HERE / "samples/rhodes",
              layers={"p": 40, "m": 80, "f": 115}, secs=7, gain=None)
PROBE_TARGET = 0.668  # -3.5 dBFS sample peak, as in fp30x_studio.slicekit


def note_midi(path, p, vel, secs):
    mf = mido.MidiFile(ticks_per_beat=960); tr = mido.MidiTrack(); mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000))
    tr.append(mido.Message("note_on", note=p, velocity=vel, time=0))
    tr.append(mido.Message("note_off", note=p, velocity=0, time=int(960 * (secs - 1))))
    mf.save(path)


def synth(sf, mid, wav, gain):
    subprocess.run(["fluidsynth", "-ni", "-g", f"{gain:.4f}", "-r", "44100", "-F", str(wav), str(sf), str(mid)],
                   check=True, capture_output=True)


def peak(wav):
    with wave.open(str(wav)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return float(np.abs(a).max()) / 32768 if a.size else 0.0


def encode(wav, mp3, secs):
    af = f"afade=t=out:st={secs - 1}:d=1"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(wav), "-t", str(secs),
                    "-af", af, "-b:a", "128k", str(mp3)], check=True)


def build(cfg):
    out = cfg["out"]; out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td); wavs = {}
        for p in range(21, 109, 3):
            for name, vel in cfg["layers"].items():
                mid, wav = td / f"{p}{name}.mid", td / f"{p}{name}.wav"
                note_midi(mid, p, vel, cfg["secs"])
                synth(cfg["sf"], mid, wav, cfg["gain"] if cfg["gain"] else 0.1)
                wavs[f"{p}{name}"] = wav
        if cfg["gain"] is None:  # one gain for every sample, from the loudest probe, then render again at it
            top = max(peak(w) for w in wavs.values())
            g = 0.1 * PROBE_TARGET / top
            print(f"probe peak {top:.3f} at -g 0.1: rendering at -g {g:.3f}")
            for key, wav in wavs.items():
                synth(cfg["sf"], td / f"{key}.mid", wav, g)
        silent = 0
        for key, wav in wavs.items():
            if peak(wav) < 1e-4:
                silent += 1; continue  # outside the instrument's range; the page falls back to a neighbour
            encode(wav, out / f"{key}.mp3", cfg["secs"])
    print(f"{len(list(out.glob('*.mp3')))} samples in {out} ({silent} silent, skipped)")


if __name__ == "__main__":
    build(RHODES if "--rhodes" in sys.argv else PIANO)
