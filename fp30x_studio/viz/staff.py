r"""Where a pitch goes on the page, and the furniture it needs to get there.

Two coordinate systems and one conversion between them.

**Diatonic step.** A staff is not a pitch axis, it is a *letter* axis. Vertical
position is fixed by the letter name and octave alone; the accidental is a
separate mark and moves nothing. So the mapping is MIDI -> spelling ->
:math:`7\,\mathrm{oct}+L` -> y, and the middle term is where the musical
decision lives. Spelling a pitch needs a key: in B major MIDI 60 is C-natural
and wants a natural sign, while MIDI 61 is C-sharp and wants nothing, because
the key signature has already said so. :data:`SHARP_KEYS` holds that table.

**Staff spaces.** Everything above is in half-steps of the staff, where one
space (two steps) is the unit. :class:`GrandStaff` multiplies by ``space`` once,
at the end. Nothing upstream of it knows about manim units, which is what lets
the glyph geometry in :mod:`.glyphs` be authored at a single fixed size.

The vertical anchor is **middle C at y = 0**. That is not arbitrary: C4 is the
one pitch equidistant from both staves, so the whole system is symmetric about
it and the treble/bass gap comes out right by construction rather than by a
tuned constant. The first version placed the two staves independently and the
gap was one space too wide -- C4's ledger line landed inside the treble staff.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from manim import ORIGIN, Line, VGroup, VMobject

from . import glyphs

#: Letter index within an octave, C = 0.
_LETTER = "CDEFGAB"

#: Spelling table for a sharp key: pitch class -> (letter index, needs a mark).
#:
#: ``needs a mark`` is relative to the key signature, so it is the *accidental
#: that has to be printed*, not the pitch's alteration: in B major a C-sharp
#: prints nothing and a C-natural prints a natural.
SHARP_KEYS: dict[str, dict[int, tuple[int, str]]] = {
    # B major / G# minor: F# C# G# D# A#
    "B": {
        0: (0, "natural"), 1: (0, ""), 2: (1, "natural"), 3: (1, ""),
        4: (2, ""), 5: (3, "natural"), 6: (3, ""), 7: (4, "natural"),
        8: (4, ""), 9: (5, "natural"), 10: (5, ""), 11: (6, ""),
    },
    # A-flat major / F minor: Bb Eb Ab Db.  Without this entry a
    # four-flat passage renders under five sharps -- every note then needs a
    # natural to cancel a sharp that should not be there.
    #
    # Spelled flat throughout: pc 6 is G-flat, not F-sharp. No entry crosses an
    # octave boundary (no C-flat, no B-sharp), which is what `spell`'s octave
    # arithmetic below requires.
    # Ab Bb C Db Eb F G are IN the signature and therefore print nothing.
    # Only the four notes outside it carry a mark.
    "Ab": {
        0: (0, ""), 1: (1, ""), 2: (1, "natural"), 3: (2, ""),
        4: (2, "natural"), 5: (3, ""), 6: (4, "flat"), 7: (4, ""),
        8: (5, ""), 9: (5, "natural"), 10: (6, ""), 11: (6, "natural"),
    },
}

#: Where each accidental of a key signature sits. Sharps run F C G D A E B,
#: flats run the same order reversed, B E A D G C F -- and the flats sit lower
#: on the staff, which is why they cannot share the sharps' positions.
KEY_SIGNATURES: dict[str, dict] = {
    "B": {
        "accidental": "sharp",
        "treble": [("F", 5), ("C", 5), ("G", 5), ("D", 5), ("A", 4)],
        "bass": [("F", 3), ("C", 3), ("G", 3), ("D", 3), ("A", 2)],
    },
    "Ab": {
        "accidental": "flat",
        "treble": [("B", 4), ("E", 5), ("A", 4), ("D", 5)],
        "bass": [("B", 2), ("E", 3), ("A", 2), ("D", 3)],
    },
}

#: Kept as an alias: older code referred to this name directly.
KEY_SIG_B_MAJOR = KEY_SIGNATURES["B"]

MIDDLE_C_STEP = 7 * 4 + 0          # C4


def spell(midi: int, key: str = "B") -> tuple[int, str]:
    """``(diatonic step, accidental name)`` for a MIDI note in ``key``.

    The octave is ``midi // 12 - 1`` for every spelling in the table because no
    entry crosses an octave boundary -- there is no C-flat or B-sharp in a sharp
    key's natural spelling. Add one and this line breaks silently, so the table
    and this arithmetic have to be changed together.
    """
    try:
        letter, acc = SHARP_KEYS[key][midi % 12]
    except KeyError:
        raise KeyError(
            f"no spelling table for key {key!r}; have {sorted(SHARP_KEYS)}. "
            "A missing key does not fail loudly anywhere else -- it silently "
            "spells every note in the wrong key -- so it fails loudly here."
        ) from None
    return 7 * (midi // 12 - 1) + letter, acc


def step_of(letter: str, octave: int) -> int:
    return 7 * octave + _LETTER.index(letter)


@dataclass(frozen=True)
class GrandStaff:
    """Layout of one grand staff, in manim units.

    ``space`` is the distance between adjacent staff lines. Every other length
    on the page is quoted as a multiple of it.

    Every glyph is scaled ``about_point=ORIGIN``. Manim scales about a
    mobject's *centre* by default, which silently moves anything whose origin is
    not its centre -- the G clef's origin is its eye, so the default put every
    clef about a space and a half too high.
    """

    space: float = 0.30
    x_left: float = -7.4
    x_right: float = 17.0

    # Staff lines named by the pitch that sits on them.
    TREBLE = [("E", 4), ("G", 4), ("B", 4), ("D", 5), ("F", 5)]
    BASS = [("G", 2), ("B", 2), ("D", 3), ("F", 3), ("A", 3)]

    def y(self, step: int) -> float:
        """Height of a diatonic step. One step is half a space."""
        return (step - MIDDLE_C_STEP) * self.space / 2.0

    def y_of(self, letter: str, octave: int) -> float:
        return self.y(step_of(letter, octave))

    # ------------------------------------------------------------- furniture

    def staff_lines(self, color: str = "#9aa0a6", opacity: float = 0.85) -> VGroup:
        g = VGroup()
        for names in (self.TREBLE, self.BASS):
            for letter, octave in names:
                yy = self.y_of(letter, octave)
                g.add(Line([self.x_left, yy, 0], [self.x_right, yy, 0],
                           stroke_width=1.5, color=color, stroke_opacity=opacity))
        return g

    def clefs(self, x: float, color: str = "#FFFFFF") -> VGroup:
        """Both clefs, positioned by the pitch each one names.

        The G clef's origin is its eye and the F clef's is between its dots, so
        each glyph is placed by moving that origin onto the line it names. No
        vertical fudge constant appears anywhere, which is the point.
        """
        t = glyphs.treble_clef(color).scale(self.space, about_point=ORIGIN)
        t.shift([x, self.y_of("G", 4), 0])
        b = glyphs.bass_clef(color).scale(self.space, about_point=ORIGIN)
        b.shift([x + 0.20, self.y_of("F", 3), 0])
        return VGroup(t, b)

    def key_signature(self, x: float, color: str = "#FFFFFF",
                      key: str = "B") -> VGroup:
        """The key signature for ``key``, on both staves."""
        spec = KEY_SIGNATURES[key]
        maker = glyphs.flat if spec["accidental"] == "flat" else glyphs.sharp
        g = VGroup()
        for staff in ("treble", "bass"):
            for i, (letter, octave) in enumerate(spec[staff]):
                s = maker(color).scale(self.space, about_point=ORIGIN)
                s.shift([x + i * 0.62 * self.space * 2.0, self.y_of(letter, octave), 0])
                g.add(s)
        return g

    def ledger_lines(self, step: int, x: float, color: str = "#c9ced4",
                     opacity: float = 0.9) -> VGroup:
        """Ledger lines between the nearest staff and a note that sits outside it.

        Both staves are consulted, so a note in the gap between them gets middle
        C's single line and nothing else -- the case that a per-staff version
        gets wrong by drawing the same line twice at two slightly different
        widths.
        """
        g = VGroup()
        top = step_of("F", 5)
        bottom = step_of("G", 2)
        upper_of_bass = step_of("A", 3)
        lower_of_treble = step_of("E", 4)
        half = 0.85 * self.space

        def add(s: int) -> None:
            yy = self.y(s)
            g.add(Line([x - half, yy, 0], [x + half, yy, 0],
                       stroke_width=1.5, color=color, stroke_opacity=opacity))

        if step > top:
            for s in range(top + 2, step + 1, 2):
                add(s)
        elif step < bottom:
            for s in range(bottom - 2, step - 1, -2):
                add(s)
        elif upper_of_bass < step < lower_of_treble:
            # The gap. Only even steps carry a line; C4 is the only one here.
            for s in range(upper_of_bass + 2, lower_of_treble, 2):
                if min(step, MIDDLE_C_STEP) <= s <= max(step, MIDDLE_C_STEP):
                    add(s)
        return g

    def accidental(self, name: str, step: int, x: float, color: str = "#FFFFFF",
                   opacity: float = 1.0) -> VMobject | None:
        if not name:
            return None
        maker = {"sharp": glyphs.sharp, "flat": glyphs.flat}.get(
            name, glyphs.natural)
        m = maker(color, opacity).scale(self.space, about_point=ORIGIN)
        m.shift([x - 1.35 * self.space, self.y(step), 0])
        return m


def wavy(x0: float, x1: float, y: float, crests: int, amp: float,
         color: str, opacity: float, width: float = 3.0, n: int = 600) -> VMobject:
    """A trill's shimmer line: exactly ``crests`` crests between ``x0`` and ``x1``.

    The crest count is the *measured onset count* of the trill, so the mark is a
    literal tally of what was played rather than a decorative squiggle. The
    envelope tapers at both ends so the line starts and stops on the staff
    instead of at an arbitrary phase.
    """
    xs = np.linspace(x0, x1, n)
    u = (xs - x0) / max(x1 - x0, 1e-6)
    env = np.sin(np.pi * u) ** 0.45
    ys = y + amp * env * np.sin(2 * np.pi * crests * u)
    m = VMobject()
    m.set_points_smoothly([np.array([a, b, 0.0]) for a, b in zip(xs, ys)])
    m.set_stroke(color, width=width, opacity=opacity)
    return m
