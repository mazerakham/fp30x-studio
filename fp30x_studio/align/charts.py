"""Inline SVG chart primitives for the alignment page.

Deliberately not a plotting library. Every figure here is a handful of paths on
a linear scale, and the page has to open from a bare ``file://`` URL in a
month's time with no network, so an inline ``<svg>`` written directly is both
smaller and more durable than any library that would have to be embedded to
draw it.

The marks follow one spec throughout: 2 px lines with round joins, hairline
solid gridlines one step off the surface, markers at least 8 px across carrying
a 2 px ring in the surface colour, and every colour taken from a CSS custom
property so light and dark are one substitution rather than two drawings.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

import numpy as np

__all__ = ["Axes", "svg_open", "fmt"]


def fmt(x: float, places: int = 2) -> str:
    s = f"{x:.{places}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


@dataclass
class Axes:
    """A linear x-y frame with hairline grid, in a fixed 1000-unit viewBox."""

    x0: float
    x1: float
    y0: float
    y1: float
    width: float = 1000.0
    height: float = 420.0
    left: float = 78.0
    right: float = 24.0
    top: float = 26.0
    bottom: float = 54.0
    parts: list[str] = field(default_factory=list)

    # -- scales ------------------------------------------------------------

    @property
    def _w(self) -> float:
        return self.width - self.left - self.right

    @property
    def _h(self) -> float:
        return self.height - self.top - self.bottom

    def sx(self, x):
        return self.left + (np.asarray(x, float) - self.x0) / (self.x1 - self.x0) * self._w

    def sy(self, y):
        return self.top + (self.y1 - np.asarray(y, float)) / (self.y1 - self.y0) * self._h

    # -- chrome ------------------------------------------------------------

    def grid(self, xticks, yticks, *, xfmt=None, yfmt=None,
             xlabel="", ylabel="", ylabel_dy=0.0):
        xfmt = xfmt or (lambda v: fmt(v))
        yfmt = yfmt or (lambda v: fmt(v))
        p = self.parts
        for v in yticks:
            y = float(self.sy(v))
            p.append(f'<line class="grid" x1="{self.left:.1f}" y1="{y:.1f}" '
                     f'x2="{self.width - self.right:.1f}" y2="{y:.1f}"/>')
            p.append(f'<text class="tick ty" x="{self.left - 10:.1f}" y="{y + 4:.1f}">'
                     f'{html.escape(yfmt(v))}</text>')
        for v in xticks:
            x = float(self.sx(v))
            p.append(f'<line class="grid" x1="{x:.1f}" y1="{self.top:.1f}" '
                     f'x2="{x:.1f}" y2="{self.height - self.bottom:.1f}"/>')
            p.append(f'<text class="tick tx" x="{x:.1f}" '
                     f'y="{self.height - self.bottom + 20:.1f}">{html.escape(xfmt(v))}</text>')
        p.append(f'<line class="axis" x1="{self.left:.1f}" '
                 f'y1="{self.height - self.bottom:.1f}" '
                 f'x2="{self.width - self.right:.1f}" '
                 f'y2="{self.height - self.bottom:.1f}"/>')
        if xlabel:
            p.append(f'<text class="axlabel" x="{self.left + self._w / 2:.1f}" '
                     f'y="{self.height - 8:.1f}">{html.escape(xlabel)}</text>')
        if ylabel:
            cy = self.top + self._h / 2 + ylabel_dy
            p.append(f'<text class="axlabel" transform="rotate(-90 14 {cy:.1f})" '
                     f'x="14" y="{cy:.1f}">{html.escape(ylabel)}</text>')
        return self

    # -- marks -------------------------------------------------------------

    def path(self, x, y, cls="s1", *, extra=""):
        xs, ys = self.sx(x), self.sy(y)
        d = "M" + " L".join(f"{a:.2f},{b:.2f}" for a, b in zip(xs, ys))
        self.parts.append(f'<path class="line {cls}" d="{d}" {extra}/>')
        return self

    def step(self, xedges, yvals, cls="s1", *, extra=""):
        """A piecewise-constant trace: the honest picture of ``phi'``."""
        pts = []
        for k, v in enumerate(yvals):
            pts.append((xedges[k], v))
            pts.append((xedges[k + 1], v))
        xs = self.sx([p[0] for p in pts])
        ys = self.sy([p[1] for p in pts])
        d = "M" + " L".join(f"{a:.2f},{b:.2f}" for a, b in zip(xs, ys))
        self.parts.append(f'<path class="line {cls}" d="{d}" {extra}/>')
        return self

    def hline(self, y, cls="ref", *, label="", dy=-8.0):
        yy = float(self.sy(y))
        self.parts.append(f'<line class="{cls}" x1="{self.left:.1f}" y1="{yy:.1f}" '
                          f'x2="{self.width - self.right:.1f}" y2="{yy:.1f}"/>')
        if label:
            self.parts.append(
                f'<text class="annot" text-anchor="end" '
                f'x="{self.width - self.right - 4:.1f}" y="{yy + dy:.1f}">'
                f'{html.escape(label)}</text>')
        return self

    def vspan(self, xa, xb, cls="span"):
        a, b = float(self.sx(xa)), float(self.sx(xb))
        self.parts.append(f'<rect class="{cls}" x="{a:.1f}" y="{self.top:.1f}" '
                          f'width="{max(b - a, 0.5):.1f}" height="{self._h:.1f}"/>')
        return self

    def stems(self, x, y, cls="s1", *, base=0.0, tips=None):
        xs, ys = self.sx(x), self.sy(y)
        yb = float(self.sy(base))
        for k, (a, b) in enumerate(zip(xs, ys)):
            self.parts.append(f'<line class="stem {cls}" x1="{a:.2f}" y1="{yb:.2f}" '
                              f'x2="{a:.2f}" y2="{b:.2f}"/>')
        for k, (a, b) in enumerate(zip(xs, ys)):
            tip = "" if tips is None else f' data-tip="{html.escape(tips[k])}"'
            self.parts.append(f'<circle class="dot {cls}" cx="{a:.2f}" cy="{b:.2f}" '
                              f'r="4"{tip}/>')
        return self

    def dots(self, x, y, cls="s1", r=4.0, tips=None):
        xs, ys = self.sx(x), self.sy(y)
        for k, (a, b) in enumerate(zip(xs, ys)):
            tip = "" if tips is None else f' data-tip="{html.escape(tips[k])}"'
            self.parts.append(f'<circle class="dot {cls}" cx="{a:.2f}" cy="{b:.2f}" '
                              f'r="{r}"{tip}/>')
        return self

    def bar(self, xa, xb, y, h, cls="s1", *, opacity=None, tip=None):
        a, b = float(self.sx(xa)), float(self.sx(xb))
        yy = float(self.sy(y))
        o = "" if opacity is None else f' opacity="{opacity}"'
        t = "" if tip is None else f' data-tip="{html.escape(tip)}"'
        self.parts.append(f'<rect class="mark {cls}" x="{a:.2f}" y="{yy - h / 2:.2f}" '
                          f'width="{max(b - a, 1.2):.2f}" height="{h:.2f}" rx="1"{o}{t}/>')
        return self

    def tick_marks(self, x, cls="tickmark", *, height=9.0):
        yb = self.height - self.bottom
        for a in self.sx(x):
            self.parts.append(f'<line class="{cls}" x1="{a:.2f}" y1="{yb:.1f}" '
                              f'x2="{a:.2f}" y2="{yb - height:.1f}"/>')
        return self

    def text(self, x, y, s, cls="annot", anchor="middle", dy=0.0):
        self.parts.append(
            f'<text class="{cls}" text-anchor="{anchor}" x="{float(self.sx(x)):.1f}" '
            f'y="{float(self.sy(y)) + dy:.1f}">{html.escape(s)}</text>')
        return self

    def raw(self, s: str):
        self.parts.append(s)
        return self

    def render(self, *, title: str, desc: str) -> str:
        return svg_open(self.width, self.height, title, desc) + \
            "".join(self.parts) + "</svg>"


def svg_open(w: float, h: float, title: str, desc: str) -> str:
    return (f'<svg class="chart" viewBox="0 0 {w:.0f} {h:.0f}" '
            f'preserveAspectRatio="xMidYMid meet" role="img" '
            f'aria-label="{html.escape(title)}">'
            f'<title>{html.escape(title)}</title>'
            f'<desc>{html.escape(desc)}</desc>')
