r"""The groove: a spherical Euler spiral, and the ship that reads it.

The conceit is that the Earth is a record and the player is the stylus. That
only works if the curve is the right one, and the right one is a **clothoid**:
curvature linear in arc length. Its two spiral centres become the two poles,
so "tight at the north pole, tight at the south pole, open at the equator"
is not something imposed on the curve — it is what the curve already does.

Why not just map a plane Euler spiral onto the sphere
--------------------------------------------------------
Because that is what I did first and it is wrong. The plane clothoid
:math:`(C(t), S(t))` (Fresnel integrals) is naturally unit-speed — that is the
whole point of the parameter — but pushing it onto a sphere through any
lat/lon formula distorts arc length by a factor that varies along the curve.
The ship would then have to change speed to stay on it, and **the music would
drift in tempo**, which is the one thing this construction exists to prevent.

So the curve is defined on the sphere directly, by the property we actually
want. For a unit-speed curve :math:`r(s)` on the unit sphere with tangent
:math:`T = r'`, the Darboux frame gives

.. math::

    r' = T, \qquad T' = -r + \kappa_g(s)\,(r \times T),

and we impose :math:`\kappa_g(s) = s/a^2` — geodesic curvature growing linearly
with arc length, the defining property of a clothoid, now intrinsic to the
sphere. The :math:`-r` term is the sphere's own normal curvature (a great
circle has :math:`\kappa_g = 0` and still curves); :math:`r \times T` is the
in-surface normal. Integrated with RK4, this is unit-speed **by
construction**, so:

    **arc length is time.** A ship flying at constant speed along the groove
    reads the music at its true tempo, everywhere, with no correction.

That is the difference between this and a gramophone. A record turns at
constant *angular* velocity, so the linear speed under the stylus falls as it
approaches the label and a groove of constant pitch would run slow. Here the
tightening near the poles costs the ship lateral acceleration, not tempo.

What the score's width has to be
--------------------------------
The notes must line up with the keys, so the staff spans the keyboard: 88 keys
is about 1.23 m. From orbit that subtends well under an arcsecond and cannot be
resolved by anything. It is visible only because it *emits* — which is the same
reason a star is visible while being angularly far smaller than any telescope's
resolution. The mathematician in the story who takes the thread for a curve of
infinite thinness is making the correct inference from the available evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["Groove", "spherical_clothoid", "EARTH_R_M", "KEYBOARD_W_M"]

EARTH_R_M = 6_371_000.0
#: 88 keys, A0 to C8. Sets the physical width of the staff on the ground.
KEYBOARD_W_M = 1.23


def spherical_clothoid(s_max: float = 9.0, a: float = 1.0,
                       ds: float = 2e-4) -> tuple[np.ndarray, np.ndarray]:
    r"""Integrate the unit-speed spherical clothoid outward from the equator.

    Returns ``(s, r)`` with ``s`` the arc length samples (symmetric about 0)
    and ``r`` of shape ``(len(s), 3)`` on the unit sphere. Because the
    integration is unit-speed, ``s`` *is* distance along the groove.
    """
    n = int(s_max / ds)

    def deriv(state, s):
        r, T = state[:3], state[3:]
        kg = s / (a * a)
        return np.concatenate([T, -r + kg * np.cross(r, T)])

    def march(sign):
        # ONE initial condition at s = 0, integrated both ways. Flipping T0 for
        # the backward arm as well as the step sign double-flips it and yields
        # two copies of the same arm -- which is what the first version did.
        r0 = np.array([1.0, 0.0, 0.0])
        T0 = np.array([0.0, 0.0, 1.0])
        st = np.concatenate([r0, T0])
        out = np.empty((n, 3))
        h = ds * sign
        for i in range(n):
            s = i * h
            k1 = deriv(st, s)
            k2 = deriv(st + 0.5 * h * k1, s + 0.5 * h)
            k3 = deriv(st + 0.5 * h * k2, s + 0.5 * h)
            k4 = deriv(st + h * k3, s + h)
            st = st + (h / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
            # Re-project: RK4 drifts off the sphere and off unit speed, and the
            # drift compounds over 45,000 steps. Cheap to fix, fatal to skip.
            st[:3] /= np.linalg.norm(st[:3])
            st[3:] -= np.dot(st[3:], st[:3]) * st[:3]
            st[3:] /= np.linalg.norm(st[3:])
            out[i] = st[:3]
        return out

    north = march(+1.0)
    south = march(-1.0)
    s = np.concatenate([-np.arange(n)[::-1] * ds, np.arange(n) * ds])
    r = np.vstack([south[::-1], north])
    return s, r


@dataclass
class Groove:
    """The groove, plus the frames a ship needs to fly it."""

    s: np.ndarray            #: arc length, unit-sphere units
    r: np.ndarray            #: (N,3) points on the unit sphere
    a: float = 1.0

    @property
    def lat_deg(self) -> np.ndarray:
        return np.degrees(np.arcsin(np.clip(self.r[:, 2], -1, 1)))

    def tangent(self) -> np.ndarray:
        T = np.gradient(self.r, axis=0)
        return T / np.linalg.norm(T, axis=1, keepdims=True)

    def frame(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(tangent, in-surface normal, outward radial) along the groove.

        These are the ship's axes. Rolling 180 degrees about the tangent is the
        corkscrew: it swaps which side of the staff the clefs appear on and
        mirrors them, because the ship is then reading the groove from beneath.
        """
        T = self.tangent()
        U = self.r / np.linalg.norm(self.r, axis=1, keepdims=True)
        N = np.cross(U, T)
        N /= np.linalg.norm(N, axis=1, keepdims=True)
        return T, N, U

    def geodesic_curvature(self) -> np.ndarray:
        """Measured, for checking against the :math:`s/a^2` we imposed."""
        T = self.tangent()
        dT = np.gradient(T, axis=0) / np.gradient(self.s)[:, None]
        U = self.r
        return np.einsum("ij,ij->i", dT, np.cross(U, T))

    def index_at(self, s_query: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.s, s_query).clip(0, len(self.s) - 1)

    def place(self, times: np.ndarray, s0: float, speed: float) -> np.ndarray:
        """Where a note played at ``t`` sits on the groove.

        ``speed`` is arc length per second. Constant, because arc length is
        time — see the module docstring. This is the only mapping between the
        performance and the geometry, and it is linear on purpose.
        """
        return self.r[self.index_at(s0 + speed * np.asarray(times))]

    def scale_check(self, speed: float) -> dict:
        """Physical sanity: how long is the groove, how fast do we fly it."""
        span_m = (self.s[-1] - self.s[0]) * EARTH_R_M
        return {
            "groove_length_km": span_m / 1000.0,
            "ship_speed_km_s": speed * EARTH_R_M / 1000.0,
            "seconds_of_music": (self.s[-1] - self.s[0]) / speed,
            "staff_width_m": KEYBOARD_W_M,
        }
