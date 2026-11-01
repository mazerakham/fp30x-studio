r"""The scene: black screen, title, camera, staff, and the notes as they fell.

Everything on screen is a function of one number
--------------------------------------------------
There is exactly one manim animation in this scene: a clock running from 0 to
:data:`RUN_TIME` at unit rate. Title opacity, camera position, how much of each
staff line has been drawn, every notehead, every glow, and the black overlay are
all computed from that clock by :meth:`Nocturne._frame`.

That is a deliberate rejection of the obvious structure, which is a chain of
``self.play`` calls. The reason is the audio. The visual clock and the take's
own clock must stay in exact correspondence for the whole run -- a note is drawn
at the instant its key went down, not on a beat -- and a chain of animations
accumulates a frame of slop at every join. Composed animations also make
overlapping phases (the clefs arriving while the camera is still moving) into a
bookkeeping problem instead of two independent functions of ``t``.

The cost is that state changes have to be idempotent and cheap: ``_frame`` runs
sixty times a second over every note, so each note record tracks whether it has
already settled and does nothing once it has.

The camera is the story
-----------------------
The title does not slide up. The camera descends, from ``y = TITLE_Y`` to the
staff at ``y = 0``, and the title -- fixed in the world -- leaves through the top
as a consequence. Nothing on screen would distinguish those two readings, which
is what the background is for: a sparse field of paper flecks and a few very
faint laid lines, fixed in the world, moving with everything else. They are at
3-6% opacity and about one fleck per three and a half square units. Denser or brighter and
it stops being paper and becomes a starfield, which is the failure mode this
whole element is one step away from.

During the music the camera pans horizontally instead, holding the write-head
about two thirds of the way across the frame.

What the colours mean
---------------------
Nothing here is decoration; each channel carries a measurement.

* **Velocity -> ink and flash.** A notehead's fill opacity is
  :math:`0.45+0.55\,v/127`. On its attack it throws a glow whose peak radius is
  :math:`(0.7+1.6\,v/127)` noteheads and whose peak opacity is
  :math:`0.20+0.40\,v/127`.
* **Velocity -> colour temperature of the flash only**, cool blue-white at
  ``v=1`` to warm amber at ``v=127``. The notation itself stays a fixed warm
  white, so the page still reads as engraving and the energy reads as light on
  top of it.
* **Onset rate -> the trill's gold.** 8 onsets/s is a dull gold, 16 a hot one.
* **Duration -> notehead shape**, hollow at or above :data:`OPEN_HEAD_S`, filled
  below. This is the one mapping that is a *convention* rather than a
  measurement dressed up as one, and it is defensible only because there is no
  metre here to make a real half-note out of.

Trills carry three layers at once, which is how "show me all the notes" and
"keep it legible" are both satisfied. The principal notehead is solid and full
size. Every individual onset is drawn at 34% size and 38% opacity at its own
position and its own time, so nothing played is discarded. Over them runs a
shimmer with **exactly one crest per onset** -- the mark is a tally, and you can
count 26 crests in the first trill because 26 notes were played.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from manim import (BLACK, ITALIC, ORIGIN, Line, ManimColor, MovingCameraScene,
                   Rectangle, Text, VGroup, VMobject, ValueTracker,
                   interpolate_color, linear)

from . import glyphs, perf
from .staff import GrandStaff, spell, step_of, wavy

# ----------------------------------------------------------------- the material

TAKE = Path.home() / "Music/FP-30X Studio/takes/2026-08-21-nocturne.fp30"

#: The window, in take seconds. Chosen because it holds both cases in one
#: phrase: an ordinary melodic ascent to C7 and back (347.0-349.4) running
#: straight into the descending chain of trills of the reprise (349.4-353.5).
TAKE_T0, TAKE_T1 = 347.0, 353.5

TITLE = "Nocturne in B major"
SUBTITLE = "Op. 62 Nº 1"

#: Which key signature to engrave, and to spell every note against. A
#: hardcoded key renders a passage in another key with a natural on every note
#: to cancel an accidental that should never have been printed. See
#: staff.KEY_SIGNATURES.
KEY = "B"


def configure(take=None, t0=None, t1=None, title=None, subtitle=None, key=None):
    """Point the scene at a different take, window or piece.

    **This exists because a scene that reads the constants below directly,
    while `render.py` forwards `--take`/`--window` only to the AUDIO half,**
    renders the audio of one take under the notation of another and reports
    success. Nothing errors, because both halves are individually valid; the
    CLI accepts arguments it silently ignores.

    So the window is no longer a constant that a caller may or may not have
    honoured; it is set here, once, and every derived quantity is recomputed
    from it. `RUN_TIME` follows the window length rather than the window being
    required to be exactly 6.5 s, which is also what makes a longer clip
    possible.
    """
    global TAKE, TAKE_T0, TAKE_T1, TITLE, SUBTITLE, RUN_TIME, FADE_OUT, KEY
    if take is not None:
        TAKE = Path(take)
    if t0 is not None:
        TAKE_T0 = float(t0)
    if t1 is not None:
        TAKE_T1 = float(t1)
    if title is not None:
        TITLE = title
    if subtitle is not None:
        SUBTITLE = subtitle
    if key is not None:
        from .staff import KEY_SIGNATURES
        if key not in KEY_SIGNATURES:
            raise ValueError(f"no key signature for {key!r}; "
                             f"have {sorted(KEY_SIGNATURES)}")
        KEY = key
    # The music occupies the window; the head is the title and staff build.
    RUN_TIME = MUSIC_START + (TAKE_T1 - TAKE_T0)
    FADE_OUT = (RUN_TIME - 0.70, RUN_TIME)
    return {"take": str(TAKE), "t0": TAKE_T0, "t1": TAKE_T1,
            "run_time": RUN_TIME, "title": TITLE, "subtitle": SUBTITLE,
            "key": KEY}
TITLE_FONT = "Didot"          # 1780s Parisian neoclassical: right for 1846.

# --------------------------------------------------------------------- timeline

RUN_TIME = 10.0
FADE_IN = (0.00, 0.70)
TITLE_IN = (0.40, 1.65)
CAM_MOVE = (1.40, 2.95)
CLEFS_IN = (2.40, 3.05)
STAFF_DRAW = (2.80, 3.45)
KEYSIG_IN = (3.05, 3.45)
MUSIC_START = 3.50            # RUN_TIME - MUSIC_START must equal TAKE_T1 - TAKE_T0
FADE_OUT = (9.30, 10.00)

# ----------------------------------------------------------------------- layout

SPACE = 0.30                  # staff space, manim units
PPS = 3.0                     # manim units per second of music
NOTES_X0 = -2.3               # x of the first onset in the window
# The clefs need real air on the left. At -6.5 the G clef's outer loop
# came within a sixth of a space of the frame edge and read as cropped.
CLEF_X = -6.15
KEYSIG_X = -5.05
HEAD_OFFSET = 4.6             # write-head sits this far right of the camera centre
TITLE_Y = 6.0

INK = "#F4F1EA"
#: Stems are drawn a shade down from the noteheads. At equal weight they win
#: the frame -- with onsets spaced by real time there is no beaming to group
#: them, so forty upright bars read as the subject and the notes as noise.
STEM_INK = "#B5B0A6"
STAFF_GREY = "#7A828E"
# interpolate_color needs ManimColor, not a hex string -- it fails with an
# AttributeError on str, which is easy to read as a manim bug and is not.
COOL = ManimColor("#AFC6E9")
WARM = ManimColor("#FFC46B")
TRILL_DULL = ManimColor("#B08D2E")
TRILL_HOT = ManimColor("#FFE9A0")

OPEN_HEAD_S = 0.45


def _smoothstep(a: float, b: float, t: float) -> float:
    if b <= a:
        return 1.0 if t >= b else 0.0
    u = min(max((t - a) / (b - a), 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


def _glow(radius: float, color: str, rings: int = 4) -> VGroup:
    """A fake radial gradient: concentric discs with quadratic falloff.

    Manim has no radial fill, and a shader for one is not worth a ten-second
    render. Four rings is the point where a fifth stops being visible at 1080p.
    """
    g = VGroup()
    for i in range(rings):
        f = 1.0 - i / rings
        g.add(glyphs.disc(0.0, 0.0, radius * (0.30 + 0.70 * f), color, 1.0))
    return g


@dataclass
class _NoteRec:
    """One drawable whose whole life is a function of the clock."""

    t_on: float                 # video seconds
    body: VGroup
    head: VMobject
    glow: VGroup
    rings: list = field(default_factory=list)   # (ring, its points at full size)
    centre: np.ndarray = field(default_factory=lambda: np.zeros(3))
    base_opacity: float = 1.0
    glow_peak: float = 0.5
    grow: float = 0.10          # seconds for the notehead to settle
    flash: float = 0.38         # seconds for the glow to die
    state: int = -1             # -1 unseen, 0 animating, 1 settled


class Nocturne(MovingCameraScene):
    """Ten seconds: title, descent, staff, and 6.5 s of the reprise."""

    def construct(self) -> None:
        self.camera.background_color = BLACK
        gs = GrandStaff(space=SPACE, x_left=-7.6, x_right=19.5)
        self.gs = gs
        self._records: list[_NoteRec] = []
        self._trill_marks: list[tuple] = []

        # The driver goes in FIRST, and that ordering is load-bearing.
        # Manim's Cairo renderer bakes every mobject that precedes the first
        # one carrying an updater into a cached static image and stops
        # redrawing it. Added last, the driver left the entire scene frozen at
        # its t=0 state -- which is a black overlay over invisible everything,
        # so the render came out ten seconds of solid black with no error.
        clock = ValueTracker(0.0)
        driver = VMobject()
        driver.add_updater(lambda _m: self._frame(clock.get_value()))
        self.add(driver)

        notes, _pedal = perf.load_window(TAKE, TAKE_T0, TAKE_T1)
        trills, in_trill = perf.detect_trills(notes)

        self._background()
        self._title()
        self._staff(gs)
        self._plain_notes(gs, [n for n in notes if id(n) not in in_trill])
        for tr in trills:
            self._trill(gs, tr)

        self.overlay = Rectangle(width=16.0, height=9.5, fill_color=BLACK,
                                 fill_opacity=1.0, stroke_width=0)
        self.add(self.overlay)

        self.camera.frame.move_to([0.0, TITLE_Y, 0.0])
        self._frame(0.0)
        self.play(clock.animate.set_value(RUN_TIME), run_time=RUN_TIME, rate_func=linear)

    # ------------------------------------------------------------------ pieces

    def _background(self) -> None:
        """Paper: sparse flecks and a few laid lines. Seeded, so it is the same
        page every render -- an unseeded field makes two renders of the same
        take impossible to compare."""
        rng = np.random.default_rng(6201)
        g = VGroup()
        for _ in range(150):
            x = rng.uniform(-10.0, 21.0)
            y = rng.uniform(-5.0, 12.0)
            r = rng.uniform(0.012, 0.032)
            g.add(glyphs.disc(x, y, r, "#CDBEA6", float(rng.uniform(0.03, 0.065))))
        for y in np.arange(-4.5, 12.0, 0.92):
            g.add(Line([-10.0, y, 0], [21.0, y, 0], stroke_width=1.0,
                       color="#8A7C68", stroke_opacity=0.055))
        self.add(g)

    def _title(self) -> None:
        self.title = VGroup(
            Text(TITLE, font=TITLE_FONT, font_size=58, color=INK),
            Text(SUBTITLE, font=TITLE_FONT, font_size=30, color=INK,
                 slant=ITALIC).set_opacity(0.82),
        ).arrange(direction=np.array([0.0, -1.0, 0.0]), buff=0.42)
        self.title.move_to([0.0, TITLE_Y, 0.0])
        self.title.set_opacity(0.0)
        self.add(self.title)

    def _staff(self, gs: GrandStaff) -> None:
        self.lines = gs.staff_lines(STAFF_GREY, 0.6)
        self._line_ends = [(m.get_start().copy(), m.get_end().copy()) for m in self.lines]
        self.add(self.lines)
        self.clefs = gs.clefs(CLEF_X, INK).set_opacity(0.0)
        self.keysig = gs.key_signature(KEYSIG_X, INK, KEY).set_opacity(0.0)
        self.add(self.clefs, self.keysig)

    # --------------------------------------------------------------- the notes

    def _x(self, take_t: float) -> float:
        return NOTES_X0 + PPS * (take_t - TAKE_T0)

    def _video_t(self, take_t: float) -> float:
        return MUSIC_START + (take_t - TAKE_T0)

    def _stem_up(self, step: int) -> bool:
        if step >= step_of("C", 4):
            return step < step_of("B", 4)
        return step < step_of("D", 3)

    def _one_note(self, gs: GrandStaff, n: perf.Note, *, scale: float = 1.0,
                  opacity: float | None = None, with_stem: bool = True,
                  with_acc: bool = True, glow_color=None,
                  glow_boost: float = 1.0) -> _NoteRec:
        step, acc = spell(n.pitch, KEY)
        x, y = self._x(n.t), gs.y(step)
        v = n.velocity / 127.0
        base = 0.45 + 0.55 * v if opacity is None else opacity

        head = glyphs.notehead(n.duration >= OPEN_HEAD_S and scale > 0.9,
                               INK, 1.0, scale).scale(gs.space, about_point=ORIGIN)
        head.shift([x, y, 0])
        body = VGroup(head)
        if with_stem and scale > 0.9:
            st = glyphs.stem(self._stem_up(step), 2.7, STEM_INK, 1.0).scale(
                gs.space, about_point=ORIGIN)
            st.shift([x, y, 0])
            body.add(st)
        if with_acc and acc and scale > 0.9:
            a = gs.accidental(acc, step, x, INK, 1.0)
            if a is not None:
                body.add(a)
        if scale > 0.9:
            body.add(gs.ledger_lines(step, x, STAFF_GREY, 0.8))

        colour = glow_color if glow_color is not None else interpolate_color(COOL, WARM, v)
        glow = _glow((0.7 + 1.6 * v) * 0.62 * gs.space * scale * glow_boost, colour)
        glow.shift([x, y, 0])
        # Born invisible. _advance only *changes* state, so a record whose
        # initial state matched its "unseen" flag was never touched and every
        # note in the window showed up on frame one, glow and all.
        body.set_opacity(0.0)
        glow.set_opacity(0.0)
        centre = np.array([x, y, 0.0])
        rec = _NoteRec(t_on=self._video_t(n.t), body=body, head=head, glow=glow,
                       rings=[(r, r.points.copy()) for r in glow], centre=centre,
                       base_opacity=base, glow_peak=(0.20 + 0.40 * v) * scale * glow_boost)
        self.add(glow, body)
        return rec

    def _plain_notes(self, gs: GrandStaff, notes: list[perf.Note]) -> None:
        for n in notes:
            self._records.append(self._one_note(gs, n))

    def _trill(self, gs: GrandStaff, tr: perf.Trill) -> None:
        hot = min(max((tr.rate - 8.0) / 8.0, 0.0), 1.0)
        colour = interpolate_color(TRILL_DULL, TRILL_HOT, hot)

        # Layer 1: the note a score would print.
        principal = perf.Note(t=tr.t0, t_off=tr.t1, pitch=tr.principal,
                              velocity=int(tr.mean_velocity))
        self._records.append(self._one_note(gs, principal))

        # Layer 2: every onset actually played, small and faint.
        for n in tr.notes:
            # The mini-notes take the trill's gold rather than their own
            # velocity colour, and are given back some of the glow their 34%
            # scale took away. Otherwise the one moment the eye most needs the
            # "played just now" cue -- the fast band -- is the one place it is
            # faintest, and the trill reads as pre-printed.
            self._records.append(self._one_note(gs, n, scale=0.34, opacity=0.38,
                                                with_stem=False, with_acc=False,
                                                glow_color=colour, glow_boost=2.1))

        # Layer 3: the tally. One crest per onset.
        step = spell(tr.principal, KEY)[0]
        y = gs.y(step) + 3.9 * gs.space
        x0, x1 = self._x(tr.t0), self._x(tr.t1)
        full = wavy(x0, x1, y, tr.n_onsets, 0.52 * gs.space, colour, 0.95,
                    width=2.6, n=260)
        shown = VMobject().set_stroke(colour, width=2.6, opacity=0.95)
        self.add(shown)
        mark = Text("tr", font=TITLE_FONT, font_size=30, color=colour, slant=ITALIC)
        mark.move_to([x0 + 0.10, y + 0.34, 0]).set_opacity(0.0)
        self.add(mark)
        self._trill_marks.append((full, shown, mark,
                                  self._video_t(tr.t0), self._video_t(tr.t1)))

    # -------------------------------------------------------------- the driver

    def _frame(self, t: float) -> None:
        # Camera: descend, then pan with the write-head.
        cam_y = TITLE_Y * (1.0 - _smoothstep(*CAM_MOVE, t))
        take_t = TAKE_T0 + max(t - MUSIC_START, 0.0)
        cam_x = max(0.0, self._x(take_t) - HEAD_OFFSET)
        self.camera.frame.move_to([cam_x, cam_y, 0.0])

        self.title.set_opacity(_smoothstep(*TITLE_IN, t))
        self.clefs.set_opacity(_smoothstep(*CLEFS_IN, t))
        self.keysig.set_opacity(_smoothstep(*KEYSIG_IN, t))

        draw = max(_smoothstep(*STAFF_DRAW, t), 1e-3)
        for line, (a, b) in zip(self.lines, self._line_ends):
            line.put_start_and_end_on(a, a + draw * (b - a))

        for rec in self._records:
            self._advance(rec, t)

        for full, shown, mark, ta, tb in self._trill_marks:
            f = min(max((t - ta) / max(tb - ta, 1e-3), 0.0), 1.0)
            if f <= 0.0:
                shown.set_stroke(opacity=0.0)
                mark.set_opacity(0.0)
                continue
            shown.set_stroke(opacity=0.95)
            shown.pointwise_become_partial(full, 0.0, f)
            mark.set_opacity(_smoothstep(ta, ta + 0.30, t))

        o = 1.0 - _smoothstep(*FADE_IN, t) + _smoothstep(*FADE_OUT, t)
        self.overlay.move_to(self.camera.frame.get_center())
        self.overlay.set_opacity(min(max(o, 0.0), 1.0))

    def _advance(self, rec: _NoteRec, t: float) -> None:
        """Update one note, and only when its state can actually have changed."""
        end = rec.t_on + max(rec.grow, rec.flash)
        if t < rec.t_on:
            if rec.state != -1:
                rec.body.set_opacity(0.0)
                rec.glow.set_opacity(0.0)
                rec.state = -1
            return
        if t >= end:
            if rec.state != 1:
                rec.body.set_opacity(rec.base_opacity)
                rec.glow.set_opacity(0.0)
                rec.state = 1
            return
        rec.state = 0
        g = _smoothstep(rec.t_on, rec.t_on + rec.grow, t)
        rec.body.set_opacity(rec.base_opacity * g)
        # The flash expands as it dies. The ring points are rebuilt from the
        # stored full-size copy each frame rather than scaled in place: repeated
        # in-place scaling compounds its own rounding and the glow drifts off
        # the notehead by the end of a long trill.
        f = min(max((t - rec.t_on) / rec.flash, 0.0), 1.0)
        s = 0.30 + 0.70 * f
        n = len(rec.rings)
        for i, (ring, base) in enumerate(rec.rings):
            ring.set_points(rec.centre + s * (base - rec.centre))
            k = 1.0 - i / n
            ring.set_opacity(rec.glow_peak * (1.0 - f) ** 2 * k * k)
