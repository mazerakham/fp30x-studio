"""Tests for :mod:`fp30x_studio.align`.

Two halves, as elsewhere in this repo.

The synthetic half needs no piano. It pins the mathematics: that a known
piecewise-linear warp is recovered as ``lambda -> 0``, that the straight line is
recovered as ``lambda -> infinity``, that the fitted corners satisfy the
Euler-Lagrange jump condition ``[phi'] = (phi(t_i) - s_i)/lambda`` exactly, that
the monotonicity constraint is enforced and not merely hoped for, and -- the one
that pins the *claim* rather than the code -- that the P1 discretisation is
exact, by inserting knots that carry no data and checking the answer does not
move.

The real half runs on the two Op. 55 No. 1 attempts in the 2026-08-19 session
and pins the measured numbers as regression fixtures. It skips cleanly where the
take is absent.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fp30x_studio.align import (Event, align_segments, cluster_onsets,
                                correspond, event_distance, fit_warp,
                                gcv_lambda, lambda_sweep, load_segments,
                                straight_line)


# ---------------------------------------------------------------------------
# onset clustering
# ---------------------------------------------------------------------------

def test_rolled_chord_becomes_one_event():
    """A chord arrives over BLE as a few-ms arpeggio and is one event."""
    rows = [(0.000, 60, 64), (0.005, 64, 62), (0.010, 67, 66)]
    (e,) = cluster_onsets(rows, tol=0.055)
    assert e.notes == frozenset({60, 64, 67})
    assert e.t == 0.0 and e.n == 3
    assert e.velocity == 66


def test_fast_run_does_not_chain_collapse():
    """The window is measured from the first of a run, not the previous note.

    Otherwise a scale at 30 ms per note would collapse into a single event of
    unbounded length.
    """
    rows = [(0.03 * k, 60 + k, 64) for k in range(8)]
    events = cluster_onsets(rows, tol=0.055)
    assert len(events) == 4                       # pairs, not one blob
    assert all(e.n == 2 for e in events)


def test_event_distance_is_a_similarity_not_an_identity():
    a = Event(0.0, frozenset({48, 60, 67}), 3, 64)
    assert event_distance(a, a) == 0.0
    # one note of three missing: still well inside the half-content criterion
    b = Event(0.0, frozenset({48, 60}), 2, 64)
    assert 0.0 < event_distance(a, b) < 0.5
    # nothing in common at all
    c = Event(0.0, frozenset({61}), 1, 64)
    assert event_distance(a, c) == 1.0


# ---------------------------------------------------------------------------
# the variational problem
# ---------------------------------------------------------------------------

def _known_warp(t):
    """Piecewise linear, one corner at t = 10: rates 1.3 then 0.7."""
    return np.where(t < 10.0, 1.3 * t, 13.0 + 0.7 * (t - 10.0))


def test_small_lambda_recovers_a_known_piecewise_linear_warp():
    t = np.linspace(0.0, 20.0, 41)
    s = _known_warp(t)
    w = fit_warp(t, s, 1e-6)
    assert w.residual_rms < 1e-6
    assert np.max(np.abs(w(t) - s)) < 1e-6
    big = w.kinks()[0]
    assert big.t == pytest.approx(10.0)
    assert big.slope_left == pytest.approx(1.3, abs=1e-3)
    assert big.slope_right == pytest.approx(0.7, abs=1e-3)


def test_large_lambda_recovers_the_straight_line():
    t = np.linspace(0.0, 20.0, 41)
    s = _known_warp(t)
    w = fit_warp(t, s, 1e8)
    c = w.mean_rate
    assert np.allclose(w.slopes, c, atol=1e-4)
    assert w.excess_energy == pytest.approx(0.0, abs=1e-6)
    null = straight_line(w.T1, w.T2, t, s)
    assert w.residual_rms == pytest.approx(null.residual_rms, rel=1e-4)


def test_euler_lagrange_jump_condition_holds_exactly():
    """``[phi']_{t_i} = (phi(t_i) - s_i) / lambda`` at every interior knot.

    This is the Euler-Lagrange equation itself, so it is the strongest single
    check that the assembled QP is the stated functional and not a neighbour of
    it.
    """
    rng = np.random.default_rng(20260819)
    t = np.linspace(0.0, 30.0, 42) + np.concatenate(
        [[0.0], rng.uniform(-0.2, 0.2, 40), [0.0]])
    s = np.linspace(0.0, 33.0, 42) + np.concatenate(
        [[0.0], rng.uniform(-0.3, 0.3, 40), [0.0]])
    for lam in (0.01, 1.0, 100.0):
        w = fit_warp(t, s, lam)
        # The identity is the *unconstrained* stationarity condition. Where a
        # monotonicity multiplier is active it picks up that multiplier too, so
        # the test asserts the constraint is idle before asserting the jump.
        assert not w.active
        jumps = np.diff(w.slopes)
        assert np.allclose(lam * jumps, w.residuals[1:-1], atol=1e-9)


def test_the_p1_discretisation_is_exact_not_approximate():
    """Knots carrying no data must come out collinear.

    The Euler-Lagrange equation says ``phi'' = 0`` away from the data, so the
    minimiser over all of H^1 already lies in P1 on the mesh of matched onsets.
    Refining the mesh with zero-weight knots therefore cannot change the answer
    -- if it did, the solve would be a numerical scheme rather than the exact
    minimiser, which is the module's central claim.
    """
    t = np.linspace(0.0, 20.0, 21)
    s = _known_warp(t)
    lam = 0.7
    base = fit_warp(t, s, lam)

    mid = 0.5 * (t[:-1] + t[1:])
    t_aug = np.sort(np.concatenate([t, mid]))
    s_aug = np.interp(t_aug, t, s)
    weights = np.where(np.isin(t_aug, t), 1.0, 0.0)
    aug = fit_warp(t_aug, s_aug, lam, weights=weights)

    assert np.allclose(aug(t), base(t), atol=1e-9)
    probe = np.linspace(0.0, 20.0, 501)
    assert np.allclose(aug(probe), base(probe), atol=1e-9)


def test_monotonicity_is_enforced_where_the_data_would_break_it():
    """A near-plateau in the data must be lifted to the floor, not accepted."""
    t = np.array([0.0, 1.0, 2.0, 3.0])
    s = np.array([0.0, 0.99, 1.00, 3.0])
    w = fit_warp(t, s, 1e-6, min_slope_frac=0.5)
    assert w.active, "the constraint should be active on this data"
    assert w.slopes.min() >= w.min_slope - 1e-9
    assert np.all(np.diff(w.values) > 0)
    assert w.kkt_residual < 1e-8


def test_the_fit_is_a_homeomorphism_and_inverts():
    t = np.linspace(0.0, 20.0, 41)
    s = _known_warp(t)
    w = fit_warp(t, s, 0.5)
    assert w.slopes.min() > 0.0
    probe = np.linspace(0.0, w.T1, 200)
    assert np.allclose(w.inverse(w(probe)), probe, atol=1e-9)


def test_excess_energy_is_the_gap_against_the_straight_line():
    """``int (phi' - c)^2 = int |phi'|^2 - c^2 T_1``, the identity the whole
    reading of the penalty rests on."""
    t = np.linspace(0.0, 20.0, 41)
    s = _known_warp(t)
    w = fit_warp(t, s, 0.3)
    null = straight_line(w.T1, w.T2, t, s)
    assert w.excess_energy == pytest.approx(w.dirichlet - null.dirichlet, rel=1e-12)
    assert w.excess_energy >= 0.0
    assert w.rate_rms == pytest.approx(np.sqrt(w.excess_energy / w.T1))


def test_sweep_is_monotone_in_both_terms():
    """Residual rises and energy falls with lambda: the L-curve is a curve."""
    t = np.linspace(0.0, 20.0, 41)
    s = _known_warp(t)
    sweep = lambda_sweep(t, s, np.logspace(-3, 3, 25))
    rms = [p.residual_rms for p in sweep]
    exc = [p.excess_energy for p in sweep]
    assert all(x <= y + 1e-12 for x, y in zip(rms, rms[1:]))
    assert all(x >= y - 1e-12 for x, y in zip(exc, exc[1:]))
    dfs = [p.df for p in sweep]
    assert all(x >= y - 1e-9 for x, y in zip(dfs, dfs[1:]))


def test_fit_rejects_data_it_cannot_honour():
    with pytest.raises(ValueError):
        fit_warp([0.0, 1.0, 1.0, 2.0], [0.0, 1.0, 2.0, 3.0], 1.0)
    with pytest.raises(ValueError):
        fit_warp([0.0, 1.0, 2.0], [0.0, 2.0, 1.0], 1.0)
    with pytest.raises(ValueError):
        fit_warp([0.0, 1.0], [0.0, 1.0], 1.0)
    with pytest.raises(ValueError):
        fit_warp([0.0, 1.0, 2.0], [0.0, 1.0, 2.0], -1.0)


# ---------------------------------------------------------------------------
# correspondence
# ---------------------------------------------------------------------------

def _synthetic_pair(warp_fn, *, drop=(), add=()):
    """Two event sequences over the same 'piece', related by ``warp_fn``."""
    rng = np.random.default_rng(7)
    chords = [frozenset(int(x) for x in rng.choice(np.arange(36, 84), size=3,
                                                   replace=False))
              for _ in range(60)]
    times = np.cumsum(rng.uniform(0.3, 0.9, 60))
    times -= times[0]
    a = [Event(float(t), c, len(c), 80) for t, c in zip(times, chords)]
    b = [Event(float(warp_fn(t)), c, len(c), 80)
         for k, (t, c) in enumerate(zip(times, chords)) if k not in drop]
    for t, c in add:
        b.append(Event(float(t), frozenset(c), len(c), 80))
    b.sort(key=lambda e: e.t)
    return a, b


def test_recovers_a_known_warp_end_to_end():
    """The headline claim: put a known homeomorphism in, get it back out."""
    def phi(t):
        return np.where(t < 15.0, 1.4 * t, 21.0 + 0.6 * (t - 15.0))

    a, b = _synthetic_pair(phi)
    corr = correspond(a, b)
    assert len(corr.pairs) == len(a)
    assert all(p.cost == 0.0 for p in corr.pairs)

    t = corr.t - corr.t[0]
    w = fit_warp(t, corr.s - corr.s[0], 1e-6)
    # Exact at every matched onset: as lambda -> 0 the fit interpolates.
    assert np.max(np.abs(w(t) - (phi(t) - phi(0.0)))) < 1e-6
    # The corner of the true warp does not sit on an onset, so P1 on the onset
    # mesh can only place it in the containing gap -- and does.
    gap = float(np.max(np.diff(t)))
    big = w.kinks()[0]
    assert abs(big.t - 15.0) <= gap
    # The corner falls between two onsets, so its slope change is split across
    # the pair either side of it; the rates away from it are exact.
    assert w.slope_at(5.0) == pytest.approx(1.4, abs=1e-6)
    assert w.slope_at(25.0) == pytest.approx(0.6, abs=1e-6)
    probe = np.linspace(0.0, w.T1, 300)
    assert np.max(np.abs(w(probe) - (phi(probe) - phi(0.0)))) < gap


def test_unmatched_events_are_counted_not_discarded():
    """Drops and additions appear in the census; nothing vanishes."""
    a, b = _synthetic_pair(lambda t: 1.1 * t,
                           drop=(20, 21, 22),
                           add=[(9.13, (30, 31)), (9.17, (32, 33))])
    corr = correspond(a, b)
    c = corr.census()
    assert c["dropped_in_core_a"] == 3
    assert c["added_in_core_b"] == 2
    assert c["matched"] == len(a) - 3
    # the accounting closes on both sides
    assert (c["matched"] + c["dropped_in_core_a"]
            + c["head_a"] + c["tail_a"]) == c["events_a"]
    assert (c["matched"] + c["added_in_core_b"]
            + c["head_b"] + c["tail_b"]) == c["events_b"]


def test_unrelated_music_raises_rather_than_returning_a_warp():
    rng = np.random.default_rng(3)
    mk = lambda seed: [
        Event(float(t), frozenset(int(x) for x in
                                  np.random.default_rng(seed + k).choice(
                                      np.arange(36, 84), 3, replace=False)),
              3, 80)
        for k, t in enumerate(np.cumsum(rng.uniform(0.3, 0.9, 40)))]
    with pytest.raises(ValueError):
        correspond(mk(1000), mk(9000))


def test_timing_prior_settles_a_tie_pitch_cannot():
    """The same pitch twice: only timing can say which repeat is which."""
    from fp30x_studio.align.correspond import timing_prior

    a = [Event(0.0, frozenset({60}), 1, 80),
         Event(1.0, frozenset({62}), 1, 80),
         Event(1.1, frozenset({64}), 1, 80),
         Event(2.0, frozenset({65}), 1, 80)]
    b = [Event(0.0, frozenset({60}), 1, 80),
         Event(1.0, frozenset({62}), 1, 80),
         Event(1.1, frozenset({64}), 1, 80),
         Event(1.9, frozenset({64}), 1, 80),
         Event(2.0, frozenset({65}), 1, 80)]
    w = fit_warp([0.0, 1.0, 2.0], [0.0, 1.0, 2.0], 1.0)
    prior = timing_prior(a, b, w, t0=0.0, s0=0.0)
    corr = correspond(a, b, prior=prior)
    pair = {p.ia: p.ib for p in corr.pairs}
    assert pair[2] == 2, "the near-in-time repeat must win the tie"


# ---------------------------------------------------------------------------
# the real pair: Chopin Op. 55 No. 1, two attempts, 2026-08-19
# ---------------------------------------------------------------------------

TAKES = Path.home() / "Music" / "FP-30X Studio" / "takes"
OP55 = TAKES / "2026-08-19-a.fp30"

needs_op55 = pytest.mark.skipif(not OP55.exists(),
                                reason="the 2026-08-19-a take is absent")


@pytest.fixture(scope="module")
def op55():
    segs = {s.index: s for s in load_segments(OP55, min_notes=20)}
    return align_segments(segs[2], segs[3],
                          label_a="seg 2, false start",
                          label_b="seg 3, complete")


@needs_op55
def test_segmentation_finds_the_four_attempts():
    segs = {s.index: s for s in load_segments(OP55, min_notes=20)}
    assert sorted(segs) == [1, 2, 3, 5]
    assert (segs[2].n_notes, len(segs[2].events)) == (130, 73)
    assert (segs[3].n_notes, len(segs[3].events)) == (1401, 923)
    assert segs[2].duration == pytest.approx(46.56, abs=0.01)
    assert segs[3].duration == pytest.approx(406.255, abs=0.01)


@needs_op55
def test_the_matched_core_and_what_falls_outside_it(op55):
    c = op55.correspondence.census()
    assert c["matched"] == 47
    assert c["exact_pairs"] == 37
    # seg 2 begins a bar earlier than seg 3 and then restarts the opening:
    # six events of head and fourteen of tail have no counterpart at all.
    assert c["head_a"] == 6
    assert c["tail_a"] == 14
    assert c["dropped_in_core_a"] == 6
    assert c["added_in_core_b"] == 15
    assert c["match_rate_core_a"] == pytest.approx(47 / 53)
    assert (c["matched"] + c["dropped_in_core_a"]
            + c["head_a"] + c["tail_a"]) == c["events_a"]


@needs_op55
def test_the_fitted_warp_is_a_homeomorphism_the_data_asked_for(op55):
    w = op55.warp
    assert w.slopes.min() > w.min_slope
    assert not w.active, ("the monotonicity constraint should never bind here: "
                          "phi' > 0 came out of the data, not the constraint")
    assert w.kkt_residual < 1e-9
    assert np.all(np.diff(w.values) > 0)


@needs_op55
def test_measured_numbers_of_the_op55_alignment(op55):
    assert op55.lam == pytest.approx(0.631, rel=1e-2)
    assert op55.warp.T1 == pytest.approx(29.92, abs=0.01)
    assert op55.warp.T2 == pytest.approx(31.31, abs=0.01)
    assert op55.residual_rms == pytest.approx(0.0969, abs=5e-4)
    assert op55.null_residual_rms == pytest.approx(0.7236, abs=5e-4)
    assert op55.explained == pytest.approx(0.866, abs=5e-3)
    assert op55.excess_energy == pytest.approx(0.949, abs=5e-3)
    assert op55.warp.rate_rms == pytest.approx(0.178, abs=5e-3)


@needs_op55
def test_the_residual_stays_well_above_the_transport_lattice(op55):
    """5.000 ms is the noise floor; nothing finer may be claimed.

    The residual sitting two orders above it is what says the alignment error
    is expressive difference and not timestamp quantisation.
    """
    from fp30x_studio.align import LATTICE_S

    assert op55.residual_rms > 10 * LATTICE_S
    assert op55.lattice_multiple == pytest.approx(op55.residual_rms / 0.005)


@needs_op55
def test_gcv_is_reported_as_circular_after_the_refinement_pass(op55):
    """The refinement makes GCV inapplicable, and the object says so."""
    assert op55.refined
    assert not op55.gcv_interior
    # ...which is exactly why lambda came from the pitch-only first pass.
    assert len(op55.first_pass.pairs) <= len(op55.correspondence.pairs)
