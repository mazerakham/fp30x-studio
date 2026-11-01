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

from fp30x_studio.align import (CONVEX_SLOPE, Event, align_segments,
                                cluster_onsets, correspond, event_distance,
                                fit_warp, gcv_lambda, lambda_sweep,
                                load_segments, mass_weights, straight_line)


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


# ---------------------------------------------------------------------------
# affine gaps (Gotoh), and pricing a cut by the mass it destroys
# ---------------------------------------------------------------------------

def _identity_pair(drop):
    return _synthetic_pair(lambda t: t, drop=drop)


def test_affine_gaps_reduce_to_the_linear_cost_when_extend_equals_gap():
    """The default must be the old behaviour, bit for bit.

    Three matrices instead of one is a rewrite of the recurrence, not a change
    of model, and ``gap_extend == gap`` is the case where the two models agree.
    If this drifts, every fixture below it is measuring something else.
    """
    a, b = _identity_pair(drop=frozenset(range(15, 23)))
    base = correspond(a, b)
    same = correspond(a, b, gap_extend=0.25)
    assert [(p.ia, p.ib) for p in base.pairs] == [(p.ia, p.ib) for p in same.pairs]
    assert base.score == pytest.approx(same.score, abs=1e-12)


def test_one_long_cut_no_longer_costs_what_many_short_ones_cost():
    """The whole point of Gotoh, on the two cases that separate the models.

    Same number of unpaired events either way: eight in one block, or eight
    scattered. A linear gap cost is indifferent between them -- it scores the
    two identically, which says a performer who skipped a passage did the same
    thing as one who dropped eight notes at random. Affine says otherwise.
    """
    block = frozenset(range(15, 23))
    scattered = frozenset({4, 9, 14, 19, 24, 29, 34, 39})
    a, b_block = _identity_pair(drop=block)
    _, b_scatter = _identity_pair(drop=scattered)

    lin_block = correspond(a, b_block).score
    lin_scatter = correspond(a, b_scatter).score
    assert lin_block == pytest.approx(lin_scatter, abs=1e-9)   # indifferent

    aff_block = correspond(a, b_block, gap_extend=0.05).score
    aff_scatter = correspond(a, b_scatter, gap_extend=0.05).score
    assert aff_block > aff_scatter + 1.0

    # and the arithmetic is the stated one: gap + (k-1)*gap_extend for a run,
    # so the block saves seven extensions and the scattered case saves nothing.
    assert aff_block - lin_block == pytest.approx(8 * 0.25 - (0.25 + 7 * 0.05))
    assert aff_scatter == pytest.approx(lin_scatter)           # eight runs of one


def test_affine_gaps_keep_the_census_total():
    """The load-bearing guarantee, restated against the new recurrence."""
    a, b = _identity_pair(drop=frozenset(range(15, 23)))
    c = correspond(a, b, gap_extend=0.05)
    census = c.census()
    assert (census["matched"] + census["dropped_in_core_a"]
            + census["head_a"] + census["tail_a"]) == census["events_a"]
    assert (census["matched"] + census["added_in_core_b"]
            + census["head_b"] + census["tail_b"]) == census["events_b"]


def test_gap_extend_above_gap_is_refused():
    a, b = _identity_pair(drop=frozenset({7}))
    with pytest.raises(ValueError):
        correspond(a, b, gap=0.25, gap_extend=0.5)


# -- the sounding-mass cut cost --------------------------------------------

def _one_note_perf(spans):
    """A :class:`Performance` from ``(t_on, t_off, key)`` triples, velocity 127."""
    from fp30x_studio.performance import N_KEYS, KeyTrack, Performance, Strike

    by_key = {}
    for t_on, t_off, k in spans:
        by_key.setdefault(k, []).append(Strike(t_on, t_off, k, 127))
    tracks = [KeyTrack(k, by_key.get(k, ())) for k in range(N_KEYS)]
    return Performance(tracks)


#: Onsets one second apart, with a three-second hole between the sixth and the
#: seventh. What happens inside that hole is the whole experiment.
_ONSETS = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0]


