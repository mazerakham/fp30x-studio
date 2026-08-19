"""The written-down instrument model: named parameters, and the laws that use them.

Everything the synthesiser knows is either a field of :class:`Preset` -- which is
JSON, interchangeable with the Web Audio workbench -- or a module constant in
this file with the data that fixed it written next to it. Nothing is buried in
the render loop. If a render sounds wrong, the thing to edit is here.

Two models, deliberately different in kind
------------------------------------------
``"string"`` is a struck string: a stiff-string partial series, per-partial
exponential decay in two stages, and a spectral centroid that moves with strike
velocity. It is *additive* -- the spectrum is written out term by term.

``"tine"`` is a Rhodes-style tine: one near-sinusoidal fundamental driven by a
non-integer-ratio FM modulator whose index collapses in a few hundred
milliseconds, one strong inharmonic bell partial well above the fundamental, and
a separate percussive bark that only appears on hard strikes. It is *not* the
string model with different numbers; there is no ``n^-rolloff`` series in it at
all, and its velocity axis moves the FM index rather than a partial tilt.

The parameter names below are a contract shared with ``docs/timbre-workbench``.
A preset exported there loads here unchanged, so the keys, their spelling and
their units are not free to drift.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

__all__ = [
    "Preset",
    "PRESET_KEYS",
    "PRESET_DIR",
    "load_preset",
    "VELOCITY_FULL_SCALE",
    "VELOCITY_AMP_EXPONENT",
    "B_REFERENCE_NOTE",
    "TINE_DECAY_HALVING_SEMITONES",
    "FM_INDEX_DECAY_S",
    "PAN_WIDTH",
    "UNISON_LOWEST_NOTE",
    "IOWA_DYNAMIC_VNORM",
    "inharmonicity_at",
    "rolloff_at",
    "partial_frequencies",
    "radiation_gain",
    "decay_scale_at",
    "damper_contact",
    "velocity_norm",
]

# ---------------------------------------------------------------------------
# Constants fixed by the captured data, not by taste
# ---------------------------------------------------------------------------

#: Strike velocity is normalised against this, not against 127. The FP-30X's
#: hammer sensors never sent more than 103 across either take of 2026-08-17
#: (piece: 1-96, open: 1-103), so dividing by 127 would leave the top fifth of
#: the velocity->timbre curve permanently unreachable and every note dull.
VELOCITY_FULL_SCALE = 103.0

#: amplitude = velocity_norm ** this. 1.5 puts roughly 40 dB between the
#: softest and loudest strike actually played, which is about a real piano's
#: dynamic range from ppp to ff.
VELOCITY_AMP_EXPONENT = 1.5

#: ``inharmonicity_B``, ``partial_amp_rolloff`` and ``partial_decay_base`` in a
#: preset are all defined at this MIDI note (A4), and the per-register laws
#: below are exponents applied about it.
B_REFERENCE_NOTE = 69

#: The tine's decay is far less pitch-dependent than a string's -- the bars are
#: individually tuned resonators, not one scaled string -- so its halving
#: length is much longer. Not fitted: no licensed single-note Rhodes corpus was
#: available, see ``docs/timbre-fit.html``.
TINE_DECAY_HALVING_SEMITONES = 48.0

#: The FM index falls with this time constant, independent of the amplitude
#: envelope. This is what makes the tine bark at the attack and turn into a
#: near-sine a moment later.
FM_INDEX_DECAY_S = 0.35

#: Below this note nothing beats. Measured on the Iowa Steinway B: the
#: amplitude modulation of the fundamental has a periodogram concentration of
#: 0.013 below MIDI 48 and 0.020 over 48-59, against 0.337 from MIDI 60 up.
#: Under 48 the strings are single wound ones, so ``unison_depth`` is faded to
#: nothing below it and reaches full an octave higher.
UNISON_LOWEST_NOTE = 48

#: What the three Iowa dynamic markings were taken to be on the model's
#: normalised velocity axis. This is the one assumption joining the recorded
#: corpus to the MIDI velocity byte, and it is an assumption: the recordings
#: carry no velocity data, only the words pp, mf and ff. Everything downstream
#: that depends on velocity -- ``velocity_brightness`` above all -- inherits its
#: error, which is why that coefficient's confidence interval is the widest of
#: the fitted set.
IOWA_DYNAMIC_VNORM = {"pp": 0.25, "mf": 0.55, "ff": 0.95}

#: Stereo width. Pan position is linear in pitch across the 88 keys; +-this at
#: the extremes. Purely a listening convenience, no physical claim.
PAN_WIDTH = 0.35


# ---------------------------------------------------------------------------
# The preset
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Preset:
    """One instrument, as named numbers.

    The field names and order are the shared schema. ``to_json`` emits exactly
    these keys and ``from_dict`` accepts exactly these keys, so a round trip
    through the workbench is lossless and a typo is an error rather than a
    silently ignored parameter.
    """

    model: str = "string"

    # -- partial series (string). B, rolloff and tau_1 are all "at A4"; the
    #    three per-register laws beside them say how they move with pitch. --
    partials: int = 32
    inharmonicity_B: float = 6.5e-4
    inharmonicity_decades_per_octave: float = 0.386
    inharmonicity_floor: float = 1.4e-4
    partial_amp_rolloff: float = 3.0
    rolloff_per_octave: float = 0.64
    hammer_strike_point: float = 0.091
    partial_decay_base: float = 0.75
    decay_halving_semitones: float = 15.5
    partial_decay_exponent: float = 0.13
    second_stage_ratio: float = 12.0
    second_stage_mix: float = 0.011

    # -- the two strings of a unison, beating against each other --
    unison_detune_cents: float = 1.5
    unison_depth: float = 0.25

    # -- attack --
    attack_ms: float = 4.0
    hammer_noise: float = 0.18
    velocity_brightness: float = 1.6
    partial_phase_spread: float = 1.0

    # -- release / damper --
    release_ms_fast: float = 45.0
    release_ms_slow: float = 320.0
    damper_cc64_scale: float = 1.0
    damper_decay_exponent: float = 1.0
    pedal_engage: float = 0.55
    pedal_knee: float = 0.25
    pedal_leak: float = 0.02
    pedal_contact_bite: float = 0.05

    # -- tine --
    fm_ratio: float = 3.37
    fm_index: float = 2.6
    bell_partial: float = 6.27
    bell_amp: float = 0.32
    bark_ms: float = 55.0
    bark_amp: float = 0.45

    # -- bookkeeping, not part of the shared schema --
    name: str = ""
    note: str = ""

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: d[k] for k in PRESET_KEYS if k in d} | (
            {"name": self.name} if self.name else {}) | (
            {"note": self.note} if self.note else {})

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent) + "\n"

    @classmethod
    def from_dict(cls, d: dict) -> "Preset":
        # The workbench exports the shared parameters inside an envelope --
        # ``{schema, generated, source, params, page_only}`` -- because its own
        # morph/layer/gain controls are page state and deliberately not part of
        # the schema. Unwrap it, keep the provenance, and drop the page state.
        if isinstance(d.get("params"), dict):
            env, d = d, dict(d["params"])
            src = env.get("source", "")
            when = env.get("generated", "")
            d.setdefault("note", " ".join(
                x for x in (f"exported from {src}" if src else "",
                            f"at {when}" if when else "") if x).strip())

        known = {f.name for f in fields(cls)}
        unknown = {k for k in d if k not in known and not k.startswith("_")}
        d = {k: v for k, v in d.items() if not k.startswith("_")}
        if unknown:
            raise ValueError(
                f"preset has keys this model does not define: {sorted(unknown)}; "
                f"the schema is shared with the workbench and is not extensible "
                f"without changing both")
        return cls(**d)

    @classmethod
    def from_json(cls, blob: str) -> "Preset":
        return cls.from_dict(json.loads(blob))

    @classmethod
    def load(cls, path: str | Path) -> "Preset":
        p = Path(path).expanduser()
        return cls.from_json(p.read_text())

    def save(self, path: str | Path) -> Path:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json())
        return p


#: The shared schema, in the order the workbench lists it.
PRESET_KEYS = (
    "model",
    "partials",
    "inharmonicity_B",
    "inharmonicity_decades_per_octave",
    "inharmonicity_floor",
    "partial_amp_rolloff",
    "rolloff_per_octave",
    "hammer_strike_point",
    "partial_decay_base",
    "decay_halving_semitones",
    "partial_decay_exponent",
    "second_stage_ratio",
    "second_stage_mix",
    "unison_detune_cents",
    "unison_depth",
    "attack_ms",
    "hammer_noise",
    "velocity_brightness",
    "partial_phase_spread",
    "release_ms_fast",
    "release_ms_slow",
    "damper_cc64_scale",
    "damper_decay_exponent",
    "pedal_engage",
    "pedal_knee",
    "pedal_leak",
    "pedal_contact_bite",
    "fm_ratio",
    "fm_index",
    "bell_partial",
    "bell_amp",
    "bark_ms",
    "bark_amp",
)

PRESET_DIR = Path(__file__).parent / "presets"


def load_preset(name_or_path: str | Path) -> Preset:
    """A bare name resolves in ``synth/presets``; anything else is a path."""
    p = Path(name_or_path).expanduser()
    if p.suffix == ".json" or p.exists():
        return Preset.load(p)
    cand = PRESET_DIR / f"{name_or_path}.json"
    if not cand.exists():
        have = sorted(x.stem for x in PRESET_DIR.glob("*.json"))
        raise FileNotFoundError(f"no preset {name_or_path!r}; have {have}")
    return Preset.load(cand)


# ---------------------------------------------------------------------------
# The laws
# ---------------------------------------------------------------------------

def velocity_norm(velocity: int) -> float:
    """MIDI strike velocity on [0, 1], scaled to the range the action uses."""
    return min(max(velocity, 0) / VELOCITY_FULL_SCALE, 1.0)


def inharmonicity_at(note: int, B_ref: float,
                     decades_per_octave: float = 0.386,
                     floor: float = 1.4e-4) -> float:
    """B for one key. ``B_ref`` is the preset's value, defined at A4.

    Fitted on 23 Iowa Steinway B notes from C3 up: ``log10 B`` is linear in
    pitch with slope 0.386 +- 0.027 decades per octave -- a decade every 31
    semitones, not the 60 the model used to assume -- and B(A4) = 6.5e-4, not
    4e-4. Residual scatter about the line is a factor of 1.38.

    The floor is not cosmetic. Below about C3 the law stops: measured B is
    1.4e-4 (IQR 1.28-1.62e-4) flat from MIDI 30 to 48 and then *rises* again to
    2.5e-4 at C1, because those are wound strings on a different scaling and the
    bass break is a discontinuity, not a bend. The exponential extrapolates to
    2.3e-5 at C1, sixteen times too small. A floor gets the flat part right and
    leaves the C1 rise as a known, documented residual.
    """
    B = B_ref * 10.0 ** ((note - B_REFERENCE_NOTE) / 12.0 * decades_per_octave)
    return max(B, floor)


def rolloff_at(note: int, rolloff_ref: float, per_octave: float = 0.64) -> float:
    """The partial amplitude tilt for one key, ``a_n ~ n^-rolloff``.

    One number for the whole keyboard is wrong by more than any other single
    thing in the old preset. Fitted jointly over pitch and dynamic on 17 notes:
    rolloff is 3.02 +- 0.19 at A4 and rises 0.643 +- 0.152 per octave, so the
    bass is nearly flat (0.6 at C1) and the top two octaves are steep (4.3 at
    C7). The old constant 1.25 was far too bright above the middle and far too
    dark below it, and a treble note synthesised with it carries twenty partials
    that the recorded instrument does not have.
    """
    return max(0.0, rolloff_ref + per_octave * (note - B_REFERENCE_NOTE) / 12.0)


def partial_frequencies(f0: float, n_max: int, B: float, nyquist: float):
    """``f_n = n f0 sqrt(1 + B n^2)`` for the partials that fit under Nyquist.

    Returns ``(n, f)`` as parallel numpy arrays. The stiffness term is what
    makes a piano a piano, and the law itself is confirmed by the data rather
    than assumed: fitted per note, the RMS departure of the measured partials
    from this curve is 1-3 cents over as many as 32 partials. The law is right;
    it was the coefficient that was wrong.
    """
    import numpy as np

    n = np.arange(1, max(int(n_max), 1) + 1, dtype=np.float64)
    f = n * f0 * np.sqrt(1.0 + B * n * n)
    keep = f < nyquist * 0.98
    return n[keep], f[keep]


def decay_scale_at(note: int, halving_semitones: float = 15.5) -> float:
    """Multiplier on ``partial_decay_base`` for one key. 1.0 at A4.

    Fitted on the prompt (first 25 dB) decay of 26 notes: the fundamental's
    time constant falls 0.775 +- 0.097 octaves per octave of pitch, halving
    every 15.5 semitones rather than every 24. Scatter about the line is 0.89
    octaves, which is large and honest -- neighbouring keys on a real piano
    differ by that much.
    """
    return 2.0 ** (-(note - B_REFERENCE_NOTE) / halving_semitones)


def damper_contact(cc64: float, engage: float = 0.55, knee: float = 0.25,
                   leak: float = 0.02, scale: float = 1.0) -> float:
    """Felt-on-string contact ``d`` in [0, 1], from the half-damper position.

    The old map was ``d = 1 - cc64/127``: linear, and zero at the top. Both
    halves of that are wrong about the mechanism.

    * A damper pedal has an **escapement**. Through the top of its travel the
      felt is clear of the strings and nothing changes; contact is lost, and
      regained, over a narrow band partway down. ``engage`` is where that band
      is centred and ``knee`` is how wide it is, as fractions of full travel.
    * A real damper **leaks**. Felt is not a perfect absorber, and the strings
      it is not touching are still coupled through the bridge. ``leak`` is the
      residual contact at full pedal. It matters far more than its size
      suggests, because cc64 sat at 127 for 86.6-91.4% of the playing time on
      his own takes: with ``leak = 0`` the damper is not weak during that time,
      it is *switched off*, and every pedalled note rings to the model's tail
      limit instead of dying.

    ``scale`` is the preset's ``damper_cc64_scale`` and still multiplies the
    whole pedal effect, so ``0`` is a piano with no pedal at all.
    """
    u = min(max(cc64, 0.0), 127.0) / 127.0 * scale
    if knee <= 1e-6:
        step = 0.0 if u >= engage else 1.0
    else:
        step = min(max((engage - u) / knee + 0.5, 0.0), 1.0)
    return min(max(leak + (1.0 - leak) * step, 0.0), 1.0)


def velocity_norm(velocity: int) -> float:
    """MIDI strike velocity on [0, 1], scaled to the range the action uses."""
    return min(max(velocity, 0) / VELOCITY_FULL_SCALE, 1.0)
