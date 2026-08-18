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
    "B_DECADE_SEMITONES",
    "DECAY_HALVING_SEMITONES",
    "TINE_DECAY_HALVING_SEMITONES",
    "SECOND_STAGE_RATIO",
    "SECOND_STAGE_MIX",
    "FM_INDEX_DECAY_S",
    "PAN_WIDTH",
    "HAMMER_STRIKE_POINT",
    "UNISON_DETUNE_CENTS",
    "UNISON_DEPTH",
    "UNISON_LOWEST_NOTE",
    "inharmonicity_at",
    "partial_frequencies",
    "decay_scale_at",
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

#: ``inharmonicity_B`` in a preset is B at this MIDI note (A4).
B_REFERENCE_NOTE = 69

#: B rises by a factor of ten every this many semitones, so a preset value of
#: 4e-4 at A4 gives 8.6e-5 at F1 and 8.6e-4 at F6 -- the 1e-4-bass-to-1e-3-
#: treble span measured on real uprights, spread over the F1-F6 the piece uses.
B_DECADE_SEMITONES = 60.0

#: Fundamental decay time halves every this many semitones upward. A bass note
#: rings for tens of seconds and a treble note for about one; a single
#: ``partial_decay_base`` cannot express that, so the pitch law is here.
DECAY_HALVING_SEMITONES = 24.0

#: The tine's decay is far less pitch-dependent than a string's -- the bars are
#: individually tuned resonators, not one scaled string -- so its halving
#: length is much longer.
TINE_DECAY_HALVING_SEMITONES = 48.0

#: Two-stage decay. A struck string's two polarisations decouple: the vertical
#: one dumps energy into the bridge quickly, the horizontal one hangs on. The
#: envelope is ``(1 - mix) * exp(-t/tau) + mix * exp(-t/(tau * ratio))``.
SECOND_STAGE_RATIO = 3.5
SECOND_STAGE_MIX = 0.22

#: The FM index falls with this time constant, independent of the amplitude
#: envelope. This is what makes the tine bark at the attack and turn into a
#: near-sine a moment later.
FM_INDEX_DECAY_S = 0.35

#: Where the hammer hits, as a fraction of the speaking length. The hammer
#: cannot excite a partial with a node at the strike point, so ``a_n`` is
#: multiplied by ``|sin(n pi alpha)|``: at alpha = 1/8 the 8th, 16th and 24th
#: partials are notched out. This is the single cheapest thing that separates a
#: struck string from an additive series, and real pianos strike between 1/7 and
#: 1/9 for exactly this reason. It is a fixed constant rather than a preset
#: field because the shared schema does not carry it; the workbench, which does
#: not apply it, will render the same preset a little brighter at the notches.
HAMMER_STRIKE_POINT = 0.125

#: Unisons. Two or three strings per note, tuned very slightly apart, beat
#: against each other. Two equal detuned sines are exactly one sine at the mean
#: frequency times a cosine at half the difference, so this costs one extra
#: cosine per partial rather than a second oscillator, and the beat rate scales
#: with partial frequency the way it physically must. Depth is below 1 because
#: the treble has three strings, not two, and never nulls completely.
UNISON_DETUNE_CENTS = 0.7
UNISON_DEPTH = 0.40

#: Below this note the strings are single wound ones and there is no unison to
#: beat, so the depth is faded out under it.
UNISON_LOWEST_NOTE = 40

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

    # -- partial series (string) --
    partials: int = 24
    inharmonicity_B: float = 4.0e-4
    partial_amp_rolloff: float = 1.6
    partial_decay_base: float = 3.0
    partial_decay_exponent: float = 0.75

    # -- attack --
    attack_ms: float = 4.0
    hammer_noise: float = 0.18
    velocity_brightness: float = 1.2

    # -- release / damper --
    release_ms_fast: float = 45.0
    release_ms_slow: float = 320.0
    damper_cc64_scale: float = 1.0

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
    "partial_amp_rolloff",
    "partial_decay_base",
    "partial_decay_exponent",
    "attack_ms",
    "hammer_noise",
    "velocity_brightness",
    "release_ms_fast",
    "release_ms_slow",
    "damper_cc64_scale",
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


def inharmonicity_at(note: int, B_ref: float) -> float:
    """B for one key. ``B_ref`` is the preset's value, defined at A4."""
    return B_ref * 10.0 ** ((note - B_REFERENCE_NOTE) / B_DECADE_SEMITONES)


def partial_frequencies(f0: float, n_max: int, B: float, nyquist: float):
    """``f_n = n f0 sqrt(1 + B n^2)`` for the partials that fit under Nyquist.

    Returns ``(n, f)`` as parallel numpy arrays. The stiffness term is what
    makes a piano a piano: by the 16th partial at B = 4e-4 the series is already
    a quarter-tone sharp of harmonic, and it is that stretch -- not the
    amplitude envelope -- that stops additive synthesis sounding like an organ.
    """
    import numpy as np

    n = np.arange(1, max(int(n_max), 1) + 1, dtype=np.float64)
    f = n * f0 * np.sqrt(1.0 + B * n * n)
    keep = f < nyquist * 0.98
    return n[keep], f[keep]


def decay_scale_at(note: int, halving_semitones: float = DECAY_HALVING_SEMITONES
                   ) -> float:
    """Multiplier on ``partial_decay_base`` for one key. 1.0 at A4."""
    return 2.0 ** (-(note - B_REFERENCE_NOTE) / halving_semitones)