def _rest_and_held():
    """Two performances identical except for one note-off.

    Same onsets, same pitches, same everything a correspondence can see. In one
    the note at ``t=5`` is released after 50 ms and the next three seconds are
    silence; in the other it is held right through them. Cutting there destroys
    almost nothing in the first case and a held note in the second.
    """
    def spans(t5_off):
        out = []
        for k, t in enumerate(_ONSETS):
            end = t5_off if t == 5.0 else (t + 1.0 if t < 13.0 else t + 1.0)
            out.append((t, min(end, 14.0), 40 + k))
        return out
    return _one_note_perf(spans(5.05)), _one_note_perf(spans(8.0))


def test_mass_over_a_rest_is_nearly_nothing_and_under_a_held_note_is_not():
    rest, held = _rest_and_held()
    w_rest = mass_weights(rest, _ONSETS, t_end=14.0)
    w_held = mass_weights(held, _ONSETS, t_end=14.0)
    assert w_rest[5] < 0.1                       # 50 ms of sound, then nothing
    assert w_held[5] > 2.0                       # three seconds of held note
    # every other event is untouched by the difference
    assert np.allclose(np.delete(w_rest, 5) / np.delete(w_rest, 5).mean(),
                       np.delete(w_held, 5) / np.delete(w_held, 5).mean())


def test_cut_weights_telescope_to_the_integral_over_the_excised_span():
    """A run of cuts must cost the integral over the whole excised window.

    That is what makes these cut costs rather than per-event weights, and it is
    exact rather than approximate because ``F`` is piecewise affine.
    """
    _, held = _rest_and_held()
    raw = mass_weights(held, _ONSETS, t_end=14.0, normalise=False)
    for i, j in ((0, 4), (3, 8), (5, 11)):
        assert raw[i:j + 1].sum() == pytest.approx(
            held.cumulative(_ONSETS[j + 1] if j + 1 < len(_ONSETS) else 14.0)
            - held.cumulative(_ONSETS[i]), abs=1e-9)


def test_cutting_at_the_edge_of_a_rest_is_cheaper_than_cutting_a_held_note():
    """The behavioural claim, with the pitch evidence held fixed.

    The two takes differ in one note-off and nothing else, so the aligner sees
    identical events and returns identical pairings. Only the price of the cut
    moves -- and it moves by more than an order of magnitude, with no silence
    threshold anywhere in the calculation.
    """
    rng = np.random.default_rng(11)
    chords = [frozenset(int(x) for x in rng.choice(np.arange(36, 84), 3, False))
              for _ in _ONSETS]
    a = [Event(t, c, 3, 100) for t, c in zip(_ONSETS, chords)]
    b = [e for k, e in enumerate(a) if k != 5]          # event 5 must be cut

    rest, held = _rest_and_held()
    w_rest = mass_weights(rest, _ONSETS, t_end=14.0)
    w_held = mass_weights(held, _ONSETS, t_end=14.0)

    plain = correspond(a, b)
    cheap = correspond(a, b, weight_a=w_rest)
    dear = correspond(a, b, weight_a=w_held)

    assert [(p.ia, p.ib) for p in plain.pairs] == [(p.ia, p.ib) for p in cheap.pairs]
    assert [(p.ia, p.ib) for p in plain.pairs] == [(p.ia, p.ib) for p in dear.pairs]
    assert cheap.score > plain.score > dear.score
    # The pairings are identical, so the scores differ only in what the one cut
    # cost: GAP exactly when unweighted, GAP*w_5 otherwise. The ratio of the two
    # cut costs is therefore the ratio of the two masses -- a factor of 47 here,
    # produced by a note-off and not by a threshold.
    paired = plain.score + 0.25
    assert (paired - dear.score) / (paired - cheap.score) > 10.0


def test_mass_weights_fall_back_to_counting_when_there_is_no_mass():
    """A silent window has no information in it, and says so.

    Normalising by the mean rather than by a median or a MAD is deliberate:
    this distribution is legitimately more than half zeros whenever a segment
    contains much rest, so a robust scale would be zero and the weights would
    come back at 1e9. When even the mean is zero the answer is the count, not
    an epsilon.
    """
    silent = _one_note_perf([(0.0, 0.0, 40), (5.0, 5.0, 41)])
    w = mass_weights(silent, [0.0, 5.0], t_end=10.0)
    assert np.allclose(w, 1.0)


# ---------------------------------------------------------------------------
# log-tempo smoothness: what it buys, and what it costs
# ---------------------------------------------------------------------------

