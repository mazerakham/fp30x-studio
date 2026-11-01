"""The variational problem: a time homeomorphism of least energy.

The problem
-----------
Two performances of one piece. Let the first run on :math:`[0,T_1]` and the
second on :math:`[0,T_2]`, and let :math:`(t_i, s_i)_{i=1}^{n}` be matched
onsets -- the same note-event located in each. We want an increasing
homeomorphism :math:`\\varphi:[0,T_1]\\to[0,T_2]` with
:math:`\\varphi(0)=0`, :math:`\\varphi(T_1)=T_2`, minimising

.. math::

    E[\\varphi] \\;=\\; \\sum_{i} \\bigl(\\varphi(t_i)-s_i\\bigr)^2
                 \\;+\\; \\lambda \\int_0^{T_1} |\\varphi'(t)|^2\\,dt .

The admissible class is :math:`H^1(0,T_1)` -- **first** derivative -- with the
monotonicity constraint. That choice, and not a smoother one, is the whole
point; see below.

Euler-Lagrange
--------------
Take :math:`\\psi \\in H^1_0(0,T_1)`. Then

.. math::

    \\tfrac12\\,\\delta E[\\varphi;\\psi]
      = \\sum_i \\bigl(\\varphi(t_i)-s_i\\bigr)\\psi(t_i)
        + \\lambda\\int_0^{T_1}\\varphi'\\psi' ,

so the stationarity condition, in :math:`\\mathcal{D}'(0,T_1)`, is

.. math::

    -\\lambda\\,\\varphi'' + \\sum_i \\bigl(\\varphi(t_i)-s_i\\bigr)\\,\\delta_{t_i} = 0 .

Two consequences, both used verbatim by the code below.

1. **Away from the knots** :math:`\\varphi''=0`: the minimiser is *affine on
   each gap between matched onsets*. It is piecewise linear, not a spline.
2. **At each knot** the slope jumps by exactly the residual over lambda:

   .. math::  [\\varphi']_{t_i} \\;=\\; \\frac{\\varphi(t_i)-s_i}{\\lambda}.

   A large kink is a large residual. The corner is not numerical debris, it is
   the solution, and :func:`Warp.kinks` reads the residual straight off it.

So the exact minimiser lies in the space :math:`P_1(\\mathcal{T})` of continuous
piecewise-linear functions on the mesh :math:`\\mathcal{T}=\\{0=t_0<t_1<\\dots
<t_{n+1}=T_1\\}`. **The discretisation below is therefore not an approximation.**
Galerkin on :math:`P_1(\\mathcal{T})` is exact: the trial space contains the
minimiser, so the finite-dimensional problem *is* the problem.

That paragraph is about :math:`\\int|\\varphi'|^2`, which is the default and is
the only term it holds for. ``smoothness="log-rate"`` breaks it, deliberately
and openly; the section headed *Two smoothness terms* below derives exactly how
and withdraws the claim for that case.

What the penalty actually penalises
-----------------------------------
:math:`\\int|\\varphi'|^2` is a penalty on **absolute rate**, not on rate of
change of rate -- there is no :math:`\\varphi''` in it. That sounds like the
wrong prior until you use the boundary conditions: :math:`\\int_0^{T_1}\\varphi'
= T_2` is *fixed*, so with :math:`c := T_2/T_1`,

.. math::

    \\int_0^{T_1}|\\varphi'|^2 \\;=\\; \\int_0^{T_1}\\bigl(\\varphi'-c\\bigr)^2
                                 \\;+\\; c^2 T_1 ,

the second term being a constant of the admissible class. The functional is
therefore exactly a rubber band around **uniform tempo scaling**
:math:`\\varphi_0(t)=ct`, penalising the *variance* of the local rate. It costs
nothing extra to put that variation in as a jump rather than a ramp -- which is
precisely why the corners survive, and why the null hypothesis this measures
against is the straight line.

State it plainly: this is a prior on tempo *level*, not tempo *smoothness*. An
:math:`H^2` penalty would be the latter, and would round off exactly the abrupt
phrase-boundary tempo changes that are the musically interesting content.

Two smoothness terms, and what the second one costs
--------------------------------------------------
Everything above is the ``smoothness="rate"`` case, and every word of it holds
there. ``smoothness="log-rate"`` replaces :math:`|\\varphi'|^2` with
:math:`|\\log\\varphi'|^2`, for a good reason: **tempo is multiplicative**.
Twice as fast and half as fast are the same size of departure from the null,
and the quadratic term prices them at 4 and :math:`\\tfrac14`. The log term
prices both at :math:`(\\log 2)^2`. It is the better model. It is not free, and
what follows is the bill, derived rather than asserted.

*The Euler-Lagrange equation.* With :math:`\\psi(v)=(\\log v)^2` the
stationarity condition reads

.. math::

    -\\lambda\\,\\frac{d}{dt}\\Bigl[\\frac{\\log\\varphi'}{\\varphi'}\\Bigr]
      \\;+\\; \\sum_i \\bigl(\\varphi(t_i)-s_i\\bigr)\\,\\delta_{t_i} \\;=\\; 0 .

Consequence 2 survives verbatim with the *flux* :math:`\\log\\varphi'/\\varphi'`
in place of :math:`\\varphi'`: the jump in the flux across a knot is still the
residual over :math:`\\lambda`, which is what :attr:`Warp.flux` computes and
what :meth:`Warp.kinks` reads the residual from. The tests check it for both
terms.

Consequence 1 does **not** survive. Off the knots the equation says the flux is
constant, not that :math:`\\varphi''=0` -- and :math:`u\\mapsto\\log u/u` is
two-to-one on :math:`(0,\\infty)`, rising to :math:`1/e` at :math:`u=e` and
falling back. A slope that jumps between the two roots of one flux level is
stationary. **Piecewise linearity is no longer a consequence of stationarity.**

*Convexity, and where it stops.* :math:`\\psi''(v)=2(1-\\log v)/v^2`, positive
for :math:`v<e` and negative after. Below :math:`e` the integrand is convex,
Jensen applies gap by gap, and constant slope is optimal for given knot values
-- so within that regime :math:`P_1` is exact after all and nothing has been
lost. Above :math:`e` it is concave, and the failure there is not cosmetic.

*The functional has no minimiser.* :math:`\\psi` grows sublinearly:
:math:`\\psi(v)/v\\to0`. Displacement can therefore be bought arbitrarily
cheaply by sprinting -- run at :math:`\\varphi'=1` over almost all of a gap and
put the remaining displacement :math:`D` into an interval of length
:math:`\\varepsilon` at slope :math:`D/\\varepsilon`, at a cost of
:math:`\\varepsilon(\\log(D/\\varepsilon))^2\\to0`. The convex envelope of
:math:`\\psi` on :math:`[1,\\infty)` is identically zero, so the relaxed
smoothness term is :math:`\\int_{\\{\\varphi'<1\\}}|\\log\\varphi'|^2`. Taken
literally over :math:`H^1`, this penalty charges for slowing down and nothing
at all for speeding up; the infimum is not attained, and the direct method does
not apply because the functional is not weakly lower semicontinuous.

That is not an abstraction, and it is not confined to the continuum. Hand the
*discrete* problem to a general-purpose NLP solver on a mesh with one short gap
carrying a large displacement and it finds precisely this: one slope of several
hundred, every other slope pinned to 1.00, and a lower energy than any sane
configuration. ``tests/test_align.py`` contains that example, because a
degeneracy nobody has seen gets rediscovered.

*What this module returns, then.* The local minimiser continued from the
straight line. The SQP starts at :math:`\\varphi_0`, which is the null
hypothesis anyway, and descends; it returns a genuine stationary point, with
its KKT residual measured against the true gradient exactly as before. Where
every slope came out below :math:`e` it is the minimiser over the convex
regime, and :attr:`Warp.convex_regime` reports whether that held. It is *not*
the global minimiser of :math:`E` over :math:`H^1`, and no implementation could
be, because that infimum is not attained.

**The exactness claim is therefore withdrawn for this term.** Under
``"rate"`` the :math:`P_1` solve is still the answer and not a scheme. Under
``"log-rate"`` the mesh is a real restriction doing real work -- it is what
makes the problem well-posed enough to solve at all -- and calling the result
"the minimiser" without the qualifications above would be false.

*Monotonicity, however, gets cheaper.* :math:`(\\log v)^2\\to+\\infty` as
:math:`v\\to0^+`, so a plateau of positive measure has infinite energy:
:math:`\\varphi'>0` is enforced by the integrand rather than by the constraint.
Two caveats, both checked rather than assumed. The barrier is only
log-squared, so a slope of :math:`10^{-6}` costs a finite :math:`190\\,h` and a
small :math:`\\lambda` will happily pay it. And a Newton step does not respect a
barrier it sees only through a second-order model. So :math:`\\delta>0` stays --
now as the guarantee of bi-Lipschitz continuity, and as what keeps every
iterate inside the domain where :math:`\\log\\varphi'` exists, rather than as
the only thing between the fit and a non-monotone answer.

Enforcing the homeomorphism
---------------------------
:math:`\\varphi' > 0` is a constraint, not a hope. Two routes:

* **Ramsay's reparametrisation** :math:`\\varphi'=e^{w}` makes monotonicity
  structural, at the price of turning the energy into :math:`\\int e^{2w}` --
  no longer quadratic, minimiser no longer in :math:`P_1`, and the exactness
  above is lost.
* **A constrained QP on the knot values**, which is what this module does:
  minimise over :math:`x_k=\\varphi(t_k)` subject to
  :math:`x_{k+1}-x_k \\ge \\delta h_k`, :math:`h_k=t_{k+1}-t_k`.

The QP is chosen because the objective stays quadratic and strictly convex, the
trial space still contains the exact minimiser, and the problem is tiny
(:math:`n\\sim 10^2`), so an exact primal active-set solve costs nothing and
returns a certificate -- the KKT residual is asserted, not hoped for. It also
yields something the smooth route hides: the **active set** names the intervals
where monotonicity actually binds, i.e. where the data wanted to run backwards.

The floor :math:`\\delta>0` is taken as a fraction of the mean rate :math:`c`.
With :math:`\\delta>0` strictly, :math:`\\varphi` is bi-Lipschitz and genuinely
a homeomorphism; :math:`\\delta=0` would only give a monotone surjection, which
may have plateaus and no continuous inverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["Warp", "Kink", "fit_warp", "lambda_sweep", "SweepPoint",
           "straight_line", "gcv_lambda", "SMOOTHNESS", "CONVEX_SLOPE"]


# --------------------------------------------------------------------------
# the piecewise-linear map
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Kink:
    """One corner of :math:`\\varphi`, at a matched onset."""

    t: float                #: knot time, in performance-A seconds
    jump: float             #: :math:`[\\varphi']_{t}`, right slope minus left
    slope_left: float
    slope_right: float
    residual: float         #: :math:`\\varphi(t)-s`, equal to ``lam * jump``

    @property
    def log_ratio(self) -> float:
        """:math:`\\log(\\varphi'_+/\\varphi'_-)`: the tempo step, in log units."""
        return float(np.log(self.slope_right / self.slope_left))


@dataclass(frozen=True)
class Warp:
    """A continuous piecewise-linear increasing map :math:`[0,T_1]\\to[0,T_2]`.

    ``knots`` are the breakpoints :math:`t_0=0<\\dots<t_{m}=T_1` and ``values``
    the corresponding :math:`\\varphi(t_k)`. Everything else is derived.
    """

    knots: np.ndarray
    values: np.ndarray
    lam: float
    data_t: np.ndarray      #: matched onsets in A, including the two endpoints
    data_s: np.ndarray      #: their partners in B
    min_slope: float
    active: tuple[int, ...] = ()    #: gap indices where ``phi' == min_slope``
    kkt_residual: float = 0.0
    iterations: int = 0
    #: which smoothness term was minimised: ``"rate"`` for
    #: :math:`\int|\varphi'|^2`, ``"log-rate"`` for :math:`\int|\log\varphi'|^2`.
    smoothness: str = "rate"
    #: ``log-rate`` only: whether every slope came out below :math:`e`, where
    #: the integrand is still convex. ``False`` means the returned map is a
    #: stationary point of a non-convex functional -- see the module docstring.
    convex_regime: bool = True
    #: :math:`\operatorname{tr}(W+\lambda K)^{-1}W`, the effective number of
    #: free parameters. Exact while no monotonicity constraint is active (the
    #: fit is then affine in the data); an upper bound when one is.
    df: float = float("nan")

    # -- evaluation --------------------------------------------------------

    def __call__(self, t):
        return np.interp(t, self.knots, self.values)

    @property
    def T1(self) -> float:
        return float(self.knots[-1])

    @property
    def T2(self) -> float:
        return float(self.values[-1])

    @property
    def mean_rate(self) -> float:
        """:math:`c=T_2/T_1`, the slope of the straight-line null."""
        return self.T2 / self.T1

    @property
    def gaps(self) -> np.ndarray:
        return np.diff(self.knots)

    @property
    def slopes(self) -> np.ndarray:
        """:math:`\\varphi'` on each gap. Piecewise constant, length ``m``."""
        return np.diff(self.values) / self.gaps

    def slope_at(self, t):
        """:math:`\\varphi'(t)`, right-continuous."""
        idx = np.clip(np.searchsorted(self.knots, t, side="right") - 1,
                      0, len(self.slopes) - 1)
        return self.slopes[idx]

    def inverse(self, s):
        """:math:`\\varphi^{-1}`. Exists because ``min_slope > 0``."""
        return np.interp(s, self.values, self.knots)

    # -- the two numbers the functional is made of -------------------------

    @property
    def dirichlet(self) -> float:
        r""":math:`\int_0^{T_1}|\varphi'|^2\,dt`. Always the quadratic form,
        whichever term was minimised, so the two stay comparable."""
        return float(np.sum(self.slopes ** 2 * self.gaps))

    @property
    def log_dirichlet(self) -> float:
        r""":math:`\int_0^{T_1}|\log\varphi'|^2\,dt`, the multiplicative form.

        Zero exactly on :math:`\varphi=\mathrm{id}`, and symmetric in the way
        that matters: a stretch run at :math:`2\times` and one run at
        :math:`\tfrac12\times` pay the same. That symmetry is the whole reason
        the term exists.
        """
        return float(np.sum(np.log(self.slopes) ** 2 * self.gaps))

    @property
    def excess_energy(self) -> float:
        r"""The energy *gap against the straight-line null* -- the whole of
        what the fit spent on departing from uniform tempo scaling.
        Non-negative, and zero exactly on :math:`\varphi_0`.

        For ``smoothness="rate"`` this is
        :math:`\int(\varphi'-c)^2 = \int|\varphi'|^2 - c^2T_1`, the identity
        derived in the module docstring. For ``"log-rate"`` the counterpart is
        :math:`\int|\log\varphi'|^2 - T_1(\log c)^2`: the constant slope
        :math:`c` minimises :math:`\int|\log\varphi'|^2` under the
        fixed-displacement constraint by Jensen, while :math:`\varphi'\le e`,
        so the difference is again the excess over the null.

        It dispatches, so the L-curve and the sweep measure the functional that
        was actually minimised rather than a neighbour of it.
        """
        if self.smoothness == "log-rate":
            return self.log_dirichlet - self.T1 * np.log(self.mean_rate) ** 2
        return self.dirichlet - self.mean_rate ** 2 * self.T1

    @property
    def rate_rms(self) -> float:
        r"""RMS of :math:`\varphi'-c`: the excess energy as a dimensionless
        tempo deviation, :math:`\sqrt{\text{excess}/T_1}`."""
        return float(np.sqrt(max(self.excess_energy, 0.0) / self.T1))

    @property
    def residuals(self) -> np.ndarray:
        return self(self.data_t) - self.data_s

    @property
    def residual_rms(self) -> float:
        return float(np.sqrt(np.mean(self.residuals ** 2)))

    @property
    def residual_max(self) -> float:
        return float(np.max(np.abs(self.residuals)))

    @property
    def data_term(self) -> float:
        return float(np.sum(self.residuals ** 2))

    @property
    def energy(self) -> float:
        """:math:`E[\\varphi]`, the functional that was actually minimised."""
        smooth = (self.log_dirichlet if self.smoothness == "log-rate"
                  else self.dirichlet)
        return self.data_term + self.lam * smooth

    @property
    def flux(self) -> np.ndarray:
        r"""The Euler-Lagrange flux :math:`\tfrac12\partial_v\psi(\varphi')` on
        each gap: :math:`\varphi'` for ``"rate"``,
        :math:`\log\varphi'/\varphi'` for ``"log-rate"``.

        Stationarity reads :math:`\lambda\,[\,\text{flux}\,]_{t_i} =
        \varphi(t_i)-s_i` in both cases -- the same equation with a different
        flux, which is precisely what changes when the integrand changes.
        :meth:`kinks` reads the residual off it.
        """
        v = self.slopes
        if self.smoothness == "log-rate":
            return np.log(v) / v
        return v

    # -- corners -----------------------------------------------------------

    def kinks(self) -> list[Kink]:
        """Every interior corner, largest tempo step first.

        The residual reported is :math:`\\lambda` times the jump in the
        Euler-Lagrange flux (:attr:`flux`), which by the Euler-Lagrange
        equation equals :math:`\\varphi(t_i)-s_i`; the two are cross-checked in
        the tests, for both smoothness terms.
        """
        sl, fx = self.slopes, self.flux
        out = [
            Kink(t=float(self.knots[k]), jump=float(sl[k] - sl[k - 1]),
                 slope_left=float(sl[k - 1]), slope_right=float(sl[k]),
                 residual=float(self.lam * (fx[k] - fx[k - 1])))
            for k in range(1, len(sl))
        ]
        out.sort(key=lambda z: -abs(z.log_ratio))
        return out


def straight_line(T1: float, T2: float, data_t, data_s, lam: float = 0.0,
                  smoothness: str = "rate") -> Warp:
    """The null hypothesis :math:`\\varphi_0(t)=(T_2/T_1)\\,t`."""
    knots = np.array([0.0, float(T1)])
    return Warp(knots=knots, values=np.array([0.0, float(T2)]), lam=lam,
                data_t=np.asarray(data_t, float), data_s=np.asarray(data_s, float),
                min_slope=T2 / T1, smoothness=smoothness)


# --------------------------------------------------------------------------
# the solver
# --------------------------------------------------------------------------

def _assemble(t: np.ndarray, s: np.ndarray, lam: float, w: np.ndarray):
    """Reduce :math:`E` to :math:`\\tfrac12 x^{\\mathsf T}Hx - g^{\\mathsf T}x`
    on the free (interior) knot values.

    ``t`` and ``s`` include both endpoints. The stiffness matrix ``K`` is the
    exact :math:`P_1` Dirichlet form on the mesh ``t``.
    """
    h = np.diff(t)
    n = len(t) - 2                       # interior knots = free variables
    # K_ff (tridiagonal) and the boundary coupling K_fb y_b, assembled directly.
    Kff = np.zeros((n, n))
    for i in range(n):
        Kff[i, i] = 1.0 / h[i] + 1.0 / h[i + 1]
        if i + 1 < n:
            Kff[i, i + 1] = Kff[i + 1, i] = -1.0 / h[i + 1]
    kfb = np.zeros(n)
    kfb[0] += -s[0] / h[0]               # y_0 = phi(0) = s[0]
    kfb[-1] += -s[-1] / h[-1]            # y_{n+1} = phi(T1) = s[-1]

    W = np.diag(w[1:-1])
    H = 2.0 * (W + lam * Kff)
    g = 2.0 * (W @ s[1:-1] - lam * kfb)
    return H, g, h


def _active_set_qp(H, g, C, d, x0, *, max_iter: int = 400):
    """Exact primal active-set solve of

        min  1/2 x'Hx - g'x   s.t.  Cx >= d,     H > 0,

    started from a feasible ``x0``. Returns ``(x, active, kkt, iters)``.

    Small and dense on purpose: ``n`` is the number of matched onsets, a few
    hundred at most, and a dense KKT factorisation per iteration is far cheaper
    than the cost of getting an approximate solver's tolerances wrong.
    """
    x = np.asarray(x0, float).copy()
    n = len(x)
    tol = 1e-10
    assert np.all(C @ x - d >= -1e-9), "active-set QP needs a feasible start"

    working: list[int] = []
    iters = 0
    for iters in range(1, max_iter + 1):
        q = H @ x - g                                  # gradient of the objective
        if working:
            Ca = C[working]
            k = len(working)
            KKT = np.zeros((n + k, n + k))
            KKT[:n, :n] = H
            KKT[:n, n:] = -Ca.T
            KKT[n:, :n] = Ca
            rhs = np.concatenate([-q, np.zeros(k)])
            sol = np.linalg.solve(KKT, rhs)
            p, mu = sol[:n], sol[n:]
        else:
            p, mu = np.linalg.solve(H, -q), np.zeros(0)

        if np.linalg.norm(p) <= tol * max(1.0, np.linalg.norm(x)):
            if len(mu) == 0 or np.all(mu >= -1e-9):
                break
            working.pop(int(np.argmin(mu)))
            continue

        # ratio test over the inactive constraints
        idle = [i for i in range(len(d)) if i not in working]
        alpha, blocker = 1.0, None
        for i in idle:
            cp = C[i] @ p
            if cp < -1e-14:
                a = (d[i] - C[i] @ x) / cp
                if a < alpha:
                    alpha, blocker = a, i
        x = x + alpha * p
        if blocker is not None:
            working.append(blocker)
    else:  # pragma: no cover - convergence is provable for a strictly convex QP
        raise RuntimeError("active-set QP did not converge")

    # KKT certificate: stationarity residual, measured, not assumed.
    q = H @ x - g
    if working:
        r = q - C[working].T @ mu
    else:
        r = q
    kkt = float(np.linalg.norm(r) / max(1.0, np.linalg.norm(g)))
    return x, tuple(sorted(working)), kkt, iters


SMOOTHNESS = ("rate", "log-rate")

#: Above this slope the log-rate integrand :math:`(\log v)^2` turns concave and
#: the discrete problem stops being convex. :math:`e`, exactly -- it is where
#: :math:`\partial_v^2(\log v)^2 = 2(1-\log v)/v^2` changes sign.
CONVEX_SLOPE = float(np.e)


def _smooth_terms(v: np.ndarray, h: np.ndarray, kind: str):
    r"""Value, flux and curvature of the smoothness integrand on each gap.

    Returns ``(S, G, a)`` where ``S`` is :math:`\int\psi(\varphi')`, ``G_k`` is
    :math:`\partial_{v}\bigl(h_k\psi(v_k)\bigr)/h_k \cdot h_k`-free, i.e. the
    gap flux whose difference across a knot is the gradient, and ``a_k`` is the
    second derivative of the gap's contribution with respect to a knot value.

    For :math:`\psi(v)=v^2`: ``G = 2v``, ``a = 2/h`` -- which reproduces
    :math:`2\lambda K_{ff}` and is asserted against it in the tests.
    For :math:`\psi(v)=(\log v)^2`: ``G = 2\log v/v``,
    ``a = 2(1-\log v)/(v^2 h)``, negative once :math:`v>e`.
    """
    if kind == "rate":
        return float(np.sum(h * v ** 2)), 2.0 * v, 2.0 / h
    lg = np.log(v)
    return float(np.sum(h * lg ** 2)), 2.0 * lg / v, 2.0 * (1.0 - lg) / (v ** 2 * h)


def _tridiagonal(a: np.ndarray) -> np.ndarray:
    """Assemble the interior-knot Hessian of the smoothness term from ``a``."""
    n = len(a) - 1
    K = np.zeros((n, n))
    for i in range(n):
        K[i, i] = a[i] + a[i + 1]
        if i + 1 < n:
            K[i, i + 1] = K[i + 1, i] = -a[i + 1]
    return K


def _fit_lograte(t, s, lam, w, C, d, x0, *, max_iter: int = 200,
                 tol: float = 1e-14):
    r"""Sequential QP for :math:`\int|\log\varphi'|^2`.

    The log-rate objective is smooth but **not quadratic**, so the single
    active-set solve that answers the rate problem outright becomes the inner
    step of a Newton iteration: at each iterate the objective is replaced by
    its second-order model, the same linear inequalities are carried over
    unchanged, and :func:`_active_set_qp` solves that exactly. A backtracking
    Armijo line search on the *true* energy accepts the step.

    Two safeguards, both consequences of the honest statement in the module
    docstring that this functional is not convex:

    * where a slope exceeds :math:`e` the curvature ``a_k`` is negative and the
      Newton model is not a QP at all. It is floored at a small positive value
      -- a Levenberg-style modification that keeps the model convex and the
      step a descent direction, and costs only iterations.
    * the iterate never leaves the feasible cone, so :math:`\log\varphi'` is
      always defined: both ``x`` and ``x+p`` satisfy :math:`Cx\ge d`, the set is
      convex, and the line search only ever looks between them.

    Returns ``(x, active, kkt, iters, convex)``.
    """
    h = np.diff(t)
    s_int, w_int = s[1:-1], w[1:-1]
    y0, yN = float(s[0]), float(s[-1])
    x = np.asarray(x0, float).copy()
    floor = 1e-8 * max(1.0, float(np.mean(2.0 / h)))

    def energy(z):
        v = np.diff(np.concatenate([[y0], z, [yN]])) / h
        S, _, _ = _smooth_terms(v, h, "log-rate")
        return float(np.sum(w_int * (z - s_int) ** 2) + lam * S)

    def gradient(z):
        v = np.diff(np.concatenate([[y0], z, [yN]])) / h
        _, G, a = _smooth_terms(v, h, "log-rate")
        return 2.0 * w_int * (z - s_int) + lam * (G[:-1] - G[1:]), a, v

    iters = 0
    convex = True
    for iters in range(1, max_iter + 1):
        grad, a, v = gradient(x)
        convex = bool(np.all(v <= CONVEX_SLOPE))
        H = 2.0 * np.diag(w_int) + lam * _tridiagonal(np.maximum(a, floor))
        # min 1/2 p'Hp + grad'p  s.t.  C(x+p) >= d, started from p = 0.
        p, working, _, _ = _active_set_qp(H, -grad, C, d - C @ x,
                                          np.zeros_like(x))
        e0, slope = energy(x), float(grad @ p)
        if slope >= 0.0:            # pragma: no cover - p is a descent step
            break
        step, e1 = 1.0, e0
        for _ in range(60):
            e1 = energy(x + step * p)
            if e1 <= e0 + 1e-4 * step * slope:
                break
            step *= 0.5
        else:                       # pragma: no cover - descent is guaranteed
            break
        x = x + step * p
        # Stop on lack of progress in the energy itself, not on the step length
        # or the Newton decrement: both of those scale with lambda and with the
        # curvature, so a threshold that is right at one end of the sweep is
        # wrong at the other. This one is the rounding floor of the objective,
        # which is the same at every lambda -- and reaching it is what real
        # correspondences do, one of which ran an earlier version of this loop
        # forever.
        if e0 - e1 <= tol * max(1.0, abs(e0)):
            break
    else:                           # pragma: no cover
        raise RuntimeError("log-rate SQP did not converge")

    # KKT certificate on the *true* gradient, measured rather than assumed:
    # the multipliers are recovered by least squares on the active rows.
    grad, a, v = gradient(x)
    if working:
        mu, *_ = np.linalg.lstsq(C[list(working)].T, grad, rcond=None)
        r = grad - C[list(working)].T @ mu
    else:
        r = grad
    kkt = float(np.linalg.norm(r) / max(1.0, np.linalg.norm(grad)))
    return x, tuple(sorted(working)), kkt, iters, bool(np.all(v <= CONVEX_SLOPE))


def fit_warp(t, s, lam: float, *, min_slope_frac: float = 0.05,
             weights=None, smoothness: str = "rate") -> Warp:
    """Minimise :math:`E` over increasing piecewise-linear :math:`\\varphi`.

    ``t`` and ``s`` are matched onsets, strictly increasing, **including the
    two endpoints**: :math:`t_0=0`, :math:`s_0=0` and :math:`t_{n+1}=T_1`,
    :math:`s_{n+1}=T_2` are imposed as boundary conditions, not fitted.

    ``min_slope_frac`` sets :math:`\\delta = \\rho\\,T_2/T_1`. It must be in
    :math:`(0,1)`; strict positivity is what makes the result a homeomorphism
    rather than merely monotone.

    ``smoothness`` picks the penalty: ``"rate"`` for
    :math:`\\int|\\varphi'|^2` (the default, quadratic, exact) or ``"log-rate"``
    for :math:`\\int|\\log\\varphi'|^2` (multiplicative, non-quadratic, solved
    by SQP). Read the module docstring before using the second: it is the
    better model of tempo and it costs the exactness claim.
    """
    t = np.asarray(t, dtype=float)
    s = np.asarray(s, dtype=float)
    if t.shape != s.shape or t.ndim != 1:
        raise ValueError("t and s must be 1-D of equal length")
    if len(t) < 3:
        raise ValueError("need at least one interior matched onset")
    if np.any(np.diff(t) <= 0) or np.any(np.diff(s) <= 0):
        raise ValueError("t and s must both be strictly increasing")
    if not 0.0 < min_slope_frac < 1.0:
        raise ValueError("min_slope_frac must lie in (0, 1)")
    if lam < 0:
        raise ValueError("lam must be non-negative")
    if smoothness not in SMOOTHNESS:
        raise ValueError(f"smoothness must be one of {SMOOTHNESS}")

    t = t - t[0]
    s = s - s[0]
    T1, T2 = float(t[-1]), float(s[-1])
    w = np.ones_like(t) if weights is None else np.asarray(weights, float)

    H, g, h = _assemble(t, s, lam, w)
    n = len(t) - 2
    delta = min_slope_frac * T2 / T1

    # C x >= d, one row per gap: x_{k+1} - x_k >= delta * h_k, with x_0 and
    # x_{n+1} substituted out.
    C = np.zeros((n + 1, n))
    d = delta * h.copy()
    for k in range(n + 1):
        if k > 0:
            C[k, k - 1] = -1.0
        if k < n:
            C[k, k] = 1.0
    # x_0 = 0 contributes nothing to row 0; x_{n+1} = T2 moves to the RHS of
    # row n, which reads  -x_{n-1} >= delta*h_n - T2.
    d[-1] -= T2

    x0 = (T2 / T1) * t[1:-1]                    # the straight line: feasible
    convex = True
    if smoothness == "rate":
        x, active, kkt, iters = _active_set_qp(H, g, C, d, x0)
    else:
        x, active, kkt, iters, convex = _fit_lograte(t, s, lam, w, C, d, x0)
        # The Newton model at the solution, for df below: same assembly, the
        # local curvature of the log integrand in place of the constant 2/h.
        v = np.diff(np.concatenate([[s[0]], x, [s[-1]]])) / h
        _, _, a = _smooth_terms(v, h, "log-rate")
        H = 2.0 * np.diag(w[1:-1]) + lam * _tridiagonal(np.maximum(a, 1e-12))

    # Effective degrees of freedom: the trace of the hat matrix of the map
    # data -> fitted values. H = 2(W + lam*Kff), so (W + lam*Kff)^-1 W is
    # recovered from H without reassembling. Under "log-rate" the fit is not
    # affine in the data, so this is the linearisation of that trace at the
    # solution -- the usual df of a nonlinear smoother, and stated as such
    # rather than passed off as exact.
    Wd = np.diag(w[1:-1])
    df = float(np.trace(np.linalg.solve(0.5 * H, Wd)))

    values = np.concatenate([[0.0], x, [T2]])
    # Guard the contract rather than trusting it.
    if np.any(np.diff(values) <= 0):
        raise RuntimeError("solver returned a non-increasing map")
    return Warp(knots=t, values=values, lam=float(lam), data_t=t, data_s=s,
                min_slope=delta, active=active, kkt_residual=kkt,
                iterations=iters, smoothness=smoothness,
                convex_regime=convex, df=df)


@dataclass(frozen=True)
class SweepPoint:
    lam: float
    warp: Warp
    residual_rms: float
    excess_energy: float
    rate_rms: float
    n_active: int
    df: float
    gcv: float


def lambda_sweep(t, s, lams, *, min_slope_frac: float = 0.05,
                 smoothness: str = "rate") -> list[SweepPoint]:
    r"""Fit across a range of :math:`\lambda` and return the L-curve.

    :math:`\lambda` carries units of **seconds**: the data term is
    :math:`\mathrm{s}^2` and :math:`\int|\varphi'|^2\,dt` is
    :math:`\mathrm{s}`. :math:`\lambda\to0` is unconstrained interpolation of
    the correspondence (pure DTW, kinks everywhere); :math:`\lambda\to\infty`
    is the straight line, i.e. rigid metronomic scaling.
    """
    out = []
    for lam in lams:
        w = fit_warp(t, s, float(lam), min_slope_frac=min_slope_frac,
                     smoothness=smoothness)
        n = len(w.data_t) - 2
        denom = n - w.df
        gcv = (n * w.data_term / denom ** 2) if denom > 1e-9 else float("inf")
        out.append(SweepPoint(lam=float(lam), warp=w, residual_rms=w.residual_rms,
                              excess_energy=w.excess_energy, rate_rms=w.rate_rms,
                              n_active=len(w.active), df=w.df, gcv=gcv))
    return out


def gcv_lambda(sweep: list[SweepPoint]) -> int:
    r"""Index of the generalised-cross-validation minimum.

    :math:`\mathrm{GCV}(\lambda)=n\,\mathrm{RSS}(\lambda)/(n-\mathrm{df})^2`,
    the standard rotation-invariant approximation to leave-one-out error. It
    needs no noise level, which matters here because there is no noise level to
    supply: the residual is real expressive difference between two
    performances, not measurement error, and the only genuine measurement error
    -- the 5 ms transport lattice -- sits two orders below the residual at
    every useful :math:`\lambda`.

    Caveat, stated rather than buried: GCV assumes independent errors, and
    expressive timing deviations are strongly autocorrelated, so this will lean
    towards undersmoothing. That is why the whole sweep is published beside it.
    """
    return int(np.argmin([p.gcv for p in sweep]))
