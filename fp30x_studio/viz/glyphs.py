r"""Every mark on the page, built from arithmetic.

No notation font, no SVG, no LilyPond. A clef here is a *centre line* and a
*width profile*; the shape you see is the region swept by a pen of varying
width travelling along that line. That is how the marks were actually made in
1840, and it is the only construction that gets the thick/thin contrast right.
Manim's stroke is uniform-width, so a clef drawn as a stroked path reads as
wire, not as engraving -- the first attempt did exactly that and looked like a
bent paperclip. :func:`ribbon` is the fix: offset the centre line by
:math:`\pm w(s)/2` along the normal and *fill* the resulting closed region.

The centre lines
----------------
The treble clef is one continuous stroke with exactly two free ends, and
knowing which two is the whole battle. They are **the eye** (the centre of the
spiral, which sits on the G4 line) and **the tail** (below the staff). The
stroke is written eye-first: spiral outward clockwise for about 1.3 turns,
rise up the left side, arc over the top (the crook), come down as the stem --
crossing the loop twice, which is why the glyph reads as woven rather than
drawn -- and hook left into the tail. An earlier draft gave it three free ends
(eye, crook tip, tail); that is topologically impossible for a single arc and
it looked wrong in a way that was hard to name until the count was done.

The spiral is a logarithmic spiral :math:`r=r_0e^{k\psi}` whose *centre drifts*
from the eye outward as it unwinds. A fixed centre gives a mechanically even
coil -- correct, and obviously machine-made. The drift is what makes the outer
loop hang low and left the way a written one does.

The bass clef needs no spiral. It is a comma: a hairline tail sweeping up from
the lower left, thickening monotonically into a round head that curls over the
top and tucks under itself, plus two dots straddling the F3 line.

Accidentals are the easy case and are built as plain polygons: two near-hairline
verticals crossed by two thick slanted bars.

Sizes are in **staff spaces** throughout -- the distance between adjacent staff
lines -- so a glyph is scaled once, by :mod:`.staff`, and never re-tuned.
"""
from __future__ import annotations

import numpy as np
from manim import BLACK, VGroup, VMobject


# ---------------------------------------------------------------- pen machinery

def _smooth_path(anchors: np.ndarray, n: int) -> np.ndarray:
    """Resample a smooth curve through ``anchors`` at ``n`` equal-arclength points.

    We borrow manim's own cubic smoothing rather than writing another
    Catmull-Rom: it is the same interpolation the rest of the library uses, so a
    glyph and a hand-authored path never disagree about what "smooth" means.
    """
    guide = VMobject()
    guide.set_points_smoothly([np.array([x, y, 0.0]) for x, y in anchors])
    ts = np.linspace(0.0, 1.0, n)
    return np.array([guide.point_from_proportion(float(t)) for t in ts])


def _width_profile(stops: list[tuple[float, float]], n: int) -> np.ndarray:
    """Piecewise-linear pen width over normalised arclength."""
    s = np.array([p[0] for p in stops])
    w = np.array([p[1] for p in stops])
    return np.interp(np.linspace(0.0, 1.0, n), s, w)


def ribbon(centre: np.ndarray, widths: np.ndarray, color: str = "#FFFFFF",
           opacity: float = 1.0) -> VMobject:
    """The region swept by a pen of width ``widths[i]`` at ``centre[i]``.

    Normals come from central differences on the centre line. Degenerate
    tangents (a repeated sample) would give a zero normal and a spike in the
    outline, so they inherit the previous normal instead of being normalised to
    NaN -- that spike is what a taper-to-zero end used to produce.
    """
    d = np.gradient(centre[:, :2], axis=0)
    norms = np.linalg.norm(d, axis=1)
    last = np.array([1.0, 0.0])
    unit = np.empty_like(d)
    for i, (v, m) in enumerate(zip(d, norms)):
        if m > 1e-9:
            last = v / m
        unit[i] = last
    normal = np.stack([-unit[:, 1], unit[:, 0]], axis=1)
    half = (widths / 2.0)[:, None]
    left = centre[:, :2] + normal * half
    right = centre[:, :2] - normal * half
    loop = np.vstack([left, right[::-1], left[:1]])
    pts = np.hstack([loop, np.zeros((len(loop), 1))])

    m = VMobject()
    m.set_points_as_corners([np.array(p) for p in pts])
    # A hairline stroke in the fill colour: without it the polygon's edge
    # anti-aliases against black and the glyph looks a half-pixel too thin.
    m.set_fill(color, opacity=opacity).set_stroke(color, width=0.7, opacity=opacity)
    return m