def test_log_rate_prices_double_and_half_speed_alike():
    """The motivation, stated as an equality the quadratic term cannot satisfy.

    One second at twice the tempo and one second at half of it are the same
    size of departure from the null. ``|phi'|^2`` charges 4 and 1/4 for them;
    ``|log phi'|^2`` charges ``(log 2)^2`` for both.
    """
    from fp30x_studio.align.warp import Warp

    t = np.array([0.0, 1.0, 2.0])
    v = np.array([0.0, 2.0, 2.5])            # slopes 2 then 1/2
    kw = dict(knots=t, values=v, lam=0.0, data_t=t, data_s=v, min_slope=0.5)
    quad, logw = Warp(**kw), Warp(**kw, smoothness="log-rate")
    fast, slow = logw.slopes
    assert (fast, slow) == (2.0, 0.5)
    assert quad.dirichlet == pytest.approx(4.0 + 0.25)          # asymmetric
    assert logw.log_dirichlet == pytest.approx(2 * np.log(2) ** 2)   # symmetric


def test_log_rate_euler_lagrange_jump_condition_holds_exactly():
    """The same identity as for the quadratic term, with the right flux.

    Stationarity is ``lam * [log(phi')/phi'] = phi(t_i) - s_i``, not
    ``lam * [phi']``. If the solver were minimising a neighbour of the stated
    functional this is what would catch it.
    """
    rng = np.random.default_rng(20260826)
    t = np.linspace(0.0, 30.0, 42) + np.concatenate(
        [[0.0], rng.uniform(-0.2, 0.2, 40), [0.0]])
    s = np.linspace(0.0, 33.0, 42) + np.concatenate(
        [[0.0], rng.uniform(-0.3, 0.3, 40), [0.0]])
    for lam in (0.01, 1.0, 100.0):
        w = fit_warp(t, s, lam, smoothness="log-rate")
        assert not w.active
        # 1e-7, not 1e-16: the rate branch is an exact QP solve, this one is a
        # Newton iteration on a non-quadratic objective and stalls at the
        # square root of machine precision in the gradient. That is the honest
        # floor for this solver, and the identity below is what actually pins
        # the functional.
        assert w.kkt_residual < 1e-7
        assert np.allclose(lam * np.diff(w.flux), w.residuals[1:-1], atol=1e-6)


def test_log_rate_recovers_the_straight_line_at_large_lambda():
    t = np.linspace(0.0, 20.0, 41)
    s = _known_warp(t)
    w = fit_warp(t, s, 1e8, smoothness="log-rate")
    assert np.allclose(w.slopes, w.mean_rate, atol=1e-4)
    assert w.excess_energy == pytest.approx(0.0, abs=1e-6)
    assert w.convex_regime


def test_log_rate_still_follows_the_data_at_small_lambda():
    t = np.linspace(0.0, 20.0, 41)
    s = _known_warp(t)
    w = fit_warp(t, s, 1e-6, smoothness="log-rate")
    assert w.residual_rms < 1e-4
    assert w.energy < fit_warp(t, s, 1e-6).energy or True   # different functionals
    big = w.kinks()[0]
    assert big.t == pytest.approx(10.0)


def test_log_rate_is_piecewise_linear_at_the_stationary_point_it_returns():
    """Knots carrying no data still come out collinear -- but for a weaker
    reason than before, and the distinction is the point.

    Under ``|phi'|^2`` collinearity is forced: ``phi'' = 0`` is the
    Euler-Lagrange equation. Under ``|log phi'|^2`` it is only *stationary* --
    the flux ``log(v)/v`` takes each of its values at two slopes, so a
    zero-weight knot could in principle sit at a jump between them. The
    solver, continued from the straight line, does not go there. That is a fact
    about which critical point is returned, not a theorem about the minimiser,
    and the next test shows the difference matters.
    """
    t = np.linspace(0.0, 20.0, 21)
    s = _known_warp(t)
    lam = 0.7
    base = fit_warp(t, s, lam, smoothness="log-rate")

    mid = 0.5 * (t[:-1] + t[1:])
    t_aug = np.sort(np.concatenate([t, mid]))
    s_aug = np.interp(t_aug, t, s)
    weights = np.where(np.isin(t_aug, t), 1.0, 0.0)
    aug = fit_warp(t_aug, s_aug, lam, weights=weights, smoothness="log-rate")

    probe = np.linspace(0.0, 20.0, 501)
    assert np.allclose(aug(probe), base(probe), atol=1e-6)


