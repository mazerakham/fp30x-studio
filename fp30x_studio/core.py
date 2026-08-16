"""Capture, render and playback for the Roland FP-30X.

No GUI in here. The app layer talks to this through a queue.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import mido

DEVICE_MATCH = "FP-30X"
SOUNDFONT_URL = (
    "https://ftp.osuosl.org/pub/musescore/soundfont/"
    "MuseScore_General/MuseScore_General.sf3"
)

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    return f"{NOTES[n % 12]}{n // 12 - 1}"


def data_dir() -> Path:
    d = Path(os.environ.get("FP30X_HOME", Path.home() / "Music" / "FP-30X Studio"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def takes_dir() -> Path:
    d = data_dir() / "takes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def soundfont_path() -> Path:
    return data_dir() / "MuseScore_General.sf3"


def have_fluidsynth() -> bool:
    return shutil.which("fluidsynth") is not None


def find_port() -> str | None:
    """The CoreMIDI input port for the piano, if macOS can see it."""
    try:
        return next((p for p in mido.get_input_names() if DEVICE_MATCH in p), None)
    except Exception:
        return None


def ensure_soundfont(progress=None) -> Path:
    """Download the instrument set on first run. ~38 MB, MIT licensed."""
    sf = soundfont_path()
    if sf.exists() and sf.stat().st_size > 1_000_000:
        return sf
    tmp = sf.with_suffix(".part")
    with urllib.request.urlopen(SOUNDFONT_URL) as r:
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        with open(tmp, "wb") as f:
            while chunk := r.read(262_144):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    tmp.replace(sf)
    return sf


class Capture(threading.Thread):
    """Reads MIDI off the piano until stopped, collecting timed messages."""

    def __init__(self, port_name: str, events: queue.Queue):
        super().__init__(daemon=True)
        self.port_name = port_name
        self.events = events
        self._stop = threading.Event()
        self.messages: list[tuple[float, mido.Message]] = []
        self.error: str | None = None
        self.started_at: float | None = None

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            with mido.open_input(self.port_name) as inp:
                self.started_at = time.time()
                self.events.put(("started", None))
                while not self._stop.is_set():
                    for msg in inp.iter_pending():
                        now = time.time()
                        self.messages.append((now - self.started_at, msg))
                        if msg.type == "note_on" and msg.velocity > 0:
                            self.events.put(("note", (msg.note, msg.velocity)))
                        elif msg.type == "control_change" and msg.control == 64:
                            self.events.put(("pedal", msg.value >= 64))
                    time.sleep(0.002)
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.error = str(exc)
            self.events.put(("error", self.error))
        finally:
            self.events.put(("stopped", None))

    @property
    def note_count(self) -> int:
        return sum(1 for _, m in self.messages
                   if m.type == "note_on" and m.velocity > 0)

    def save_midi(self, path: Path, bpm: int = 120) -> Path | None:
        if not self.messages:
            return None
        mid = mido.MidiFile()
        track = mido.MidiTrack()
        mid.tracks.append(track)
        tempo = mido.bpm2tempo(bpm)
        prev = 0.0
        for t, msg in self.messages:
            delta = mido.second2tick(t - prev, mid.ticks_per_beat, tempo)
            track.append(msg.copy(time=int(delta)))
            prev = t
        mid.save(path)
        return path


def render(midi_path: Path, wav_path: Path, soundfont: Path, rate: int = 44100) -> Path:
    """MIDI file -> WAV, using fluidsynth and the downloaded instrument set."""
    subprocess.run(
        ["fluidsynth", "-ni", "-F", str(wav_path), "-r", str(rate),
         str(soundfont), str(midi_path)],
        check=True, capture_output=True,
    )
    return wav_path


class Player:
    """Wraps afplay so the UI can start and stop playback."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None

    def play(self, wav: Path, on_done=None):
        self.stop()
        self.proc = subprocess.Popen(["afplay", str(wav)])
        if on_done:
            def wait():
                p = self.proc
                if p:
                    p.wait()
                    if self.proc is p:
                        on_done()
            threading.Thread(target=wait, daemon=True).start()

    @property
    def playing(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.playing and self.proc:
            self.proc.terminate()
        self.proc = None


def list_takes() -> list[Path]:
    return sorted(takes_dir().glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