def _disc(cx: float, cy: float, r: float, color: str = "#FFFFFF",
          opacity: float = 1.0, n: int = 48) -> VMobject:
    th = np.linspace(0, 2 * np.pi, n)
    pts = np.stack([cx + r * np.cos(th), cy + r * np.sin(th), np.zeros_like(th)], axis=1)
    m = VMobject()
    m.set_points_as_corners([np.array(p) for p in pts])
    m.set_fill(color, opacity=opacity).set_stroke(color, width=0.7, opacity=opacity)
    return m


# ------------------------------------------------------------------ treble clef

#: Spiral: 1.30 turns, ending at 147 deg (upper left) so the stroke leaves the
#: loop heading up-and-right, straight into the rise toward the crook.
_G_TURNS = 1.30
_G_PHI_END = np.deg2rad(150.0)
_G_CENTRE_OUT = np.array([0.30, 0.12])

#: Radius against fraction-of-the-way-out, interpolated in **log r**.
#:
#: A single logarithmic spiral is the obvious model and it is wrong here. Fit it
#: to a 0.055-space eye and a 1.3-space outer loop over 1.3 turns and the radius
#: at the *right-hand* crossing comes out around 0.3 spaces -- so the loop never
#: reaches past the stem and the glyph closes into a plain oval with a dot in it,
#: which is exactly what the first two drafts rendered. The double crossing of
#: the stem is the G clef's signature, so the radius law is pinned to produce it:
#: a fast tight coil for the first third, then a slow wide sweep.
_G_RADIUS = [(0.00, 0.05), (0.30, 0.24), (0.48, 0.70),
             (0.62, 1.02), (0.80, 1.30), (1.00, 1.45)]


def _treble_spiral(n: int = 200) -> np.ndarray:
    psi_max = _G_TURNS * 2 * np.pi
    psi = np.linspace(0.0, psi_max, n)
    u = psi / psi_max
    phi = _G_PHI_END + (psi_max - psi)          # phi decreases: clockwise
    r = np.exp(np.interp(u, [a for a, _ in _G_RADIUS],
                         [np.log(b) for _, b in _G_RADIUS]))
    c = _G_CENTRE_OUT[None, :] * (u ** 1.3)[:, None]
    x = c[:, 0] + 1.20 * r * np.cos(phi)
    y = c[:, 1] + 1.12 * r * np.sin(phi)        # loop is slightly taller than wide
    return np.stack([x, y], axis=1)


def treble_clef(color: str = "#FFFFFF", opacity: float = 1.0) -> VGroup:
    """G clef, one space unit = 1.0, origin on the G4 line at the eye."""
    anchors = np.vstack([
        _treble_spiral(),
        np.array([
            [-1.42, 1.95],      # rise up the left side
            [-1.28, 3.00],
            [-0.72, 3.72],      # into the crook
            [0.05, 3.86],       # apex
            [0.56, 3.30],
            [0.56, 2.50],       # crook closes onto the stem
            [0.48, 1.10],       # stem: leans very slightly left on the way down
            [0.44, -0.45],
            [0.38, -1.80],
            [0.18, -2.42],      # tail hooks
            [-0.22, -2.70],
            [-0.58, -2.62],
        ]),
    ])
    n = 700
    centre = _smooth_path(anchors, n)
    widths = _width_profile([
        (0.00, 0.04),   # the eye: a point
        (0.06, 0.13),
        (0.20, 0.30),
        (0.34, 0.36),   # thickest on the low, left sweep of the loop
        (0.46, 0.24),
        (0.58, 0.13),
        (0.68, 0.10),   # hairline over the crook, as a nib held at an angle gives
        (0.78, 0.19),
        (0.86, 0.23),   # stem
        (0.95, 0.19),
        (1.00, 0.09),
    ], n)
    stroke = ribbon(centre, widths, color, opacity)
    dot = _disc(-0.62, -2.63, 0.26, color, opacity)
    return VGroup(stroke, dot)


# -------------------------------------------------------------------- bass clef

def bass_clef(color: str = "#FFFFFF", opacity: float = 1.0) -> VGroup:
    """F clef, one space unit = 1.0, origin on the F3 line at the head's centre."""
    anchors = np.array([
        [-1.22, -2.62],   # hairline tail, bottom left
        [-0.72, -2.05],
        [-0.10, -1.30],
        [0.42, -0.55],
        [0.63, 0.18],
        [0.34, 0.82],     # over the top of the head
        [-0.30, 0.96],
        [-0.78, 0.55],
        [-0.80, 0.02],
        [-0.52, -0.36],   # tucks under itself
    ])
    n = 420
    centre = _smooth_path(anchors, n)
    widths = _width_profile([
        (0.00, 0.03),
        (0.18, 0.14),
        (0.38, 0.36),
        (0.52, 0.54),     # the head is the pen at its widest, not a separate blob
        (0.68, 0.58),
        (0.82, 0.46),
        (0.92, 0.30),
        (1.00, 0.14),
    ], n)
    return VGroup(
        ribbon(centre, widths, color, opacity),
        _disc(1.06, 0.52, 0.155, color, opacity),
        _disc(1.06, -0.52, 0.155, color, opacity),
    )