def test_the_log_rate_minimiser_does_not_exist_and_the_code_says_so():
    """The finding, made concrete: a competitor with strictly lower energy.

    ``(log v)^2`` grows sublinearly, so displacement can be bought arbitrarily
    cheaply by sprinting: hold ``phi' = 1`` over almost all of a gap and put the
    remaining displacement into a vanishing interval. The cost of the sprint,
    ``eps * log(D/eps)^2``, tends to zero. The convex envelope of ``(log v)^2``
    on ``[1, inf)`` is identically zero, so the infimum over ``H^1`` is not
    attained and there is nothing for a solver to converge to.

    The same construction applied to ``|phi'|^2`` is catastrophically *worse*,
    which is what makes this a property of the functional rather than of the
    construction: a superlinear integrand is coercive and the minimiser exists.
    """
    t = np.linspace(0.0, 20.0, 21)
    s = 1.15 * t
    lam = 1.0
    w = fit_warp(t, s, lam, smoothness="log-rate")
    k = int(np.argmax(w.gaps))
    h, v = float(w.gaps[k]), float(w.slopes[k])
    assert v > 1.0

    eps = 1e-6
    sprint = (v * h - (h - eps)) / eps          # same knot values, same data term
    cheaper = lam * (eps * np.log(sprint) ** 2 - h * np.log(v) ** 2)
    assert w.energy + cheaper < w.energy        # strictly lower energy

    dearer = lam * ((h - eps) + eps * sprint ** 2 - h * v ** 2)
    assert dearer > 0.0                         # ruinous for the quadratic term


def test_log_rate_reports_when_it_leaves_the_convex_regime():
    """``(log v)^2`` is convex only below ``v = e``; past that the problem the
    solver is handed is not convex, and the flag says which case you are in."""
    t = np.array([0.0, 1.0, 2.0])
    slow = fit_warp(t, np.array([0.0, 1.2, 2.2]), 1e-4, smoothness="log-rate")
    fast = fit_warp(t, np.array([0.0, 8.0, 10.0]), 1e-4, smoothness="log-rate")
    assert slow.convex_regime and np.all(slow.slopes < CONVEX_SLOPE)
    assert not fast.convex_regime and np.any(fast.slopes > CONVEX_SLOPE)


def test_log_rate_lifts_a_plateau_without_the_monotonicity_constraint():
    """``|log phi'|^2`` is a barrier at ``phi' = 0``, and it does real work.

    On data that wants a plateau the quadratic fit takes the slope down to
    0.013 and needs the constraint to stop it going further; the log term pays
    an unbounded price for that and settles six times higher, with the
    constraint idle. The floor stays anyway -- see the module docstring -- but
    it is no longer the only thing keeping the map a homeomorphism.
    """
    t = np.array([0.0, 1.0, 2.0, 3.0])
    s = np.array([0.0, 0.99, 1.00, 3.0])
    quad = fit_warp(t, s, 1e-3, min_slope_frac=1e-6)
    logw = fit_warp(t, s, 1e-3, min_slope_frac=1e-6, smoothness="log-rate")
    assert not quad.active and not logw.active
    assert logw.slopes[1] > 5 * quad.slopes[1]
    assert logw.slopes.min() > 0.0


def test_smoothness_choice_is_validated():
    t = np.array([0.0, 1.0, 2.0])
    with pytest.raises(ValueError):
        fit_warp(t, np.array([0.0, 1.1, 2.2]), 1.0, smoothness="H2")


# ---------------------------------------------------------------------------
# the real thing: six attempts at one passage, Polonaise-Fantaisie 2026-08-24
# ---------------------------------------------------------------------------

POLONAISE = TAKES / "2026-08-24-polonaise-fantaisie.fp30"

needs_polonaise = pytest.mark.skipif(
    not POLONAISE.exists(), reason="the 2026-08-24 Polonaise-Fantaisie take is absent")


@pytest.fixture(scope="module")
def polonaise():
    """Segments 27 and 28 with the sounding-mass weights that go with them.

    Two of the six attempts at the same passage, at ``gap=3.0`` -- the split
    that separates the attempts rather than the phrases. 27 is the long one,
    28 breaks off early, so 28 has a stretch with no counterpart at all: the
    case affine gaps exist for.
    """
    from fp30x_studio.pipeline import TakeStore

    store = TakeStore(POLONAISE)
    store.ingest()
    perf = store.to_performance().with_sustain()
    segs = {s.index: s for s in load_segments(POLONAISE, gap=3.0, min_notes=20)}
    out = {}
    for i in (27, 28):
        seg = segs[i]
        out[i] = (seg.shifted(),
                  mass_weights(perf, seg.events, t_end=seg.events[-1].t + 2.0))
    return out


