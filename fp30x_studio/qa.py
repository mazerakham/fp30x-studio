"""Quality gate for a finished render.

Every check here exists because something got past the pipeline looking fine.

  sample_rate   loudnorm resamples to 192 kHz internally for true-peak
                detection and does not put it back, so the AAC encoder picked
                96 kHz and every render carried it. Nothing errored.
  not_silent    six renders were written at an empty gain and came out at
                -90 dBFS. The files existed, had the right duration, and the
                pipeline reported success.
  duration      a render that stops at the last MIDI event truncates a chord
                that is still ringing; one that doubles is a rate mismatch.
  no_clipping   a file at -1.0 dBFS *sample* peak measured +0.03 dBTP after
                AAC. The PCM meter said it was fine.
  loudness      the target is the whole point of the loudness stage; if it
                missed, the stage silently did nothing.
  channels      a dead right channel is inaudible on a mono check.
  dc_offset     a DC step is inaudible and wrecks headroom.
  brightness    a soundfont that fails to load can still render something.
                Energy above 1 kHz is what separates an instrument from a hum.

Run it on anything:

    python -m fp30x_studio.qa RENDER.m4a --expect-seconds 345 --expect-lufs -18
"""

from __future__ import annotations
import argparse, json, math, subprocess, sys, tempfile, os
from dataclasses import dataclass, asdict, field


@dataclass
class Check:
    name: str
    passed: bool
    value: str
    detail: str = ""
    fatal: bool = True          # False = warn only


@dataclass
class Report:
    path: str
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks if c.fatal)

    @property
    def failures(self):
        return [c for c in self.checks if not c.passed and c.fatal]

    @property
    def warnings(self):
        return [c for c in self.checks if not c.passed and not c.fatal]


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=sample_rate,channels,sample_fmt:format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return {"sample_rate": int(st.get("sample_rate", 0)),
            "channels": int(st.get("channels", 0)),
            "duration": float((d.get("format") or {}).get("duration", 0) or 0)}


def _decode(path, max_seconds=None):
    """Decode to float32 so measurement never depends on the container."""
    import soundfile as sf
    tmp = tempfile.mktemp(suffix=".wav")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-i", path, "-c:a", "pcm_f32le", tmp]
    subprocess.run(cmd, check=True)
    x, sr = sf.read(tmp, dtype="float32")
    os.remove(tmp)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    return x, sr


def _ebur128(path):
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", path, "-af",
                        "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    lufs = peak = None
    for line in reversed(r.stderr.splitlines()):
        t = line.strip()
        if lufs is None and t.startswith("I:") and "LUFS" in t:
            lufs = float(t[2:].replace("LUFS", "").strip())
        if peak is None and t.startswith("Peak:") and "dBFS" in t:
            peak = float(t[5:].replace("dBFS", "").strip())
        if lufs is not None and peak is not None:
            break
    return lufs, peak


def check(path, expect_sr=48000, expect_seconds=None, seconds_tol=None,
          expect_lufs=-18.0, lufs_tol=1.5, max_true_peak=-1.0,
          min_rms_db=-45.0) -> Report:
    import numpy as np

    rep = Report(path=path)
    meta = _probe(path)

    rep.checks.append(Check(
        "sample_rate", meta["sample_rate"] == expect_sr,
        f"{meta['sample_rate']} Hz",
        f"expected {expect_sr} Hz — loudnorm leaves 192 kHz behind unless aresample follows it"))

    rep.checks.append(Check(
        "channels", meta["channels"] == 2, f"{meta['channels']}",
        "expected stereo"))

    if expect_seconds:
        tol = seconds_tol if seconds_tol is not None else max(2.0, expect_seconds * 0.05)
        d = meta["duration"]
        rep.checks.append(Check(
            "duration", abs(d - expect_seconds) <= tol,
            f"{d:.1f} s",
            f"expected {expect_seconds:.1f} s ±{tol:.1f}"))

    x, sr = _decode(path)
    mono = x.mean(axis=1)
    rms = float(np.sqrt((mono.astype(np.float64) ** 2).mean()))
    rms_db = 20 * math.log10(max(rms, 1e-12))
    rep.checks.append(Check(
        "not_silent", rms_db > min_rms_db, f"{rms_db:.1f} dBFS RMS",
        f"a render quieter than {min_rms_db} dBFS is silence with a file size"))

    # Per-channel life. A dead channel passes every mono measurement.
    if x.shape[1] == 2:
        ch = [20 * math.log10(max(float(np.sqrt((x[:, i].astype(np.float64) ** 2).mean())), 1e-12))
              for i in range(2)]
        rep.checks.append(Check(
            "both_channels", min(ch) > min_rms_db,
            f"L {ch[0]:.1f} / R {ch[1]:.1f} dBFS",
            "one channel is silent"))

    full = int((np.abs(x) >= 0.999).sum())
    rep.checks.append(Check(
        "no_clipping", full == 0, f"{full} samples at full scale",
        "hard clipping"))

    dc = float(np.abs(mono.mean()))
    rep.checks.append(Check(
        "dc_offset", dc < 0.002, f"{dc:.5f}",
        "a DC step eats headroom and is inaudible", fatal=False))

    lufs, tpeak = _ebur128(path)
    if lufs is not None:
        rep.checks.append(Check(
            "loudness", abs(lufs - expect_lufs) <= lufs_tol, f"{lufs:.1f} LUFS",
            f"expected {expect_lufs} ±{lufs_tol}"))
    if tpeak is not None:
        rep.checks.append(Check(
            "true_peak", tpeak <= max_true_peak, f"{tpeak:.2f} dBTP",
            f"ceiling {max_true_peak} dBTP — sample peak does not see this"))

    # Brightness. A piano-like render carries real energy above 1 kHz; a failed
    # soundfont, a stuck oscillator or a pure hum does not.
    from numpy.fft import rfft, rfftfreq
    seg = mono[: sr * 30] if len(mono) > sr * 30 else mono
    if len(seg) > 1024:
        w = seg * np.hanning(len(seg))
        P = np.abs(rfft(w)) ** 2
        f = rfftfreq(len(w), 1 / sr)
        tot = P.sum() or 1.0
        hi = float(P[(f >= 1000) & (f < 8000)].sum() / tot)
        rep.checks.append(Check(
            "brightness", hi > 0.0005, f"{hi*100:.3f}% of energy 1–8 kHz",
            "almost no energy above 1 kHz — the instrument may not have loaded",
            fatal=False))

    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quality gate for a finished render.")
    ap.add_argument("path")
    ap.add_argument("--expect-sr", type=int, default=48000)
    ap.add_argument("--expect-seconds", type=float)
    ap.add_argument("--expect-lufs", type=float, default=-18.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    rep = check(a.path, expect_sr=a.expect_sr, expect_seconds=a.expect_seconds,
                expect_lufs=a.expect_lufs)

    if a.json:
        print(json.dumps({"ok": rep.ok, "path": rep.path,
                          "checks": [asdict(c) for c in rep.checks]}, indent=2))
    else:
        print(os.path.basename(rep.path))
        for c in rep.checks:
            mark = "PASS" if c.passed else ("WARN" if not c.fatal else "FAIL")
            print(f"  [{mark}] {c.name:14s} {c.value}"
                  + (f"   — {c.detail}" if not c.passed else ""))
        print("  OK" if rep.ok else f"  {len(rep.failures)} FAILED")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
