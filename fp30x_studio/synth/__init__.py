"""Synthesis from the captured performance, with no samples anywhere in it.

The whole instrument is a page of named numbers (:mod:`.model`), two voice
functions that read them (:mod:`.voices`), and a mixer (:mod:`.engine`). Events
come from the pipeline index via :mod:`.score` -- there is no second MIDI parser
here, deliberately.

    python -m fp30x_studio.synth presets
    python -m fp30x_studio.synth render 2026-08-17-piece.fp30 -p acoustic
"""

from .engine import RenderResult, render, write_wav
from .model import PRESET_DIR, PRESET_KEYS, Preset, load_preset
from .score import Note, Score, read_score
from .voices import VOICES, note_frequency, release_tau, render_string, render_tine

__all__ = [
    "Preset", "PRESET_KEYS", "PRESET_DIR", "load_preset",
    "Note", "Score", "read_score",
    "VOICES", "render_string", "render_tine", "note_frequency", "release_tau",
    "render", "write_wav", "RenderResult",
]
