"""Two segments in, a homeomorphism and its diagnostics out."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .correspond import Correspondence, correspond, timing_prior
from .events import LATTICE_S, Segment
from .warp import (SweepPoint, Warp, fit_warp, gcv_lambda, lambda_sweep,
                   straight_line)

__all__ = ["Alignment", "align_segments", "DEFAULT_LAMBDAS", "l_curve_corner"]

#: Six decades, log-spaced. lambda has units of seconds: the data term is s^2
#: and the Dirichlet integral is s. The two ends are the two null models --
#: unconstrained interpolation of the correspondence, and rigid tempo scaling.
DEFAULT_LAMBDAS = tuple(float(x) for x in np.logspace(-4, 4, 41))


def l_curve_corner(sweep: list[SweepPoint]) -> int:
    r"""Index of the L-curve corner: maximum curvature of

    .. math:: \lambda \mapsto \bigl(\log\textstyle\sum_i r_i^2,\;
              \log\int(\varphi'-c)^2\bigr).

    There is no noise level to apply a discrepancy principle against here --
    the residual is *signal*, real expressive difference between two
    performances, not measurement error. (The only genuine measurement error is
    the 5 ms transport lattice, and the residual sits two orders above it at
    every useful :math:`\lambda`.) So the corner is the honest selection rule:
    the point past which buying residual costs disproportionate tempo variance.
    It is a choice, and the sweep is published beside it so the choice is
    visible rather than silent.
    """
    x = np.log(np.array([max(p.warp.data_term, 1e-300) for p in sweep]))
    y = np.log(np.array([max(p.excess_energy, 1e-300) for p in sweep]))
    if len(x) < 5:
        return len(x) // 2
    x1, y1 = np.gradient(x), np.gradient(y)
    x2, y2 = np.gradient(x1), np.gradient(y1)
    kappa = (x1 * y2 - y1 * x2) / np.power(x1 ** 2 + y1 ** 2, 1.5)
    return int(np.argmax(kappa[2:-2]) + 2)


@dataclass
class Alignment:
    """Everything the fit produced, in one object."""

    correspondence: Correspondence
    warp: Warp
    null: Warp
    sweep: list[SweepPoint]
    lam: float
    corner: int = 0
    chosen: int = 0
    #: correspondence before the timing-prior pass, kept so the refinement is
    #: auditable rather than asserted
    first_pass: Correspondence | None = None
    refined: bool = False
    #: whether GCV on the *reported* correspondence has an interior minimum.
    #: False after a refinement pass, and that is expected -- see
    #: :func:`align_segments`.
    gcv_interior: bool = True
    label_a: str = "A"
    label_b: str = "B"

    # -- headline numbers --------------------------------------------------

    @property
    def residual_rms(self) -> float:
        return self.warp.residual_rms

    @property
    def null_residual_rms(self) -> float:
        return self.null.residual_rms

    @property
    def excess_energy(self) -> float:
        r""":math:`\int(\varphi'-c)^2` -- the energy the fit spent departing
        from the straight line. Zero for the null by construction."""
        return self.warp.excess_energy

    @property
    def explained(self) -> float:
        """Fraction of the null's timing discrepancy the warp removes."""
        return 1.0 - self.residual_rms / self.null_residual_rms

    @property
    def lattice_multiple(self) -> float:
        """Residual in units of the 5 ms transport lattice."""
        return self.residual_rms / LATTICE_S

    def summary(self) -> dict:
        c = self.correspondence.census()
        k = self.warp.kinks()
        return {
            **c,
            "lam": self.lam,
            "T1": self.warp.T1,
            "T2": self.warp.T2,
            "mean_rate": self.warp.mean_rate,
            "residual_rms": self.residual_rms,
            "residual_max": self.warp.residual_max,
            "null_residual_rms": self.null_residual_rms,
            "residual_lattice_multiple": self.lattice_multiple,
            "explained": self.explained,
            "lam_gcv": self.sweep[self.chosen].lam if self.sweep else None,
            "lam_corner": self.sweep[self.corner].lam if self.sweep else None,
            "df": self.warp.df,
            "refined": self.refined,
            "matched_first_pass": len(self.first_pass.pairs) if self.first_pass else None,
            "gcv_interior": self.gcv_interior,
            "dirichlet": self.warp.dirichlet,
            "null_dirichlet": self.null.dirichlet,
            "excess_energy": self.excess_energy,
            "rate_rms": self.warp.rate_rms,
            "slope_min": float(self.warp.slopes.min()),
            "slope_max": float(self.warp.slopes.max()),
            "n_active_constraints": len(self.warp.active),
            "kkt_residual": self.warp.kkt_residual,
            "top_kinks": [
                {"t": z.t, "log_ratio": z.log_ratio, "residual": z.residual,
                 "slope_left": z.slope_left, "slope_right": z.slope_right}
                for z in k[:8]
            ],
        }


def align_segments(seg_a: Segment | tuple, seg_b: Segment | tuple, *,
                   lam: float | None = None, lambdas=DEFAULT_LAMBDAS,
                   min_slope_frac: float = 0.05, refine: bool = True,
                   label_a: str = "A", label_b: str = "B") -> Alignment:
    r"""Align two attempts, fit :math:`\varphi`, and sweep :math:`\lambda`.

    The endpoints of :math:`\varphi` are the **first and last matched onsets**,
    not the first and last note played: outside the matched core the two
    performances are not playing the same music, so there is nothing there for a
    homeomorphism to be a homeomorphism *of*. What falls outside is counted in
    :meth:`Correspondence.census`, never dropped in silence.

    Three steps, in this order, and the order is the point.

    1. **Correspondence from pitch alone.** No warp exists yet, so nothing about
       timing can have biased it.
    2. **:math:`\lambda` from GCV on that correspondence.** Chosen here, while
       the residuals are still an honest out-of-sample quantity.
    3. **One refinement pass**, in which the first-pass :math:`\varphi` breaks
       the ties pitch could not, followed by a refit *at the :math:`\lambda`
       already chosen*.

    Selecting :math:`\lambda` after the refinement would be circular -- the
    correspondence would have been chosen to suit a warp, and the residual is
    then no longer out-of-sample. It shows: on the Op. 55 pair, GCV on the
    refined correspondence has no interior minimum at all and runs to the edge
    of the grid, which is the circularity announcing itself. ``gcv_interior``
    records whether that happened. Pass ``lam=`` to override the selection, or
    ``refine=False`` to skip step 3 and keep GCV strictly self-consistent.
    """
    ea = seg_a.shifted() if isinstance(seg_a, Segment) else tuple(seg_a)
    eb = seg_b.shifted() if isinstance(seg_b, Segment) else tuple(seg_b)

    first = correspond(ea, eb)
    if len(first.pairs) < 3:
        raise ValueError("too few matched onsets to fit a warp")

    sweep0 = lambda_sweep(first.t, first.s, lambdas, min_slope_frac=min_slope_frac)
    i_gcv = gcv_lambda(sweep0)
    lam_gcv = sweep0[i_gcv].lam

    corr, refined = first, False
    if refine:
        prior = timing_prior(ea, eb, sweep0[i_gcv].warp,
                             t0=first.t[0], s0=first.s[0])
        try:
            cand = correspond(ea, eb, prior=prior)
        except ValueError:
            cand = None
        # Kept only if it does not lose pairings: a refinement must never
        # quietly shrink the evidence base it is judged on.
        if cand is not None and len(cand.pairs) >= len(first.pairs):
            corr, refined = cand, True

    t, s = corr.t, corr.s
    sweep = lambda_sweep(t, s, lambdas, min_slope_frac=min_slope_frac)
    chosen = int(np.argmin(np.abs(np.log([p.lam for p in sweep]) - np.log(lam_gcv))))
    corner = l_curve_corner(sweep)
    g = gcv_lambda(sweep)
    gcv_interior = 0 < g < len(sweep) - 1

    if lam is None:
        lam = sweep[chosen].lam
        warp = sweep[chosen].warp
    else:
        warp = fit_warp(t, s, float(lam), min_slope_frac=min_slope_frac)
    null = straight_line(warp.T1, warp.T2, warp.data_t, warp.data_s, lam=lam)
    return Alignment(correspondence=corr, warp=warp, null=null, sweep=sweep,
                     lam=float(lam), corner=corner, chosen=chosen,
                     first_pass=first, refined=refined,
                     gcv_interior=gcv_interior,
                     label_a=label_a, label_b=label_b)