# ------------------------------------------------------------------ accidentals

def _bar(x0: float, y0: float, x1: float, y1: float, t: float,
         color: str, opacity: float) -> VMobject:
    """A thick slanted bar: the parallelogram between two horizontal edges."""
    pts = [(x0, y0), (x1, y1), (x1, y1 - t), (x0, y0 - t), (x0, y0)]
    m = VMobject()
    m.set_points_as_corners([np.array([px, py, 0.0]) for px, py in pts])
    m.set_fill(color, opacity=opacity).set_stroke(color, width=0.7, opacity=opacity)
    return m


def _vert(x: float, y0: float, y1: float, w: float,
          color: str, opacity: float) -> VMobject:
    pts = [(x - w / 2, y0), (x + w / 2, y0), (x + w / 2, y1), (x - w / 2, y1), (x - w / 2, y0)]
    m = VMobject()
    m.set_points_as_corners([np.array([px, py, 0.0]) for px, py in pts])
    m.set_fill(color, opacity=opacity).set_stroke(color, width=0.7, opacity=opacity)
    return m


def sharp(color: str = "#FFFFFF", opacity: float = 1.0) -> VGroup:
    """Sharp sign, origin at the centre of the notehead it governs."""
    return VGroup(
        _vert(-0.155, -0.78, 0.86, 0.062, color, opacity),
        _vert(0.155, -0.86, 0.78, 0.062, color, opacity),
        _bar(-0.34, -0.12, 0.34, 0.10, 0.175, color, opacity),
        _bar(-0.34, 0.34, 0.34, 0.56, 0.175, color, opacity),
    )


def natural(color: str = "#FFFFFF", opacity: float = 1.0) -> VGroup:
    """Natural sign. Same construction as the sharp; the verticals differ."""
    return VGroup(
        _vert(-0.145, -0.52, 0.88, 0.062, color, opacity),
        _vert(0.145, -0.88, 0.52, 0.062, color, opacity),
        _bar(-0.145, -0.10, 0.145, 0.06, 0.155, color, opacity),
        _bar(-0.145, 0.32, 0.145, 0.48, 0.155, color, opacity),
    )


# -------------------------------------------------------------------- noteheads

_HEAD_TILT = np.deg2rad(21.0)      # noteheads lean up to the right


def _ellipse(rx: float, ry: float, tilt: float, color: str, opacity: float,
             n: int = 64) -> VMobject:
    th = np.linspace(0, 2 * np.pi, n)
    x, y = rx * np.cos(th), ry * np.sin(th)
    xr = x * np.cos(tilt) - y * np.sin(tilt)
    yr = x * np.sin(tilt) + y * np.cos(tilt)
    m = VMobject()
    m.set_points_as_corners([np.array([a, b, 0.0]) for a, b in zip(xr, yr)])
    m.set_fill(color, opacity=opacity).set_stroke(color, width=0.7, opacity=opacity)
    return m


def notehead(open_head: bool = False, color: str = "#FFFFFF",
             opacity: float = 1.0, scale: float = 1.0) -> VGroup:
    """A notehead, one space unit = 1.0, origin at its centre.

    ``open_head`` hollows it out by stacking a black ellipse on top rather than
    building a true subpath hole. The scene's ground is black and never anything
    else, so the cheap version is indistinguishable and costs one mobject.
    """
    outer = _ellipse(0.62 * scale, 0.40 * scale, _HEAD_TILT, color, opacity)
    if not open_head:
        return VGroup(outer)
    inner = _ellipse(0.42 * scale, 0.185 * scale, _HEAD_TILT, BLACK, opacity)
    return VGroup(outer, inner)


#: Public alias: the scene needs plain filled discs for glows and paper flecks.
disc = _disc


def stem(up: bool, length: float = 3.4, color: str = "#FFFFFF",
         opacity: float = 1.0) -> VMobject:
    """Stem, attached to a notehead whose centre is the origin.

    Right side going up, left side going down -- the convention, and the reason
    the offsets are asymmetric about zero.
    """
    x = 0.58 if up else -0.58
    y0, y1 = (0.0, length) if up else (0.0, -length)
    return _vert(x, y0, y1, 0.115, color, opacity)
