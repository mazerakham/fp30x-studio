"""Tests for :mod:`fp30x_studio.qa`, the gate on a finished render.

Every check in that module exists because something reached the library looking
finished. These tests exist because the gate itself reached the app without
being called: it was written, it worked, and `Actions.render` measured loudness
by hand and never compared it to anything. So what is pinned here is not only
that the checks fire, but that the two failures which actually shipped on
2026-09-24 -- a 96 kHz file and a file 2 dB off its loudness target -- are
caught rather than merely measured.

The fixtures are generated with ffmpeg so the suite needs no audio on disk, and
skip cleanly where ffmpeg is absent.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from fp30x_studio import qa

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")

SECONDS = 4.0


def _encode(path, *, rate=48000, filters="", src="sine=frequency=440:duration=4"):
    args = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"{src}", "-ac", "2"]
    if filters:
        args += ["-af", filters]
    args += ["-ar", str(rate), "-c:a", "aac", "-b:a", "256k",
             "-movflags", "+faststart", str(path)]
    subprocess.run(args, check=True)
    return path


def _named(rep, name):
    return next(c for c in rep.checks if c.name == name)


def test_a_good_render_passes_every_check(tmp_path):
    f = _encode(tmp_path / "ok.m4a", filters="loudnorm=I=-18:TP=-1.5:LRA=11,aresample=48000")
    rep = qa.check(str(f), expect_seconds=SECONDS, seconds_tol=0.5)
    assert rep.ok, [c.name for c in rep.failures]


def test_ninety_six_kilohertz_is_caught(tmp_path):
    """The 2026-09-24 defect: loudnorm's 192 kHz rate reaching the encoder."""
    f = _encode(tmp_path / "wrong-rate.m4a", rate=96000)
    rep = qa.check(str(f), expect_seconds=SECONDS, seconds_tol=0.5)
    assert not rep.ok
    assert _named(rep, "sample_rate").passed is False
    assert "96000" in _named(rep, "sample_rate").value


def test_missing_the_loudness_target_is_caught(tmp_path):
    """Dynamic normalisation landed 2 dB high and was recorded, not rejected."""
    f = _encode(tmp_path / "loud.m4a",
                filters="loudnorm=I=-12:TP=-1.5:LRA=11,aresample=48000")
    rep = qa.check(str(f), expect_seconds=SECONDS, seconds_tol=0.5,
                   expect_lufs=-18.0)
    assert not rep.ok
    assert _named(rep, "loudness").passed is False


def test_silence_is_caught(tmp_path):
    f = _encode(tmp_path / "silent.m4a", src="anullsrc=r=48000:cl=stereo:d=4")
    rep = qa.check(str(f), expect_seconds=SECONDS, seconds_tol=0.5)
    assert not rep.ok
    assert _named(rep, "not_silent").passed is False


def test_a_tight_tolerance_catches_a_length_change(tmp_path):
    """The check's real job: an encode that changed the length of the audio."""
    f = _encode(tmp_path / "ok.m4a", filters="loudnorm=I=-18:TP=-1.5:LRA=11,aresample=48000")
    rep = qa.check(str(f), expect_seconds=SECONDS * 2, seconds_tol=0.5)
    assert _named(rep, "duration").passed is False


def test_a_mono_render_is_caught(tmp_path):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-ac", "1", "-ar", "48000", "-c:a", "aac",
                    str(tmp_path / "mono.m4a")], check=True)
    rep = qa.check(str(tmp_path / "mono.m4a"), expect_seconds=SECONDS, seconds_tol=0.5)
    assert not rep.ok
    assert _named(rep, "channels").passed is False
