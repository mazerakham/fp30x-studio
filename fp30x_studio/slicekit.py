r"""Render an arbitrary window of a performance, with the pedal chased into it.

The studio already cuts *segments* -- stretches separated by long silences. A
session page needs something finer and more arbitrary: four seconds around a
fumbled note, one occurrence of a recurring figure, a phrase to audition
against another. Those windows do not respect segment boundaries and there may
be a hundred of them, so this is a function rather than a CLI.

**Chasing the pedal is the whole difficulty.** CC64 is held across bar lines
for tens of seconds. A window that opens mid-pedal and is rendered from its own
CC64 events alone starts dry, and the difference is audible immediately -- it
does not sound like an excerpt, it sounds like a different instrument. So a
pedal span that began before ``t0`` is clamped to ``t0`` and still applied.
This is what a DAW calls *Chase Events*.

Windows also keep any note already sounding at ``t0`` (clamped the same way):
an excerpt that opens inside a held chord should open with that chord, not with
whatever attacks next.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from .performance import Performance, Strike, synthesize_midi

SOUNDFONT = Path("~/Music/FP-30X Studio/soundfonts/jRhodes3/jRhodes3c-stereo.sf2"
                 ).expanduser()
# Silence appended so a pedalled ending can decay instead of being guillotined.
TAIL = 1.6


def window(perf: Performance, t0: float, t1: float, *, tail: float = TAIL
           ) -> tuple[list[Strike], list[tuple[float, float]]]:
    """Strikes and pedal spans of ``[t0, t1]``, clamped and rebased to zero."""
    strikes: list[Strike] = []
    for s in perf.strikes():
        if s.t_off < t0 or s.t_on > t1:
            continue
        a, b = max(s.t_on, t0), min(s.t_off, t1 + tail)
        strikes.append(Strike(a - t0, max(b - t0, a - t0), s.key, s.velocity))
    pedal = [(max(a, t0) - t0, min(b, t1 + tail) - t0)
             for a, b in perf.pedal if b >= t0 and a <= t1]
    return strikes, pedal


def _gain_for(midi: Path) -> float:
    """Probe at 0.1 and scale to -1 dBFS.

    Probing at 1.0 clips on the electric piano, and a clipped probe reports the
    clipping point rather than the signal, so the correction is wrong in an
    unknown direction.
    """
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        probe = Path(fh.name)
    try:
        subprocess.run(["fluidsynth", "-ni", "-g", "0.1", "-F", str(probe),
                        "-r", "48000", str(SOUNDFONT), str(midi)],
                       capture_output=True, check=False)
        raw = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(probe),
             "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
            capture_output=True, check=False).stdout
        if not raw:
            return 0.8
        d = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768
        pk = float(abs(d).max())
        return min(10.0, 0.1 * (0.89 / pk)) if pk > 1e-6 else 1.0
    finally:
        probe.unlink(missing_ok=True)


def render(strikes: Sequence[Strike], pedal: Sequence[tuple[float, float]],
           out: Path, *, gain: float | None = None) -> Path | None:
    """Strikes -> a faststart ``.m4a`` through the Rhodes soundfont.

    ``+faststart`` or the moov atom lands at the end of the file and a browser
    must fetch the whole thing before it can play or seek.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        mid, wav = Path(td) / "w.mid", Path(td) / "w.wav"
        synthesize_midi(strikes, pedal, mid)
        g = _gain_for(mid) if gain is None else gain
        subprocess.run(["fluidsynth", "-ni", "-g", f"{g:.3f}", "-F", str(wav),
                        "-r", "48000", str(SOUNDFONT), str(mid)],
                       capture_output=True, check=False)
        if not wav.exists() or wav.stat().st_size == 0:
            return None
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(wav),
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        str(out), "-y"], capture_output=True, check=False)
    return out if out.exists() else None


def clip(perf: Performance, t0: float, t1: float, out: Path, **kw) -> Path | None:
    """Convenience: :func:`window` then :func:`render`."""
    strikes, pedal = window(perf, t0, t1)
    return render(strikes, pedal, out, **kw) if strikes else None