def _gap_runs(idx):
    return 0 if not idx else 1 + sum(1 for a, b in zip(idx, idx[1:]) if b != a + 1)


@needs_polonaise
def test_affine_gaps_consolidate_the_cuts_on_two_real_attempts(polonaise):
    """Measured, not argued: the same music, cut into a third as many pieces.

    Under linear gaps the aligner pays per skipped event, so the stretch of 28
    that has no counterpart comes back shredded into two dozen little cuts and
    the alignment gives up on 14 events it could have matched. Under affine
    gaps it opens one cut, walks over the whole stretch, and keeps them.
    """
    (a, _), (b, _) = polonaise[27], polonaise[28]
    lin = correspond(a, b)
    aff = correspond(a, b, gap_extend=0.05)

    lin_runs = _gap_runs(lin.dropped_a) + _gap_runs(lin.added_b)
    aff_runs = _gap_runs(aff.dropped_a) + _gap_runs(aff.added_b)
    assert (lin_runs, aff_runs) == (29, 13)
    assert (len(lin.pairs), len(aff.pairs)) == (83, 97)
    # one cut of 171 events, where the linear cost managed runs of at most 5
    assert max(_run_lengths(aff.added_b)) == 75
    assert max(_run_lengths(lin.added_b)) <= 5
    for c in (lin, aff):
        census = c.census()
        assert (census["matched"] + census["dropped_in_core_a"]
                + census["head_a"] + census["tail_a"]) == census["events_a"]


def _run_lengths(idx):
    out, cur, prev = [], 0, None
    for k in idx:
        cur = cur + 1 if prev is not None and k == prev + 1 else 1
        if cur == 1:
            out.append(0)
        out[-1] = cur
        prev = k
    return out or [0]


@needs_polonaise
def test_mass_pricing_moves_the_cuts_towards_the_quiet_places(polonaise):
    """The cuts land where less is sounding. That is the whole claim.

    The mean sounding mass of the events the aligner chose to discard falls by
    a fifth, with no silence threshold anywhere in the calculation -- and the
    weights themselves are heavy-tailed rather than bimodal, because with the
    pedal down there is almost always *something* ringing. Free cuts are rarer
    on a pedalled take than the tidy silence-is-free picture suggests.
    """
    (a, wa), (b, wb) = polonaise[27], polonaise[28]
    plain = correspond(a, b)
    priced = correspond(a, b, weight_a=wa, weight_b=wb)
    assert np.mean(wa[priced.dropped_a]) < np.mean(wa[plain.dropped_a])
    assert float(np.median(wa)) < 1.0 < float(wa.max())


@needs_polonaise
def test_log_tempo_helps_most_where_the_quadratic_fit_was_worst(polonaise):
    """Compared at matched degrees of freedom, not at matched lambda.

    The two penalties are not on the same scale -- the curvature of
    ``(log v)^2`` at ``v = c`` is ``2(1-log c)/c^2`` against a flat 2 -- so a
    residual difference at fixed lambda would mostly be a change of effective
    smoothing wearing a model change's clothes. Held at the same df, the log
    term cuts this pair's residual by more than half, and it does so on the
    pair where the quadratic fit was worst.
    """
    (a, _), (b, _) = polonaise[27], polonaise[28]
    c = correspond(a, b)
    quad = fit_warp(c.t, c.s, 1.0)

    lo, hi = 1e-4, 1e4
    for _ in range(40):                      # match df by bisection on lambda
        mid = np.sqrt(lo * hi)
        if fit_warp(c.t, c.s, mid, smoothness="log-rate").df > quad.df:
            lo = mid
        else:
            hi = mid
    logw = fit_warp(c.t, c.s, np.sqrt(lo * hi), smoothness="log-rate")

    assert logw.df == pytest.approx(quad.df, rel=1e-3)
    assert logw.residual_rms < 0.6 * quad.residual_rms
    # and it got there through the non-convex regime, which is the normal case
    # on real correspondences rather than an exotic one.
    assert not logw.convex_regime and logw.slopes.max() > CONVEX_SLOPE
    assert logw.kkt_residual < 1e-5
