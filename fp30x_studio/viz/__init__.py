r"""From-scratch music visualisation: every mark built out of primitives.

No notation font is loaded and no notation library is called. A clef here is a
centre line and a pen-width profile (:mod:`.glyphs`); where a pitch lands is a
diatonic-step calculation (:mod:`.staff`); when it lands is the take's own
timestamp (:mod:`.perf`). :mod:`.scene` is the ten-second piece those three
make, and :mod:`.render` is the command that renders it with sound.

The one thing read from outside is note *data* -- onset, release, pitch,
velocity -- from a ``.fp30`` capture. Nothing anyone else drew is used.
"""

from .perf import Note, Trill, detect_trills, load_window
from .staff import GrandStaff, spell, step_of

__all__ = ["Note", "Trill", "detect_trills", "load_window",
           "GrandStaff", "spell", "step_of"]
