"""Timbre as FPX records it — two halves that must not be confused.

MIDI does not carry tone. It carries the gestures that cause tone: how hard a
key was struck, whether the damper was off the strings, whether one note was
still sounding when the next began. Those are *performed timbre* — measurable
from a capture, identical on every replay, and the thing worth searching a
practice session by.

What the listener actually hears is *rendered timbre*: the performed gestures
put through an instrument model. That half lives in the preset
(`synth/presets/*.json`) and belongs to a transformation, never to the capture.
A capture has no rendered timbre. A result has exactly one.

Both halves are needed to make a rendering reproducible, and keeping them
apart is what stops a bright-sounding preset from being mistaken for bright
playing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

# CC64. The FP-30X sends continuous values, not just 0/127, so a threshold
# is a modelling choice: >=64 is "damper off the strings" by MIDI convention,
# and 1..63 is a real half-pedal the piano can produce and the convention
# cannot express.
PEDAL_DOWN = 64


def _stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "mean": None, "sd": None, "p10": None, "p90": None}
    s = sorted(xs)
    n = len(s)
    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / n
    return {
        "n": n,
        "mean": round(mean, 2),
        "sd": round(var ** 0.5, 2),
        "p10": round(s[int(0.10 * (n - 1))], 1),
        "p90": round(s[int(0.90 * (n - 1))], 1),
    }


def load_pedal(db: Path) -> list[tuple[int, int]]:
    """(ns, value) for every CC64 message, in order."""
    con = sqlite3.connect(db)
    return con.execute(
        "SELECT ns, d2 FROM message "
        "WHERE kind='control_change' AND d1=64 ORDER BY ns"
    ).fetchall()


def pedal_profile(pedal: list[tuple[int, int]], t0_ns: int, t1_ns: int) -> dict:
    """How much of the window was pedalled, and how the pedal was used.

    Pedal state is *chased*: the value in force at t0 is the last value sent
    before it, which may be tens of seconds earlier. A window that ignores
    this reports a dry opening for a passage that was held throughout.
    """
    span = max(t1_ns - t0_ns, 1)
    before = [v for ns, v in pedal if ns <= t0_ns]
    state = before[-1] if before else 0
    inside = [(ns, v) for ns, v in pedal if t0_ns < ns <= t1_ns]

    down_ns = half_ns = 0
    depth_ns = 0.0
    cur, cur_t = state, t0_ns
    for ns, v in inside + [(t1_ns, state)]:
        dt = ns - cur_t
        if cur >= PEDAL_DOWN:
            down_ns += dt
        elif cur > 0:
            half_ns += dt
        depth_ns += cur * dt
        cur, cur_t = v, ns

    return {
        "entering_value": state,
        "chased": bool(before) and (not inside or inside[0][0] > t0_ns),
        "changes": len(inside),
        "down_fraction": round(down_ns / span, 3),
        "half_fraction": round(half_ns / span, 3),
        "mean_depth": round(depth_ns / span, 1),
    }


def performed(notes: list[tuple[float, int, int, float]],
              pedal: list[tuple[int, int]] | None = None,
              t0_ns: int | None = None, t1_ns: int | None = None) -> dict:
    """Performed-timbre features for one window.

    `notes` is (t_seconds, note, velocity, duration_seconds), the shape
    `segment.load` returns.
    """
    if not notes:
        return {"schema_version": SCHEMA_VERSION, "notes": 0}

    ons = [e[0] for e in notes]
    vels = [float(e[2]) for e in notes]
    durs = [float(e[3]) for e in notes]
    pits = [e[1] for e in notes]
    span = (ons[-1] - ons[0]) or 1.0

    # Legato index: what share of onsets arrive while the previous note is
    # still held. Overlapping notes are the player's hand, not the pedal —
    # the two produce very different sound and only one of them is CC64.
    overlaps = sum(
        1 for i in range(1, len(notes))
        if ons[i] < ons[i - 1] + durs[i - 1] - 0.005
    )

    ioi = [b - a for a, b in zip(ons, ons[1:]) if b - a > 0.005]
    mean_ioi = sum(ioi) / len(ioi) if ioi else 0.0
    mean_dur = sum(durs) / len(durs)

    wsum = sum(durs) or 1.0
    centroid = sum(p * d for p, d in zip(pits, durs)) / wsum
    spread = (sum(((p - centroid) ** 2) * d for p, d in zip(pits, durs)) / wsum) ** 0.5

    # Voicing: in a chord (onsets within 30 ms), how far the top note is
    # pushed above the mean of the notes under it. This is what "projecting
    # the melody" measures as.
    tops, chords = [], []
    cur = [notes[0]]
    for e in notes[1:]:
        if e[0] - cur[0][0] <= 0.030:
            cur.append(e)
        else:
            chords.append(cur)
            cur = [e]
    chords.append(cur)
    for ch in chords:
        if len(ch) >= 2:
            top = max(ch, key=lambda e: e[1])
            under = [e[2] for e in ch if e is not top]
            tops.append(top[2] - sum(under) / len(under))

    half = len(vels) // 2 or 1
    arc = (sum(vels[half:]) / max(len(vels) - half, 1)) - (sum(vels[:half]) / half)

    out = {
        "schema_version": SCHEMA_VERSION,
        "notes": len(notes),
        "velocity": _stats(vels),
        "attack_rate_hz": round(len(notes) / span, 2),
        "legato_index": round(overlaps / max(len(notes) - 1, 1), 3),
        "duty_cycle": round(mean_dur / mean_ioi, 2) if mean_ioi else None,
        "register_centroid": round(centroid, 1),
        "register_spread": round(spread, 1),
        "chords": len(chords),
        "voicing_top_lead": round(sum(tops) / len(tops), 1) if tops else None,
        "dynamic_arc": round(arc, 1),
    }
    if pedal is not None and t0_ns is not None and t1_ns is not None:
        out["pedal"] = pedal_profile(pedal, t0_ns, t1_ns)
    return out


def envelope(notes, buckets: int = 64) -> dict:
    """A coarse shape of the window: where the notes are, how hard, how high.

    Purely for display. It is derived from the same intervals as everything
    else, so it can be regenerated and never needs to be trusted.
    """
    if not notes:
        return {"buckets": 0, "density": [], "velocity": [], "pitch": []}
    t0, t1 = notes[0][0], notes[-1][0]
    span = (t1 - t0) or 1.0
    dens = [0] * buckets
    vsum = [0.0] * buckets
    psum = [0.0] * buckets
    for t, n, v, _d in notes:
        i = min(int((t - t0) / span * buckets), buckets - 1)
        dens[i] += 1
        vsum[i] += v
        psum[i] += n
    return {
        "buckets": buckets,
        "density": dens,
        "velocity": [round(vsum[i] / dens[i]) if dens[i] else 0
                     for i in range(buckets)],
        "pitch": [round(psum[i] / dens[i]) if dens[i] else 0
                  for i in range(buckets)],
    }


def rendered_soundfont(soundfont: str | Path, *, engine: str = "fluidsynth",
                       gain: float = 0.2, rate: int = 44100,
                       reverb: dict | None = None) -> dict:
    """A result rendered by playing samples out of a soundfont.

    `gain` is recorded because fluidsynth's default is 0.2, which leaves
    about 24 dB unused; a file rendered at the default and one amplified
    afterwards are not the same file and must not claim to be.
    """
    p = Path(soundfont)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "soundfont",
        "engine": engine,
        "soundfont": p.name,
        "soundfont_path": str(p),
        "gain": gain,
        "sample_rate": rate,
        "reverb": reverb,
        "velocity_layers_verified": None,
        "note": ("Sampled, not modelled. Timbre here is whatever the "
                 "soundfont's author recorded; it carries no fitted "
                 "relationship to the FP-30X."),
    }


def rendered_model(preset_path: str | Path, *, gain: float,
                   engine: str = "fp30x_studio.synth",
                   reverb: dict | None = None) -> dict:
    """A result rendered by the physical model, from a named preset."""
    import json
    p = Path(preset_path)
    preset = json.loads(p.read_text())
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "model",
        "engine": engine,
        "preset_name": preset.get("name"),
        "preset_path": str(p),
        "model": preset.get("model"),
        "fitted": preset.get("name") == "acoustic",
        "gain": gain,
        "reverb": reverb,
        # The whole preset, inline. A result must stay reproducible when the
        # preset file is later refitted; a path alone does not survive that.
        "preset": preset,
    }
